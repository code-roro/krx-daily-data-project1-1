"""하루치(시장 1개) 수집 → 저장 공통 로직"""
from . import db as dbm
from .krx import KrxAuthError, KrxError, fetch_daily, is_target, normalize


def fetch_and_store(db, auth_key: str, market: str, bas_dd: str) -> int | None:
    """반환: 저장 행 수 / 0 = 데이터 없음(휴장 또는 미갱신) / None = 오류.
    인증 오류(KrxAuthError)는 즉시 상위로 올려 전체 실행을 멈춘다."""
    try:
        raw = fetch_daily(market, bas_dd, auth_key)
    except KrxAuthError:
        raise
    except KrxError as e:
        dbm.log_fetch(db, bas_dd, market, "error", message=str(e)[:300])
        print(f"  x {bas_dd} {market} 오류: {e}", flush=True)
        return None

    if not raw:
        dbm.log_fetch(db, bas_dd, market, "empty")
        print(f"  - {bas_dd} {market} 데이터 없음", flush=True)
        return 0

    rows = [normalize(r, market, bas_dd) for r in raw if is_target(r)]
    dbm.upsert_prices(db, rows)
    dbm.log_fetch(db, bas_dd, market, "ok", len(rows), len(raw))
    print(f"  o {bas_dd} {market} {len(rows)}종목 저장 (원본 {len(raw)})", flush=True)
    return len(rows)
