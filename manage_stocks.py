"""
관심종목/알람 설정 관리 CLI
보유종목은 실계좌에서 자동 조회되므로 여기선 관심종목과 알람 규칙만 관리

사용법:
  python manage_stocks.py list
  python manage_stocks.py add 035420 NAVER --target 200000 --stop 150000
  python manage_stocks.py remove 035420
  python manage_stocks.py set-threshold 3.0
  python manage_stocks.py set-interval 10
  python manage_stocks.py portfolio          ← 실계좌 보유종목 조회
"""

import argparse
import json
import sys
import os
from pathlib import Path

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    os.system("chcp 65001 > nul 2>&1")

from rich.console import Console
from rich.table import Table
from rich import box

CONFIG_PATH = Path(__file__).parent / "config.json"
console = Console()


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def cmd_list(args):
    config = load_config()
    s = config["settings"]
    console.print(
        f"\n[bold]설정[/bold]: 조회간격={s['check_interval_seconds']}초, "
        f"변동임계값=±{s['price_change_threshold_pct']}%%, "
        f"쿨다운={s['alert_cooldown_minutes']}분, "
        f"WebSocket={'켜짐' if s.get('use_realtime_websocket') else '꺼짐'}\n"
    )
    wl = config.get("watchlist", [])
    if wl:
        t = Table(title="[blue]관심 종목[/blue]", box=box.ROUNDED)
        t.add_column("종목명"); t.add_column("티커")
        t.add_column("목표가", justify="right"); t.add_column("손절가", justify="right")
        for s in wl:
            t.add_row(
                s["name"], s["ticker"],
                f"{s['target_price']:,}" if s.get("target_price") else "-",
                f"{s['stop_loss_price']:,}" if s.get("stop_loss_price") else "-",
            )
        console.print(t)
    else:
        console.print("[dim]관심종목 없음[/dim]")


def cmd_add(args):
    config = load_config()
    entry = {
        "ticker": args.ticker,
        "name": args.name,
        "target_price": args.target,
        "stop_loss_price": args.stop,
    }
    config["watchlist"] = [w for w in config["watchlist"] if w["ticker"] != args.ticker]
    config["watchlist"].append(entry)
    save_config(config)
    console.print(f"[green]✓[/green] 관심종목 추가: {args.name} ({args.ticker})")


def cmd_remove(args):
    config = load_config()
    before = len(config["watchlist"])
    config["watchlist"] = [w for w in config["watchlist"] if w["ticker"] != args.ticker]
    if len(config["watchlist"]) < before:
        save_config(config)
        console.print(f"[green]✓[/green] {args.ticker} 삭제 완료")
    else:
        console.print(f"[red]✗[/red] {args.ticker} 를 찾을 수 없습니다")


def cmd_set_threshold(args):
    config = load_config()
    config["settings"]["price_change_threshold_pct"] = args.pct
    save_config(config)
    console.print(f"[green]✓[/green] 변동 임계값 ±{args.pct}%%")


def cmd_set_interval(args):
    config = load_config()
    config["settings"]["check_interval_seconds"] = args.seconds
    save_config(config)
    console.print(f"[green]✓[/green] 조회 간격 {args.seconds}초")


def cmd_portfolio(args):
    """실계좌 보유종목 현황 조회"""
    try:
        from kis_api import KISClient
        config = load_config()
        client = KISClient(is_mock=config["settings"].get("is_mock", False))
        portfolio = client.get_portfolio()
        if not portfolio:
            console.print("[dim]보유종목 없음[/dim]")
            return

        t = Table(title="[yellow]보유 종목 현황[/yellow]", box=box.ROUNDED)
        t.add_column("종목명"); t.add_column("티커")
        t.add_column("수량", justify="right"); t.add_column("평균단가", justify="right")
        t.add_column("현재가", justify="right"); t.add_column("수익률", justify="right")
        t.add_column("평가손익", justify="right")
        for s in portfolio:
            pct = s["profit_pct"]
            pnl = s["profit_loss"]
            style = "green" if pct >= 0 else "red"
            t.add_row(
                s["name"], s["ticker"], f"{s['quantity']:,}",
                f"{s['avg_price']:,.0f}", f"{s['current_price']:,}",
                f"[{style}]{pct:+.2f}%%[/{style}]",
                f"[{style}]{pnl:+,}[/{style}]",
            )
        console.print(t)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
    except Exception as e:
        console.print(f"[red]오류: {e}[/red]")


def main():
    parser = argparse.ArgumentParser(description="주식 모니터 설정 관리")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="설정 및 관심종목 목록 출력")

    p = sub.add_parser("add", help="관심종목 추가")
    p.add_argument("ticker", help="종목코드 (예: 035420)")
    p.add_argument("name", help="종목명")
    p.add_argument("--target", type=float, default=None, help="목표가")
    p.add_argument("--stop", type=float, default=None, help="손절가")

    p = sub.add_parser("remove", help="관심종목 삭제")
    p.add_argument("ticker")

    p = sub.add_parser("set-threshold", help="가격 변동 임계값 설정 (단위: %%)")
    p.add_argument("pct", type=float)

    p = sub.add_parser("set-interval", help="조회 간격 설정 (단위: 초)")
    p.add_argument("seconds", type=int)

    sub.add_parser("portfolio", help="실계좌 보유종목 현황 조회")

    args = parser.parse_args()
    handlers = {
        "list": cmd_list, "add": cmd_add, "remove": cmd_remove,
        "set-threshold": cmd_set_threshold, "set-interval": cmd_set_interval,
        "portfolio": cmd_portfolio,
    }
    if args.cmd in handlers:
        handlers[args.cmd](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
