"""설정·환경변수 로딩.

인증키 등 민감정보는 코드에 넣지 않고 환경변수로만 읽는다.
- GitHub Actions: 저장소 Settings > Secrets and variables > Actions 에 등록
- 로컬 테스트: 저장소 루트에 .env 파일 (깃에는 올라가지 않음)
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, "").strip()
    if not val:
        if required:
            raise RuntimeError(
                f"환경변수 {name} 이(가) 없습니다. GitHub Secrets 또는 .env 를 확인하세요."
            )
        return default
    return val


# KRX Open API
KRX_BASE_URL = "https://data-dbg.krx.co.kr/svc/apis/sto"
MARKETS = {
    "KOSPI": "stk_bydd_trd",   # 유가증권 일별매매정보
    "KOSDAQ": "ksq_bydd_trd",  # 코스닥 일별매매정보
}
KRX_CALL_INTERVAL = float(env("KRX_CALL_INTERVAL", "0.5"))  # 호출 간 대기(초)

# 보관 기간(거래일 수)
RETENTION_DAYS = int(env("RETENTION_DAYS", "200"))

# 실행 결과 파일 (워크플로가 저장소에 커밋 → Claude 등 외부에서 확인용)
STATUS_FILE = ROOT / "status" / "latest.json"
