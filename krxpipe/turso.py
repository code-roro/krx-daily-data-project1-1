"""Turso(libSQL) HTTP 클라이언트.

별도 드라이버 없이 Turso의 HTTP API(/v2/pipeline)를 requests 로 직접 호출한다.
(GitHub Actions 환경에서 네이티브 드라이버 설치 문제를 피하기 위함)
"""
import time

import requests

from .config import env


class TursoError(Exception):
    pass


def _encode(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def _decode(cell):
    t = cell.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(cell["value"])
    if t == "float":
        return float(cell["value"])
    return cell.get("value")


class Turso:
    def __init__(self, url: str, token: str, timeout: int = 60):
        url = url.strip()
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        self.endpoint = url.rstrip("/") + "/v2/pipeline"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })

    @classmethod
    def from_env(cls) -> "Turso":
        return cls(env("TURSO_DATABASE_URL", required=True),
                   env("TURSO_AUTH_TOKEN", required=True))

    def pipeline(self, stmts: list[tuple[str, list]], retries: int = 3) -> list[dict]:
        """여러 SQL 을 한 번의 HTTP 요청으로 실행.
        반환: [{"columns": [...], "rows": [dict, ...], "affected": int}, ...]"""
        body = {"requests": [
            {"type": "execute", "stmt": {"sql": sql, "args": [_encode(a) for a in (args or [])]}}
            for sql, args in stmts
        ] + [{"type": "close"}]}

        last_err = None
        for attempt in range(1, retries + 1):
            try:
                r = self.session.post(self.endpoint, json=body, timeout=self.timeout)
                if r.status_code in (401, 403):
                    raise TursoError(f"Turso 인증 실패(HTTP {r.status_code}) - 토큰을 확인하세요")
                r.raise_for_status()
                data = r.json()
                break
            except TursoError:
                raise
            except (requests.RequestException, ValueError) as e:
                last_err = e
                time.sleep(3 * attempt)
        else:
            raise TursoError(f"Turso 요청 {retries}회 실패: {last_err}")

        out = []
        for res in data["results"][: len(stmts)]:
            if res.get("type") == "error":
                raise TursoError(res.get("error", {}).get("message", str(res)))
            result = res["response"]["result"]
            cols = [c.get("name") for c in result.get("cols", [])]
            rows = [dict(zip(cols, (_decode(c) for c in row))) for row in result.get("rows", [])]
            out.append({"columns": cols, "rows": rows,
                        "affected": result.get("affected_row_count", 0)})
        return out

    def execute(self, sql: str, args: list | None = None) -> dict:
        return self.pipeline([(sql, args or [])])[0]

    def query(self, sql: str, args: list | None = None) -> list[dict]:
        return self.execute(sql, args)["rows"]
