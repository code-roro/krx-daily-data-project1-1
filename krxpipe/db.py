"""스키마 및 DB 작업"""
import json

from .calendar_kr import now_kst

PRICE_COLS = [
    "bas_dd", "isu_cd", "isu_nm", "market", "sect_tp_nm",
    "open_prc", "high_prc", "low_prc", "close_prc", "chg", "fluc_rt",
    "volume", "trd_value", "mktcap", "list_shrs",
]

SCHEMA = [
    # 일별 시세 (기준일자 + 종목코드가 기본키 → 같은 날짜를 다시 받아도 덮어쓰기만 됨)
    """CREATE TABLE IF NOT EXISTS daily_price (
        bas_dd     TEXT NOT NULL,   -- 기준일자 YYYYMMDD
        isu_cd     TEXT NOT NULL,   -- 종목코드(단축)
        isu_nm     TEXT,            -- 종목명
        market     TEXT,            -- KOSPI / KOSDAQ
        sect_tp_nm TEXT,            -- 소속부
        open_prc   INTEGER,         -- 시가
        high_prc   INTEGER,         -- 고가
        low_prc    INTEGER,         -- 저가
        close_prc  INTEGER,         -- 종가
        chg        INTEGER,         -- 전일대비
        fluc_rt    REAL,            -- 등락률(%)
        volume     INTEGER,         -- 거래량
        trd_value  INTEGER,         -- 거래대금
        mktcap     INTEGER,         -- 시가총액
        list_shrs  INTEGER,         -- 상장주식수
        PRIMARY KEY (bas_dd, isu_cd)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_price_isu ON daily_price(isu_cd, bas_dd)",
    # 날짜·시장별 수집 기록 (이어받기/재시도 판단용)
    """CREATE TABLE IF NOT EXISTS fetch_log (
        bas_dd     TEXT NOT NULL,
        market     TEXT NOT NULL,
        status     TEXT NOT NULL,   -- ok / empty(휴장 또는 미갱신) / error
        row_count  INTEGER,         -- 저장한 종목 수(우선주·스팩 제외 후)
        raw_count  INTEGER,         -- API 원본 종목 수
        message    TEXT,
        checked_at TEXT,
        PRIMARY KEY (bas_dd, market)
    )""",
    # 실행 이력 (성공/실패 판단용)
    """CREATE TABLE IF NOT EXISTS pipeline_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        mode         TEXT,          -- init / daily
        started_at   TEXT,
        finished_at  TEXT,
        status       TEXT,          -- running / success / partial / no_data / failed
        target_date  TEXT,
        dates_loaded INTEGER,
        rows_written INTEGER,
        message      TEXT
    )""",
    # 상장주식수 변동 감지 (액면분할·병합·증자 등 → 가격 불연속 가능성 표시)
    """CREATE VIEW IF NOT EXISTS v_share_changes AS
       SELECT bas_dd, isu_cd, isu_nm, market, prev_shrs, list_shrs,
              ROUND(CAST(list_shrs AS REAL) / prev_shrs, 4) AS ratio
       FROM (
           SELECT bas_dd, isu_cd, isu_nm, market, list_shrs,
                  LAG(list_shrs) OVER (PARTITION BY isu_cd ORDER BY bas_dd) AS prev_shrs
           FROM daily_price
       )
       WHERE prev_shrs IS NOT NULL AND prev_shrs > 0 AND list_shrs <> prev_shrs""",
    # 최신 거래일 전 종목 (시총 순위 포함)
    """CREATE VIEW IF NOT EXISTS v_latest AS
       SELECT p.*, RANK() OVER (ORDER BY p.mktcap DESC) AS mktcap_rank
       FROM daily_price p
       WHERE p.bas_dd = (SELECT MAX(bas_dd) FROM fetch_log WHERE status = 'ok')""",
    # 데이터 상태 한눈에 보기
    """CREATE VIEW IF NOT EXISTS v_data_status AS
       SELECT
         (SELECT MAX(bas_dd) FROM fetch_log WHERE status = 'ok')            AS latest_date,
         (SELECT COUNT(DISTINCT bas_dd) FROM fetch_log WHERE status = 'ok') AS stored_days,
         (SELECT status      FROM pipeline_runs ORDER BY id DESC LIMIT 1)   AS last_run_status,
         (SELECT finished_at FROM pipeline_runs ORDER BY id DESC LIMIT 1)   AS last_run_at,
         (SELECT message     FROM pipeline_runs ORDER BY id DESC LIMIT 1)   AS last_run_message""",
]


def ts() -> str:
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


def ensure_schema(db) -> None:
    db.pipeline([(s, []) for s in SCHEMA])


def upsert_prices(db, rows: list[dict], rows_per_stmt: int = 100, stmts_per_request: int = 8) -> int:
    """여러 행을 묶어서 INSERT OR REPLACE (행 100개 × 문장 8개 = 요청당 800행)"""
    if not rows:
        return 0
    stmts = []
    one = "(" + ",".join("?" * len(PRICE_COLS)) + ")"
    for i in range(0, len(rows), rows_per_stmt):
        chunk = rows[i:i + rows_per_stmt]
        sql = (f"INSERT OR REPLACE INTO daily_price ({','.join(PRICE_COLS)}) VALUES "
               + ",".join([one] * len(chunk)))
        args = [r[c] for r in chunk for c in PRICE_COLS]
        stmts.append((sql, args))
    for i in range(0, len(stmts), stmts_per_request):
        db.pipeline(stmts[i:i + stmts_per_request])
    return len(rows)


def log_fetch(db, bas_dd, market, status, row_count=0, raw_count=0, message=None) -> None:
    db.execute(
        "INSERT OR REPLACE INTO fetch_log (bas_dd, market, status, row_count, raw_count, message, checked_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [bas_dd, market, status, row_count, raw_count, message, ts()],
    )


def load_fetch_log(db) -> dict:
    rows = db.query("SELECT bas_dd, market, status, row_count FROM fetch_log")
    return {(r["bas_dd"], r["market"]): r for r in rows}


def latest_ok_date(db) -> str | None:
    rows = db.query("SELECT MAX(bas_dd) AS d FROM fetch_log WHERE status = 'ok'")
    return rows[0]["d"] if rows else None


def apply_retention(db, keep_days: int) -> str | None:
    """최근 keep_days 거래일만 남기고 이전 데이터 삭제. 반환: 남은 가장 오래된 날짜"""
    rows = db.query(
        "SELECT bas_dd FROM fetch_log WHERE status = 'ok' AND row_count > 0 "
        "GROUP BY bas_dd ORDER BY bas_dd DESC LIMIT 1 OFFSET ?",
        [keep_days - 1],
    )
    if not rows:
        return None
    cutoff = rows[0]["bas_dd"]
    res = db.pipeline([
        ("DELETE FROM daily_price WHERE bas_dd < ?", [cutoff]),
        ("DELETE FROM fetch_log WHERE bas_dd < ?", [cutoff]),
    ])
    if res[0]["affected"]:
        print(f"보관기간 정리: {cutoff} 이전 {res[0]['affected']}행 삭제", flush=True)
    return cutoff


def start_run(db, mode: str) -> int:
    db.execute("INSERT INTO pipeline_runs (mode, started_at, status) VALUES (?,?,?)",
               [mode, ts(), "running"])
    return db.query("SELECT MAX(id) AS id FROM pipeline_runs")[0]["id"]


def finish_run(db, run_id, status, target_date=None, dates_loaded=0, rows_written=0, message=None):
    db.execute(
        "UPDATE pipeline_runs SET finished_at=?, status=?, target_date=?, dates_loaded=?, "
        "rows_written=?, message=? WHERE id=?",
        [ts(), status, target_date, dates_loaded, rows_written, message, run_id],
    )


def data_status(db) -> dict:
    rows = db.query("SELECT latest_date, stored_days FROM v_data_status")
    return rows[0] if rows else {}


def write_status_file(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
