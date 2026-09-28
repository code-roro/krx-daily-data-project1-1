"""날짜 유틸 (모든 날짜는 한국시간 기준)"""
from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays

KST = ZoneInfo("Asia/Seoul")

# 공휴일은 아니지만 KRX가 쉬는 날 (근로자의날, 연말 휴장일)
_KRX_EXTRA_CLOSED = {(5, 1), (12, 31)}


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


def fmt(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


@lru_cache(maxsize=None)
def _kr_holidays(year: int):
    return holidays.KR(years=year)


def is_expected_trading_day(d: date) -> bool:
    """달력상 거래일로 예상되는 날인지 (재시도 여부 판단용 '힌트'일 뿐,
    실제 거래일 여부는 KRX 응답으로 최종 판단한다)"""
    if is_weekend(d) or (d.month, d.day) in _KRX_EXTRA_CLOSED:
        return False
    return d not in _kr_holidays(d.year)


def weekdays_between(start: date, end: date) -> list[date]:
    """start~end(포함) 사이의 평일 목록"""
    out, d = [], start
    while d <= end:
        if not is_weekend(d):
            out.append(d)
        d += timedelta(days=1)
    return out
