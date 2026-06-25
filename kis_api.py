"""
한국투자증권 Open API 클라이언트
- OAuth2 토큰 자동 갱신
- 국내 주식 현재가 조회
- 보유종목 조회 (실계좌 연동)
- WebSocket 실시간 체결가 구독
"""

import os
import json
import time
import logging
import requests
import websocket
import threading

log = logging.getLogger("monitor")
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# 실전/모의 투자 도메인
REAL_DOMAIN = "https://openapi.koreainvestment.com:9443"
MOCK_DOMAIN = "https://openapivts.koreainvestment.com:29443"

REAL_WS = "ws://ops.koreainvestment.com:21000"
MOCK_WS = "ws://ops.koreainvestment.com:31000"

TOKEN_CACHE = Path(__file__).parent / ".token_cache.json"


class KISClient:
    def __init__(self, is_mock: bool = False):
        self.app_key = os.getenv("KIS_APP_KEY", "")
        self.app_secret = os.getenv("KIS_APP_SECRET", "")
        self.account_no = os.getenv("KIS_ACCOUNT_NO", "")
        self.account_suffix = os.getenv("KIS_ACCOUNT_SUFFIX", "01")
        self.is_mock = is_mock
        self.base_url = MOCK_DOMAIN if is_mock else REAL_DOMAIN
        self.ws_url = MOCK_WS if is_mock else REAL_WS
        self._access_token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_callbacks: dict[str, Callable] = {}
        self._approval_key: Optional[str] = None

        if not self.app_key or self.app_key == "여기에_앱키_입력":
            raise ValueError(".env 파일에 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO 를 설정하세요.")

    # ─── 인증 ───────────────────────────────────────────────

    def _load_cached_token(self) -> bool:
        if not TOKEN_CACHE.exists():
            return False
        try:
            data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            expires = datetime.fromisoformat(data["expires"])
            if datetime.now() < expires - timedelta(minutes=5):
                self._access_token = data["token"]
                self._token_expires = expires
                return True
        except Exception:
            pass
        return False

    def _save_token_cache(self):
        TOKEN_CACHE.write_text(
            json.dumps({"token": self._access_token, "expires": self._token_expires.isoformat()},
                       ensure_ascii=False),
            encoding="utf-8"
        )

    def get_access_token(self) -> str:
        if self._access_token and self._token_expires and datetime.now() < self._token_expires - timedelta(minutes=5):
            return self._access_token
        if self._load_cached_token():
            return self._access_token

        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        res = requests.post(url, json=body, timeout=10)
        res.raise_for_status()
        data = res.json()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires = datetime.now() + timedelta(seconds=expires_in)
        self._save_token_cache()
        return self._access_token

    def _headers(self, tr_id: str, extra: dict = None) -> dict:
        h = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.get_access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if extra:
            h.update(extra)
        return h

    def _get(self, path: str, tr_id: str, params: dict, retries: int = 3) -> dict:
        """500 오류 시 최대 retries 회 재시도 (지수 백오프)"""
        url = f"{self.base_url}{path}"
        last_err = None
        for attempt in range(retries):
            try:
                res = requests.get(url, headers=self._headers(tr_id), params=params, timeout=10)
                if res.status_code == 500 and attempt < retries - 1:
                    time.sleep(2 ** attempt)   # 1, 2, 4초 대기
                    continue
                res.raise_for_status()
                data = res.json()
                if data.get("rt_cd") != "0":
                    raise RuntimeError(f"KIS API 오류: {data.get('msg1', data)}")
                return data
            except requests.exceptions.HTTPError as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        raise last_err

    # ─── 국내 주식 현재가 ────────────────────────────────────

    def get_stock_price(self, ticker: str) -> dict:
        """
        국내 주식 현재가 조회 (NXT 애프터마켓 시간엔 NX 코드 자동 사용)
        반환: {price, open, high, low, volume, change_pct, change_price, name}
        """
        from market_schedule import now_kst
        from datetime import time as _time
        _t = now_kst().time()
        # NXT 애프터마켓(15:30~20:00)엔 "NX" 마켓 코드로 실시간 가격 조회
        mkt_code = "NX" if _time(15, 30) <= _t < _time(20, 0) else "J"
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": mkt_code, "FID_INPUT_ISCD": ticker},
        )
        o = data["output"]
        return {
            "ticker": ticker,
            "name": o.get("hts_kor_isnm", ticker),
            "price": int(o["stck_prpr"]),
            "open": int(o["stck_oprc"]),
            "high": int(o["stck_hgpr"]),
            "low": int(o["stck_lwpr"]),
            "volume": int(o["acml_vol"]),
            "change_price": int(o["prdy_vrss"]),
            "change_pct": float(o["prdy_ctrt"]),
            "status": o.get("iscd_stat_cls_code", ""),
        }

    # ─── 투자자별 매매동향 ──────────────────────────────────────

    def get_investor_trend(self, ticker: str) -> dict:
        """
        투자자별 매매동향 조회 (당일 기준, output 첫 번째 행)
        반환: {
          foreign:    {buy, sell, net_qty, net_amt},   # 외국인
          institute:  {buy, sell, net_qty, net_amt},   # 기관
          individual: {buy, sell, net_qty, net_amt},   # 개인
          date: "YYYYMMDD"
        }
        net_qty: 순매수 수량 (양수=순매수, 음수=순매도)
        net_amt: 순매수 금액 (백만원)
        """
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            tr_id="FHKST01010900",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        rows = data.get("output", [])
        if not rows:
            return {}
        o = rows[0]  # 가장 최근 영업일

        def parse(prefix):
            buy      = int(o.get(f"{prefix}_shnu_vol",     "0") or 0)
            sell     = int(o.get(f"{prefix}_seln_vol",     "0") or 0)
            net_qty  = int(o.get(f"{prefix}_ntby_qty",     "0") or 0)
            net_amt  = int(o.get(f"{prefix}_ntby_tr_pbmn", "0") or 0)  # 백만원
            return {"buy": buy, "sell": sell, "net_qty": net_qty, "net_amt": net_amt}

        return {
            "date":       o.get("stck_bsop_date", ""),
            "foreign":    parse("frgn"),   # 외국인
            "institute":  parse("orgn"),   # 기관
            "individual": parse("prsn"),   # 개인
        }

    # ─── 보유종목 조회 ───────────────────────────────────────

    def get_portfolio(self) -> list[dict]:
        """
        실계좌 보유종목 전체 조회
        반환: [{ticker, name, quantity, avg_price, current_price, profit_loss, profit_pct}, ...]
        """
        tr_id = "VTTC8434R" if self.is_mock else "TTTC8434R"
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_suffix,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._get("/uapi/domestic-stock/v1/trading/inquire-balance", tr_id=tr_id, params=params)

        result = []
        for item in data.get("output1", []):
            qty = int(item.get("hldg_qty", 0))
            if qty == 0:
                continue
            result.append({
                "ticker": item["pdno"],
                "name": item["prdt_name"],
                "quantity": qty,
                "avg_price": float(item["pchs_avg_pric"]),
                "current_price": int(item["prpr"]),
                "profit_loss": int(item["evlu_pfls_amt"]),
                "profit_pct": float(item["evlu_pfls_rt"]),
                "eval_amount": int(item["evlu_amt"]),
                "buy_amount": int(item["pchs_amt"]),
            })
        return result

    # ─── WebSocket 실시간 체결가 ─────────────────────────────

    def get_approval_key(self) -> str:
        if self._approval_key:
            return self._approval_key
        url = f"{self.base_url}/oauth2/Approval"
        body = {"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret}
        res = requests.post(url, json=body, timeout=10)
        res.raise_for_status()
        self._approval_key = res.json()["approval_key"]
        return self._approval_key

    def subscribe_realtime(self, tickers: list[str], on_price: Callable[[dict], None]):
        """WebSocket으로 실시간 체결가 구독"""
        approval_key = self.get_approval_key()

        def on_open(ws):
            for ticker in tickers:
                msg = json.dumps({
                    "header": {
                        "approval_key": approval_key,
                        "custtype": "P",
                        "tr_type": "1",
                        "content-type": "utf-8",
                    },
                    "body": {
                        "input": {"tr_id": "H0STCNT0", "tr_key": ticker}
                    }
                })
                ws.send(msg)

        def on_message(ws, message):
            if message.startswith("{"):
                return  # 구독 확인 메시지
            try:
                parts = message.split("|")
                if len(parts) < 4:
                    return
                tr_id = parts[1]
                if tr_id != "H0STCNT0":
                    return
                fields = parts[3].split("^")
                # 필드 순서: 유가증권단축종목코드^주식체결시간^주식현재가^전일대비부호^전일대비...
                ticker_code = fields[0]
                price = int(fields[2])
                change_price = int(fields[4])
                change_pct = float(fields[5])
                volume = int(fields[12]) if len(fields) > 12 else 0
                on_price({
                    "ticker": ticker_code,
                    "price": price,
                    "change_price": change_price,
                    "change_pct": change_pct,
                    "volume": volume,
                    "time": fields[1],
                })
            except Exception:
                pass

        self._ws_stop = False

        def on_error(ws, error):
            log.warning(f"WebSocket 오류: {error}")

        def on_close(ws, *args):
            if not self._ws_stop:
                log.warning("WebSocket 끊김 — 10초 후 재연결 시도")

        def run_with_reconnect():
            delay = 10
            while not self._ws_stop:
                try:
                    self._ws = websocket.WebSocketApp(
                        self.ws_url,
                        on_open=on_open,
                        on_message=on_message,
                        on_error=on_error,
                        on_close=on_close,
                    )
                    self._ws.run_forever(ping_interval=30, ping_timeout=10)
                except Exception as e:
                    log.warning(f"WebSocket 예외: {e}")
                if self._ws_stop:
                    break
                log.info(f"WebSocket {delay}초 후 재연결...")
                time.sleep(delay)
                delay = min(delay * 2, 120)  # 최대 2분 간격

        self._ws_thread = threading.Thread(target=run_with_reconnect, daemon=True)
        self._ws_thread.start()

    def close_websocket(self):
        self._ws_stop = True
        if self._ws:
            self._ws.close()
