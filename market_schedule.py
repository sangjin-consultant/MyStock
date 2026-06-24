"""
한국 주식시장 장 시간 관리
- 항상 KST(Asia/Seoul) 기준으로 판단
- 프리장 / 정규장 / 시간외 세션 판별
- 클라우드(GitHub Actions UTC 서버) 대응
"""

from datetime import datetime, time, date, timedelta
from zoneinfo import ZoneInfo
import time as time_module

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """현재 KST 시각 반환 (UTC 서버에서도 정확)"""
    return datetime.now(KST)


# ─── 세션 정의 (KST) ────────────────────────────────────────

SESSIONS = {
    "nxt_pre":   (time(8,  0),  time(8,  50), "NXT프리마켓"),
    "single":    (time(8,  50), time(9,   0), "단일가매매"),
    "regular":   (time(9,  0),  time(15, 20), "메인마켓"),
    "after":     (time(15, 30), time(20,  0), "NXT애프터마켓"),
}

MARKET_OPEN  = time(8,  0)   # NXT 프리마켓 시작
MARKET_CLOSE = time(20, 0)   # NXT 애프터마켓 종료


def current_session(now: datetime = None) -> tuple[str, str] | tuple[None, None]:
    """현재 세션 반환 (key, 이름). 장외면 (None, None). 항상 KST 기준."""
    now = now_kst() if now is None else now
    if now.weekday() >= 5:  # 토/일
        return None, None
    t = now.time()
    for key, (start, end, label) in SESSIONS.items():
        if start <= t < end:
            return key, label
    return None, None


def is_market_open(now: datetime = None) -> bool:
    key, _ = current_session(now)
    return key is not None


def is_trading_day(d: date = None) -> bool:
    d = d or now_kst().date()
    return d.weekday() < 5


def seconds_until_open(now: datetime = None) -> float:
    """장 시작까지 남은 초 (KST 기준). 이미 열렸으면 0."""
    now = now_kst() if now is None else now
    if is_market_open(now):
        return 0.0

    today_open = datetime.combine(now.date(), MARKET_OPEN, tzinfo=KST)
    if now < today_open and is_trading_day(now.date()):
        return (today_open - now).total_seconds()

    next_day = now.date() + timedelta(days=1)
    while not is_trading_day(next_day):
        next_day += timedelta(days=1)
    return (datetime.combine(next_day, MARKET_OPEN, tzinfo=KST) - now).total_seconds()


def seconds_until_close(now: datetime = None) -> float:
    """장 마감까지 남은 초 (KST 기준). 이미 닫혔으면 0."""
    now = now_kst() if now is None else now
    today_close = datetime.combine(now.date(), MARKET_CLOSE, tzinfo=KST)
    if now >= today_close:
        return 0.0
    return (today_close - now).total_seconds()


def wait_for_market_open(log_fn=print):
    """장이 열릴 때까지 대기. 이미 열려있으면 즉시 반환."""
    secs = seconds_until_open()
    if secs <= 0:
        return
    open_time = now_kst() + timedelta(seconds=secs)
    log_fn(f"장 시작 대기 중... {open_time.strftime('%m/%d %H:%M')} KST 에 시작합니다.")
    while True:
        secs = seconds_until_open()
        if secs <= 0:
            break
        time_module.sleep(min(secs, 60))


def session_label(now: datetime = None) -> str:
    _, label = current_session(now)
    return label or "장외"
