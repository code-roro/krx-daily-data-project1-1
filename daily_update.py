"""매일 실행: 마지막 저장일 다음 날 ~ 어제까지 빠진 거래일을 채우고, 200거래일만 유지.

- 이미 최신이면 아무것도 안 하고 끝남 (cron-job.org + 백업 스케줄 중복 실행 안전)
- KRX 전일 데이터가 아직 안 올라왔으면 --retry-interval 간격으로 --deadline 까지 재시도
- 실행이 며칠 빠져도 다음 실행 때 빈 날짜를 모두 채움

사용: python daily_update.py [--deadline 08:35] [--retry-interval 180]
"""
import argparse
import sys
import time
from datetime import datetime, timedelta

from krxpipe import db as dbm
from krxpipe.calendar_kr import (KST, fmt, is_expected_trading_day, now_kst, parse,
                                 today_kst, weekdays_between)
from krxpipe.config import MARKETS, RETENTION_DAYS, STATUS_FILE, env
from krxpipe.krx import KrxAuthError
from krxpipe.loader import fetch_and_store
from krxpipe.turso import Turso

MAX_GAP_WEEKDAYS = 60  # 이보다 오래 멈춰 있었다면 최근 60평일만 채움


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deadline", default=env("DAILY_DEADLINE", "08:35"),
                    help="재시도 마감 시각(KST, HH:MM)")
    ap.add_argument("--retry-interval", type=int, default=int(env("RETRY_INTERVAL_SEC", "180")))
    args = ap.parse_args()

    auth_key = env("KRX_AUTH_KEY", required=True)
    db = Turso.from_env()
    dbm.ensure_schema(db)
    run_id = dbm.start_run(db, "daily")

    today = today_kst()
    hh, mm = map(int, args.deadline.split(":"))
    deadline = datetime(today.year, today.month, today.day, hh, mm, tzinfo=KST)

    status, msg, target_s = "failed", "", None
    rows_total = dates_loaded = attempts = 0
    try:
        latest = dbm.latest_ok_date(db)
        if not latest:
            raise RuntimeError("저장된 데이터가 없습니다. init_load 를 먼저 실행하세요.")

        candidates = weekdays_between(parse(latest) + timedelta(days=1), today - timedelta(days=1))
        if len(candidates) > MAX_GAP_WEEKDAYS:
            candidates = candidates[-MAX_GAP_WEEKDAYS:]
        expected = [d for d in candidates if is_expected_trading_day(d)]
        target = expected[-1] if expected else None
        target_s = fmt(target) if target else None
        print(f"마지막 저장일 {latest} / 확인 대상 평일 {len(candidates)}일 / "
              f"목표 거래일 {target_s or '없음(휴장)'}", flush=True)

        had_error = False
        while True:
            attempts += 1
            known = dbm.load_fetch_log(db)
            for d in candidates:
                ds = fmt(d)
                for market in MARKETS:
                    st = known.get((ds, market))
                    if st and st["status"] == "ok":
                        continue
                    n = fetch_and_store(db, auth_key, market, ds)
                    if n is None:
                        had_error = True
                    elif n > 0:
                        rows_total += n

            known = dbm.load_fetch_log(db)
            pending = [m for m in MARKETS
                       if target_s and known.get((target_s, m), {}).get("status") != "ok"]
            if not pending:
                break
            if now_kst() + timedelta(seconds=args.retry_interval) > deadline:
                break
            print(f"{target_s} {','.join(pending)} 미갱신 → {args.retry_interval}초 후 재시도 "
                  f"({attempts}회차, 마감 {args.deadline})", flush=True)
            time.sleep(args.retry_interval)

        known = dbm.load_fetch_log(db)
        dates_loaded = len({ds for (ds, m), r in known.items()
                            if r["status"] == "ok" and ds in {fmt(c) for c in candidates}})

        if not candidates:
            status, msg = "success", f"이미 최신 상태({latest})"
        elif not target_s:
            status, msg = "success", "새로 적재할 거래일 없음(휴장일)"
        elif not pending:
            status, msg = "success", f"{target_s} 적재 완료"
        elif had_error:
            status, msg = "failed", f"{target_s} {','.join(pending)} 수집 오류 (fetch_log 참고)"
        else:
            status, msg = "no_data", (f"{target_s} {','.join(pending)} 데이터 없음 - "
                                      f"KRX 미갱신 또는 달력에 없는 휴장일")

        dbm.apply_retention(db, RETENTION_DAYS)
    except KrxAuthError as e:
        status, msg = "failed", f"KRX 인증 오류: {e}"
    except Exception as e:  # noqa: BLE001
        status, msg = "failed", f"{type(e).__name__}: {e}"

    dbm.finish_run(db, run_id, status, target_s, dates_loaded, rows_total, msg)
    info = dbm.data_status(db)
    dbm.write_status_file(STATUS_FILE, {
        "mode": "daily", "status": status, "message": msg,
        "target_date": target_s,
        "latest_date": info.get("latest_date"), "stored_days": info.get("stored_days"),
        "rows_written": rows_total, "attempts": attempts,
        "finished_at_kst": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    })
    print(f"[{status}] {msg}", flush=True)
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
