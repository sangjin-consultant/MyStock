"""
한국 주식시장 장 시간 관리
- 프리장 / 정규장 / 시간외 세션 판별
- 공휴일 처리 (KRX 휴장일)
- 클라우드 자동 시작/종료 타이밍 계산
"""

from datetime import datetime, time, date, timedelta
import time as time_module


# ─── 세션 정의 (KST) ────────────────────────────────────────

SESSIONS = {
    "pre":       (time(8,  0), time(9,  0),  "프리장"),
    "regular":   (time(9,  0), time(15, 30), "정규장"),
    "after1":    (time(15, 40), time(16, 0), "시간외종가"),
    "after2":    (time(16,  0), time(18, 0), "시간외단일가"),
}

MARKET_OPEN  = time(8,  0)   # 프리장 시작
MARKET_CLOSE = time(18, 0)   # 시간외 종료


def current_session(now: datetime = None) -> tuple[str, str] | tuple[None, None]:
    """현재 세션 반환 (key, 이름). 장외면 (None, None)"""
    now = now or datetime.now()
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
    d = d or date.today()
    return d.weekday() < 5  # 평일 (공휴일 제외는 별도 처리)


def seconds_until_open(now: datetime = None) -> float:
    """장 시작까지 남은 초. 이미 열렸으면 0."""
    now = now or datetime.now()
    if is_market_open(now):
        return 0.0

    # 오늘 08:00
    today_open = datetime.combine(now.date(), MARKET_OPEN)

    if now < today_open and is_trading_day(now.date()):
        return (today_open - now).total_seconds()

    # 다음 평일 08:00
    next_day = now.date() + timedelta(days=1)
    while not is_trading_day(next_day):
        next_day += timedelta(days=1)
    return (datetime.combine(next_day, MARKET_OPEN) - now).total_seconds()


def seconds_until_close(now: datetime = None) -> float:
    """장 마감까지 남은 초. 이미 닫혔으면 0."""
    now = now or datetime.now()
    today_close = datetime.combine(now.date(), MARKET_CLOSE)
    if now >= today_close:
        return 0.0
    return (today_close - now).total_seconds()


def wait_for_market_open(log_fn=print):
    """장이 열릴 때까지 대기. 이미 열려있으면 즉시 반환."""
    secs = seconds_until_open()
    if secs <= 0:
        return
    open_time = datetime.now() + timedelta(seconds=secs)
    log_fn(f"장 시작 대기 중... {open_time.strftime('%m/%d %H:%M')} 에 시작합니다.")
    # 1분 단위로 확인 (sleep 걸어두기)
    while True:
        secs = seconds_until_open()
        if secs <= 0:
            break
        time_module.sleep(min(secs, 60))


def session_label(now: datetime = None) -> str:
    _, label = current_session(now)
    return label or "장외"
