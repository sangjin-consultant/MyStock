"""
한국투자증권 API 기반 주식 모니터링 에이전트
- 프리장(08:00) ~ 시간외(18:00) 자동 실행/종료
- 클라우드(Linux) + 로컬(Windows) 겸용
- 카카오톡 알람 중심 설계
"""

import json
import time
import threading
import signal
import sys
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ─── 플랫폼 대응 ────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.system("chcp 65001 > nul 2>&1")

# ─── 로깅 설정 ──────────────────────────────────────────────
LOG_PATH = Path(__file__).parent / "monitor.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("monitor")

# ─── Rich (터미널 표시용, 없어도 동작) ──────────────────────
try:
    from rich.console import Console, Group
    from rich.table import Table
    from rich.live import Live
    from rich.panel import Panel
    from rich import box
    console = Console()
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    console = None

# ─── Windows 데스크탑 알림 (선택) ───────────────────────────
def _desktop_notify(title: str, message: str):
    if not IS_WINDOWS:
        return
    try:
        from plyer import notification
        notification.notify(title=title, message=message, app_name="주식 모니터", timeout=10)
    except Exception:
        pass

from kis_api import KISClient
from kakao_notify import send_kakao
from market_schedule import (
    is_market_open, seconds_until_open, seconds_until_close,
    session_label, wait_for_market_open, MARKET_OPEN, MARKET_CLOSE,
)

CONFIG_PATH       = Path(__file__).parent / "config.json"
CLOSE_BETTING_PATH = Path(__file__).parent / "close_betting.json"
ALERT_LOG_PATH    = Path(__file__).parent / "alerts.log"


# ─── 상태 관리 ──────────────────────────────────────────────

@dataclass
class AlertState:
    last_alert_time: dict  = field(default_factory=dict)
    previous_prices: dict  = field(default_factory=dict)
    previous_volumes: dict = field(default_factory=dict)
    price_history: dict    = field(default_factory=dict)  # {ticker: [(datetime, price)]}


def record_price(state: AlertState, ticker: str, price: int):
    now = datetime.now()
    history = state.price_history.setdefault(ticker, [])
    history.append((now, price))
    cutoff = now - timedelta(hours=2)
    state.price_history[ticker] = [(t, p) for t, p in history if t >= cutoff]


def get_past_price(state: AlertState, ticker: str, minutes_ago: int) -> Optional[int]:
    history = state.price_history.get(ticker, [])
    target  = datetime.now() - timedelta(minutes=minutes_ago)
    past    = [(t, p) for t, p in history if t <= target]
    return past[-1][1] if past else None


def pct_change(current: int, past: Optional[int]) -> Optional[float]:
    if past is None or past == 0:
        return None
    return (current - past) / past * 100


# ─── 설정 ───────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ─── 알람 ───────────────────────────────────────────────────

def log_alert(message: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(ALERT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {message}\n")
    log.info(message)


def can_alert(state: AlertState, key: str, cooldown_min: int) -> bool:
    last = state.last_alert_time.get(key)
    return last is None or (datetime.now() - last) > timedelta(minutes=cooldown_min)


def fire_alert(state: AlertState, key: str, title: str, body: str, cooldown_min: int):
    if not can_alert(state, key, cooldown_min):
        return
    state.last_alert_time[key] = datetime.now()
    full = f"[{title}] {body}"
    log_alert(full)
    _desktop_notify(f"🔔 {title}", body)
    send_kakao(f"🔔 {title}", body)


def fmt_investor_kakao(inv: dict) -> str:
    """투자자 동향 카카오 메시지용 1줄 요약"""
    if not inv:
        return ""
    lines = []
    for key, label in [("foreign", "외국인"), ("institute", "기관"), ("individual", "개인")]:
        d = inv.get(key, {})
        net_qty = d.get("net_qty", 0) or 0
        net_amt = d.get("net_amt", 0) or 0   # 백만원
        if net_qty == 0:
            continue
        arrow  = "▲" if net_qty > 0 else "▼"
        val    = abs(net_qty)
        amt_uk = net_amt / 100               # 억원
        qty_s  = f"{val/1_000_000:.1f}M주" if val >= 1_000_000 else \
                 f"{val/1_000:.0f}K주"      if val >= 1_000     else f"{val}주"
        lines.append(f"  {label} {arrow}{qty_s} ({amt_uk:+.0f}억)")
    return "\n".join(lines)


def build_kakao_body(name: str, price: int, stock_info: dict,
                     change_pct: Optional[float] = None,
                     p1h: Optional[float] = None,
                     p30m: Optional[float] = None) -> str:
    """카카오 알람 본문 — 현재가·매입가·수익·등락·투자자 정보 포함"""
    avg   = stock_info.get("avg_price", 0)
    qty   = stock_info.get("quantity",  0)
    inv   = stock_info.get("investor",  {})
    session = session_label()

    lines = [f"📌 {name}  [{session}]", f"현재가: {price:,}원"]

    # 등락률
    chg_parts = []
    if change_pct is not None:
        chg_parts.append(f"당일 {change_pct:+.2f}%")
    if p1h is not None:
        chg_parts.append(f"-1h {p1h:+.2f}%")
    if p30m is not None:
        chg_parts.append(f"-30m {p30m:+.2f}%")
    if chg_parts:
        lines.append("등락: " + " | ".join(chg_parts))

    # 매입가 / 수익
    if avg:
        lines.append(f"매입가: {avg:,.0f}원")
    if avg and qty:
        profit     = (price - avg) * qty
        profit_pct = (price - avg) / avg * 100
        emoji      = "💰" if profit >= 0 else "📉"
        lines.append(f"수익: {emoji} {profit:+,.0f}원 ({profit_pct:+.2f}%)")

    # 투자자 동향
    inv_str = fmt_investor_kakao(inv)
    if inv_str:
        lines.append("── 투자자 동향 ──")
        lines.append(inv_str)

    return "\n".join(lines)


# ─── 알람 조건 체크 ─────────────────────────────────────────

def check_alerts(ticker: str, name: str, price: int, volume: int,
                 config: dict, state: AlertState, stock_info: dict):
    settings      = config["settings"]
    cooldown      = settings["alert_cooldown_minutes"]
    threshold_pct = settings["price_change_threshold_pct"]
    prev_price    = state.previous_prices.get(ticker)
    prev_volume   = state.previous_volumes.get(ticker)
    now5m         = int(datetime.now().timestamp() // 300)
    now10m        = int(datetime.now().timestamp() // 600)

    chg_pct = stock_info.get("change_pct")
    p1h     = pct_change(price, get_past_price(state, ticker, 60))
    p30m    = pct_change(price, get_past_price(state, ticker, 30))

    def body():
        return build_kakao_body(name, price, stock_info, chg_pct, p1h, p30m)

    # 가격 변동 %
    if prev_price and prev_price > 0:
        delta = (price - prev_price) / prev_price * 100
        if abs(delta) >= threshold_pct:
            direction = "▲" if delta > 0 else "▼"
            fire_alert(state, f"{ticker}_chg_{now5m}",
                       f"가격 변동 {direction} {delta:+.2f}%", body(), cooldown)

    # 목표가
    target = stock_info.get("target_price")
    if target and prev_price and prev_price < target <= price:
        fire_alert(state, f"{ticker}_target",
                   f"목표가 {target:,}원 도달 🎯", body(), cooldown)

    # 손절가
    stop_loss = stock_info.get("stop_loss_price")
    if stop_loss and prev_price and prev_price > stop_loss >= price:
        fire_alert(state, f"{ticker}_stop",
                   f"손절가 {stop_loss:,}원 도달 ⚠️", body(), cooldown)

    # 수익률 임계값 (보유종목)
    avg_price = stock_info.get("avg_price", 0)
    if avg_price and prev_price:
        cur_pnl  = (price     - avg_price) / avg_price * 100
        prev_pnl = (prev_price - avg_price) / avg_price * 100
        rules = config.get("alert_rules", {})
        for t in rules.get("profit_thresholds", [10, 20, 30]):
            if prev_pnl < t <= cur_pnl:
                fire_alert(state, f"{ticker}_profit_{t}",
                           f"수익률 +{t}% 돌파 💰", body(), cooldown * 4)
        for t in rules.get("loss_thresholds", [-10, -20, -30]):
            if prev_pnl > t >= cur_pnl:
                fire_alert(state, f"{ticker}_loss_{t}",
                           f"손실률 {t}% 돌파 📉", body(), cooldown * 4)

    # 거래량 급증
    spike = settings["volume_spike_multiplier"]
    if prev_volume and prev_volume > 0 and volume:
        ratio = volume / prev_volume
        if ratio >= spike:
            fire_alert(state, f"{ticker}_vol_{now10m}",
                       f"거래량 {ratio:.1f}배 급증 📊", body(), cooldown)


# ─── 카카오 요약 메시지 ─────────────────────────────────────

def send_open_summary(portfolio: list[dict], config: dict):
    """장 시작 시 보유종목 현황 전송"""
    now = datetime.now().strftime("%Y-%m-%d")
    header = f"🔔 [{now}] 장 시작 — 보유종목 현황\n{'─'*24}"
    lines  = [header]

    sorted_p = sorted(portfolio, key=lambda s: abs(s.get("profit_pct", 0)), reverse=True)
    for s in sorted_p:
        name  = s["name"]
        price = s["current_price"]
        avg   = s["avg_price"]
        qty   = s["quantity"]
        pnl   = (price - avg) * qty
        pct   = (price - avg) / avg * 100
        arrow = "▲" if pnl >= 0 else "▼"
        lines.append(f"{arrow} {name}\n   현재가 {price:,} | 매입 {avg:,.0f} | 손익 {pnl:+,.0f}원 ({pct:+.1f}%)")

    # 5개씩 분할 전송
    chunk_size = 5
    chunks = [lines[i:i+chunk_size] for i in range(1, len(lines), chunk_size)]
    for idx, chunk in enumerate(chunks):
        title = f"장 시작 현황 ({idx+1}/{len(chunks)})" if len(chunks) > 1 else "장 시작 현황"
        send_kakao(title, "\n\n".join(chunk))


def send_close_summary(portfolio: list[dict], config: dict):
    """장 마감 시 당일 결산 전송"""
    now   = datetime.now().strftime("%Y-%m-%d")
    total_pnl = sum((s["current_price"] - s["avg_price"]) * s["quantity"] for s in portfolio)
    total_eval = sum(s["current_price"] * s["quantity"] for s in portfolio)

    header = (
        f"📊 [{now}] 장 마감 결산\n{'─'*24}\n"
        f"총 평가금액: {total_eval:,}원\n"
        f"총 평가손익: {total_pnl:+,}원"
    )

    sorted_p = sorted(portfolio, key=lambda s: abs(s.get("profit_pct", 0)), reverse=True)
    lines = []
    for s in sorted_p:
        chg   = s.get("change_pct", 0) or 0
        pct   = s.get("profit_pct",  0)
        price = s["current_price"]
        avg   = s["avg_price"]
        qty   = s["quantity"]
        pnl   = (price - avg) * qty
        day_arrow = "▲" if chg  >= 0 else "▼"
        pnl_arrow = "💰" if pnl >= 0 else "📉"
        lines.append(
            f"{pnl_arrow} {s['name']}\n"
            f"   종가 {price:,} ({day_arrow}{chg:+.2f}%) | 손익 {pnl:+,.0f}원 ({pct:+.1f}%)"
        )

    chunks = [lines[i:i+5] for i in range(0, len(lines), 5)]
    for idx, chunk in enumerate(chunks):
        prefix = header if idx == 0 else f"📊 장 마감 결산 ({idx+1}/{len(chunks)})"
        send_kakao(prefix, "\n\n".join(chunk))


# ─── 종가배팅 ───────────────────────────────────────────────

def load_close_betting(portfolio: list[dict], watchlist: list[dict]) -> list[dict]:
    """close_betting.json 로드 + 종목명으로 ticker 자동 매칭"""
    if not CLOSE_BETTING_PATH.exists():
        return []
    try:
        items = json.loads(CLOSE_BETTING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

    all_stocks = {s["name"]: s["ticker"] for s in portfolio + watchlist}
    result = []
    for item in items:
        if "ticker" not in item:
            item["ticker"] = all_stocks.get(item.get("name", ""), "")
        if item["ticker"]:
            result.append(item)
        else:
            log.warning(f"종가배팅: '{item.get('name')}' 종목 코드를 찾을 수 없습니다.")
    return result


def check_close_betting(price: int, item: dict, state: AlertState):
    """익절가/물타기가 도달 시 카카오톡 알림"""
    ticker   = item["ticker"]
    name     = item.get("name", ticker)
    buy      = item.get("buy_price", 0)
    target   = item.get("익절가")
    avg_down = item.get("물타기가")

    def _alert(key, title, msg):
        cooldown = 60 * 60  # 1시간 쿨다운
        last = state.last_alert_time.get(key, 0)
        if time.time() - last < cooldown:
            return
        state.last_alert_time[key] = time.time()
        log.info(f"[종가배팅] {title}")
        try:
            send_kakao(title, msg)
        except Exception as e:
            log.warning(f"카카오 전송 실패: {e}")

    if buy:
        diff     = price - buy
        diff_pct = diff / buy * 100
        profit_str = f"{'📈' if diff >= 0 else '📉'} {diff:+,}원 ({diff_pct:+.2f}%)"
    else:
        profit_str = ""

    if target and price >= target:
        _alert(
            f"bet_target_{ticker}",
            f"🎯 {name} 익절가 도달",
            f"현재가: {price:,}원\n매수가: {buy:,}원\n손익: {profit_str}"
        )

    if avg_down and price <= avg_down:
        _alert(
            f"bet_avgdown_{ticker}",
            f"📉 {name} 물타기 구간",
            f"현재가: {price:,}원\n매수가: {buy:,}원\n손익: {profit_str}"
        )


# ─── 폴링 루프 ──────────────────────────────────────────────

def polling_loop(client: KISClient, config: dict, state: AlertState,
                 shared: dict, stop_event: threading.Event):
    interval = config["settings"]["check_interval_seconds"]

    market_was_open  = False
    close_bets       = []
    last_bet_reload  = 0

    while not stop_event.is_set():
        # 장이 한 번이라도 열렸다가 닫히면 종료 (시작 시 장외는 허용)
        currently_open = is_market_open()
        if market_was_open and not currently_open:
            log.info("장 마감 — 모니터링 종료")
            stop_event.set()
            break
        if currently_open:
            market_was_open = True

        # 보유종목 조회
        try:
            portfolio = client.get_portfolio()
            for s in portfolio:
                ticker = s["ticker"]
                price  = s["current_price"]
                try:
                    detail = client.get_stock_price(ticker)
                    s["change_pct"] = detail["change_pct"]
                    volume = detail["volume"]
                except Exception:
                    volume = 0
                try:
                    s["investor"] = client.get_investor_trend(ticker)
                except Exception:
                    pass
                check_alerts(ticker, s["name"], price, volume, config, state, s)
                state.previous_prices[ticker]  = price
                state.previous_volumes[ticker] = volume
                record_price(state, ticker, price)
            shared["portfolio"] = portfolio
        except Exception as e:
            log.warning(f"보유종목 조회 오류: {e}")

        # 관심종목 조회
        watchlist_prices = shared.get("watchlist_prices", {})
        for wl in config.get("watchlist", []):
            ticker = wl["ticker"]
            try:
                data   = client.get_stock_price(ticker)
                price  = data["price"]
                volume = data["volume"]
                try:
                    data["investor"] = client.get_investor_trend(ticker)
                except Exception:
                    pass
                merged = {**data, **wl}
                check_alerts(ticker, wl.get("name", data["name"]), price, volume,
                             config, state, merged)
                state.previous_prices[ticker]  = price
                state.previous_volumes[ticker] = volume
                record_price(state, ticker, price)
                watchlist_prices[ticker] = data
            except Exception as e:
                log.warning(f"{ticker} 조회 오류: {e}")
        shared["watchlist_prices"] = watchlist_prices

        # 종가배팅 5분마다 hot-reload + 가격 체크
        if time.time() - last_bet_reload > 300:
            close_bets = load_close_betting(
                shared.get("portfolio", []),
                config.get("watchlist", [])
            )
            last_bet_reload = time.time()
            if close_bets:
                log.info(f"종가배팅 {len(close_bets)}개 로드: {[b['name'] for b in close_bets]}")

        for bet in close_bets:
            try:
                price = client.get_stock_price(bet["ticker"])["price"]
                check_close_betting(price, bet, state)
            except Exception as e:
                log.warning(f"종가배팅 조회 오류 ({bet.get('name')}): {e}")

        stop_event.wait(interval)


# ─── WebSocket 실시간 ────────────────────────────────────────

def realtime_loop(client: KISClient, config: dict, state: AlertState, shared: dict):
    portfolio = shared.get("portfolio", [])
    watchlist = config.get("watchlist",  [])
    tickers   = [s["ticker"] for s in portfolio] + [w["ticker"] for w in watchlist]
    if not tickers:
        return

    def on_price(data: dict):
        ticker = data["ticker"]
        price  = data["price"]
        volume = data["volume"]

        ps = next((s for s in shared.get("portfolio", []) if s["ticker"] == ticker), None)
        if ps:
            ps["current_price"] = price
            ps["change_pct"]    = data["change_pct"]
            check_alerts(ticker, ps["name"], price, volume, config, state, ps)
            state.previous_prices[ticker] = price
            record_price(state, ticker, price)

        wl = next((w for w in config.get("watchlist", []) if w["ticker"] == ticker), None)
        if wl:
            wp = shared.setdefault("watchlist_prices", {})
            wp[ticker] = {**wp.get(ticker, {}), "price": price,
                          "change_pct": data["change_pct"], "volume": volume}
            check_alerts(ticker, wl.get("name", ticker), price, volume,
                         config, state, {**wp[ticker], **wl})
            state.previous_prices[ticker] = price
            record_price(state, ticker, price)

    client.subscribe_realtime(tickers, on_price)


# ─── 터미널 표시 (클라우드에선 생략 가능) ────────────────────

def fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "[dim]·[/dim]"
    s = "green" if val >= 0 else "red"
    return f"[{s}]{val:+.2f}%[/{s}]"


def build_display(portfolio, watchlist_prices, watchlist_cfg, state) -> object:
    if not RICH_AVAILABLE:
        return None
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sess    = session_label()

    t = Table(
        title=f"[bold cyan]주식 모니터[/bold cyan]  [dim]{now_str}  {sess}[/dim]",
        box=box.SIMPLE_HEAVY, header_style="bold white on dark_blue",
        show_lines=False, expand=True, padding=(0, 1),
    )
    t.add_column("",       min_width=2, max_width=2, no_wrap=True)
    t.add_column("종목명", min_width=12, ratio=3, no_wrap=True, style="bold")
    t.add_column("현재가", min_width=10, max_width=14, no_wrap=True, justify="right")
    t.add_column("등락%", min_width=7, max_width=9,  no_wrap=True, justify="right")
    t.add_column("-1h%",  min_width=7, max_width=9,  no_wrap=True, justify="right")
    t.add_column("-30m%", min_width=7, max_width=9,  no_wrap=True, justify="right")
    t.add_column("수익률", min_width=9, max_width=12, no_wrap=True, justify="right")
    t.add_column("평가손익", min_width=10, max_width=14, no_wrap=True, justify="right")

    for s in portfolio:
        price  = s.get("current_price", 0)
        pp     = s.get("profit_pct",    0.0)
        pl     = s.get("profit_loss",   0)
        ticker = s.get("ticker", "")
        name   = s.get("name", ticker)
        nd     = name[:20] + "…" if len(name) > 21 else name
        ps     = "green" if pp >= 0 else "red"
        t.add_row("[yellow]P[/yellow]", nd,
                  f"{price:,}" if price else "-",
                  fmt_pct(s.get("change_pct")),
                  fmt_pct(pct_change(price, get_past_price(state, ticker, 60))),
                  fmt_pct(pct_change(price, get_past_price(state, ticker, 30))),
                  f"[{ps}]{pp:+.2f}%[/{ps}]",
                  f"[{ps}]{pl:+,}[/{ps}]")

    if portfolio:
        t.add_section()

    for wl in watchlist_cfg:
        ticker = wl["ticker"]
        d      = watchlist_prices.get(ticker, {})
        price  = d.get("price", 0)
        name   = wl.get("name", ticker)
        nd     = name[:20] + "…" if len(name) > 21 else name
        tgt    = wl.get("target_price")
        stp    = wl.get("stop_loss_price")
        ts     = "  ".join(filter(None, [
            f"[green]목표 {tgt:,}[/green]" if tgt else "",
            f"[red]손절 {stp:,}[/red]"     if stp else "",
        ])) or "[dim]-[/dim]"
        t.add_row("[blue]W[/blue]", nd,
                  f"{price:,}" if price else "[dim]조회중[/dim]",
                  fmt_pct(d.get("change_pct") if price else None),
                  fmt_pct(pct_change(price, get_past_price(state, ticker, 60)) if price else None),
                  fmt_pct(pct_change(price, get_past_price(state, ticker, 30)) if price else None),
                  "[dim]-[/dim]", ts)
    return t


# ─── 메인 ───────────────────────────────────────────────────

def main():
    config = load_config()
    log.info("주식 모니터 시작")

    # API 연결
    try:
        client = KISClient(is_mock=config["settings"].get("is_mock", False))
        client.get_access_token()
        log.info("KIS API 연결 완료")
    except ValueError as e:
        log.error(str(e))
        return
    except Exception as e:
        log.error(f"API 연결 오류: {e}")
        return

    # ── 즉시 시작 (cron-job.org가 08:00 / 14:00 KST에 정확히 트리거) ──
    log.info(f"=== 모니터 시작 [{session_label()}] ===")

    state  = AlertState()
    shared = {"portfolio": [], "watchlist_prices": {}}
    stop_event = threading.Event()

    # 보유종목 초기 로드
    try:
        shared["portfolio"] = client.get_portfolio()
        log.info(f"보유종목 {len(shared['portfolio'])}개 로드")
    except Exception as e:
        log.warning(f"보유종목 로드 실패: {e}")

    # 장 시작 카카오 요약
    if shared["portfolio"]:
        try:
            send_open_summary(shared["portfolio"], config)
            log.info("장 시작 카카오 요약 전송 완료")
        except Exception as e:
            log.warning(f"장 시작 요약 전송 실패: {e}")

    # WebSocket 실시간 구독
    if config["settings"].get("use_realtime_websocket", True):
        try:
            realtime_loop(client, config, state, shared)
            log.info("WebSocket 연결됨")
        except Exception as e:
            log.warning(f"WebSocket 실패: {e}")

    # REST 폴링 스레드
    poll_thread = threading.Thread(
        target=polling_loop,
        args=(client, config, state, shared, stop_event),
        daemon=True,
    )
    poll_thread.start()

    # SIGTERM 핸들러 — GitHub Actions 6시간 초과 시 마감 요약 전송
    def _on_terminate(signum, frame):
        log.info("종료 신호 수신 (SIGTERM) — 마감 요약 전송 후 종료")
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_terminate)

    # 터미널 표시 (리치 없으면 그냥 대기)
    try:
        if RICH_AVAILABLE and sys.stdout.isatty():
            with Live(console=console, refresh_per_second=1) as live:
                while not stop_event.is_set():
                    disp = build_display(
                        shared["portfolio"],
                        shared.get("watchlist_prices", {}),
                        config.get("watchlist", []),
                        state,
                    )
                    if disp:
                        live.update(disp)
                    time.sleep(1)
        else:
            while not stop_event.is_set():
                time.sleep(30)
    except KeyboardInterrupt:
        log.info("수동 종료")
        stop_event.set()

    # 마감 처리
    client.close_websocket()
    log.info("=== 모니터 종료 ===")

    if shared["portfolio"]:
        try:
            send_close_summary(shared["portfolio"], config)
            log.info("마감 카카오 요약 전송 완료")
        except Exception as e:
            log.warning(f"마감 요약 전송 실패: {e}")



if __name__ == "__main__":
    main()
