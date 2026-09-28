"""초기 적재: 어제부터 거꾸로 거슬러 올라가며 최근 N거래일(기본 200일)을 채운다.

- 날짜 1개 호출 = 그날 전 종목의 모든 필드가 한 번에 옴 (시장당 1회)
- 이미 받은 날짜는 건너뛰므로, 중간에 끊겨도 다시 실행하면 이어서 받음
- --max-calls 로 한 번에 호출할 수를 제한해 며칠에 나눠 받을 수도 있음

사용: python init_load.py [--days 200] [--max-calls 600]
"""
import argparse
import sys
from datetime import timedelta

from krxpipe import db as dbm
from krxpipe.calendar_kr import fmt, is_weekend, now_kst, today_kst
from krxpipe.config import MARKETS, RETENTION_DAYS, STATUS_FILE, env
from krxpipe.krx import KrxAuthError
from krxpipe.loader import fetch_and_store
from krxpipe.turso import Turso

MAX_LOOKBACK_DAYS = 420      # 200거래일 ≈ 달력 290일. 넉넉히 안전장치
MAX_CONSECUTIVE_ERRORS = 5   # 연속 오류 시 API 거부로 보고 중단
RECENT_EMPTY_RECHECK = 7     # 최근 7일 이내 'empty'는 미갱신일 수 있어 재확인


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=RETENTION_DAYS)
    ap.add_argument("--max-calls", type=int, default=int(env("INIT_MAX_CALLS", "600")))
    args = ap.parse_args()

    auth_key = env("KRX_AUTH_KEY", required=True)
    db = Turso.from_env()
    dbm.ensure_schema(db)
    run_id = dbm.start_run(db, "init")

    today = today_kst()
    known = dbm.load_fetch_log(db)
    d = today - timedelta(days=1)
    stop = today - timedelta(days=MAX_LOOKBACK_DAYS)

    trading_days = calls = rows_total = dates_loaded = consec_err = 0
    had_error = hit_limit = False
    status, msg = "failed", ""

    print(f"초기 적재 시작: 목표 {args.days}거래일, 호출 한도 {args.max_calls}회", flush=True)
    try:
        while trading_days < args.days and d >= stop:
            if is_weekend(d):
                d -= timedelta(days=1)
                continue
            ds = fmt(d)
            day_has_data = False
            for market in MARKETS:
                st = known.get((ds, market))
                if st and st["status"] == "ok":
                    day_has_data |= (st["row_count"] or 0) > 0
                    continue
                if st and st["status"] == "empty" and (today - d).days > RECENT_EMPTY_RECHECK:
                    continue  # 과거 휴장일로 확인된 날
                if calls >= args.max_calls:
                    hit_limit = True
                    break
                n = fetch_and_store(db, auth_key, market, ds)
                calls += 1
                if n is None:
                    had_error = True
                    consec_err += 1
                    if consec_err >= MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError(f"연속 {consec_err}회 오류 → API 거부 가능성, 중단")
                else:
                    consec_err = 0
                    if n > 0:
                        day_has_data = True
                        rows_total += n
            if hit_limit:
                break
            if day_has_data:
                trading_days += 1
                dates_loaded += 1
            d -= timedelta(days=1)

        if trading_days >= args.days and not had_error:
            dbm.apply_retention(db, args.days)
            status, msg = "success", f"{trading_days}거래일 적재 완료"
        else:
            status = "partial"
            reason = "호출 한도 도달" if hit_limit else ("일부 날짜 오류" if had_error else "기간 부족")
            msg = f"{trading_days}/{args.days}거래일 확보 ({reason}) - 다시 실행하면 이어서 받음"
    except KrxAuthError as e:
        status, msg = "failed", f"KRX 인증 오류: {e}"
    except Exception as e:  # noqa: BLE001
        status, msg = "failed", f"{type(e).__name__}: {e}"

    dbm.finish_run(db, run_id, status, None, dates_loaded, rows_total, msg)
    info = dbm.data_status(db)
    dbm.write_status_file(STATUS_FILE, {
        "mode": "init", "status": status, "message": msg,
        "latest_date": info.get("latest_date"), "stored_days": info.get("stored_days"),
        "api_calls": calls, "rows_written": rows_total,
        "finished_at_kst": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    })
    print(f"[{status}] {msg} / API 호출 {calls}회, 저장 {rows_total}행", flush=True)
    return 0 if status in ("success", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
