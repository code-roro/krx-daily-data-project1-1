"""KRX Open API 호출 및 응답 정규화"""
import time

import requests

from .config import KRX_BASE_URL, KRX_CALL_INTERVAL, MARKETS


class KrxError(Exception):
    """일시적 오류 (다음 실행에서 재시도 가능)"""


class KrxAuthError(KrxError):
    """인증키/서비스 승인 문제 (재시도해도 안 됨)"""


def fetch_daily(market: str, bas_dd: str, auth_key: str,
                retries: int = 3, timeout: int = 30) -> list[dict]:
    """기준일자 하루치 전 종목 매매정보. 휴장일이면 빈 리스트."""
    url = f"{KRX_BASE_URL}/{MARKETS[market]}"
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params={"basDd": bas_dd},
                             headers={"AUTH_KEY": auth_key}, timeout=timeout)
            if r.status_code in (401, 403):
                raise KrxAuthError(f"{market} 인증 실패(HTTP {r.status_code}): {r.text[:200]}")
            r.raise_for_status()
            data = r.json()
            if "OutBlock_1" not in data:
                # 인증 오류가 200 으로 오는 경우 대비
                msg = str(data)[:200]
                if "auth" in msg.lower() or "401" in msg or "403" in msg:
                    raise KrxAuthError(f"{market} 인증 오류: {msg}")
                raise KrxError(f"{market} 예상치 못한 응답: {msg}")
            time.sleep(KRX_CALL_INTERVAL)
            return data["OutBlock_1"] or []
        except KrxAuthError:
            raise
        except (requests.RequestException, ValueError, KrxError) as e:
            last_err = e
            wait = 5 * attempt
            print(f"  ! {market} {bas_dd} 호출 실패({attempt}/{retries}): {e} → {wait}초 후 재시도",
                  flush=True)
            time.sleep(wait)
    raise KrxError(f"{market} {bas_dd} 호출 {retries}회 실패: {last_err}")


def _int(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def _float(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def short_code(isu_cd: str) -> str:
    """표준코드(KR7005930003)가 오면 단축코드(005930)로 변환"""
    isu_cd = (isu_cd or "").strip()
    if len(isu_cd) == 12 and isu_cd.startswith("KR"):
        return isu_cd[3:9]
    return isu_cd


def is_target(row: dict) -> bool:
    """우선주·스팩 제외 (리츠는 포함).
    소속부(SECT_TP_NM)는 저장하지 않지만 스팩 판별에만 참고한다."""
    code = short_code(row.get("ISU_CD", ""))
    name = row.get("ISU_NM", "") or ""
    sect = (row.get("SECT_TP_NM", "") or "").upper()
    if not code:
        return False
    if code[-1] != "0":          # 종목코드 끝자리가 0이 아니면 우선주(5·7·9·K 등)
        return False
    if "스팩" in name or "인수목적" in name or "SPAC" in sect:
        return False
    return True


def normalize(row: dict, market: str, bas_dd: str) -> dict:
    return {
        "bas_dd": bas_dd,
        "isu_cd": short_code(row.get("ISU_CD", "")),
        "isu_nm": (row.get("ISU_NM") or "").strip(),
        "market": market,
        "open_prc": _int(row.get("TDD_OPNPRC")),
        "high_prc": _int(row.get("TDD_HGPRC")),
        "low_prc": _int(row.get("TDD_LWPRC")),
        "close_prc": _int(row.get("TDD_CLSPRC")),
        "chg": _int(row.get("CMPPREVDD_PRC")),
        "fluc_rt": _float(row.get("FLUC_RT")),
        "volume": _int(row.get("ACC_TRDVOL")),
        "trd_value": _int(row.get("ACC_TRDVAL")),
        "mktcap": _int(row.get("MKTCAP")),
        "list_shrs": _int(row.get("LIST_SHRS")),
    }
