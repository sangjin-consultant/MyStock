"""
카카오톡 나에게 보내기 알림
- 최초 1회 브라우저 인증 필요 (python kakao_notify.py --auth)
- 이후 자동으로 토큰 갱신
"""

import os
import json
import webbrowser
import requests
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TOKEN_PATH = Path(__file__).parent / ".kakao_token.json"
KAKAO_AUTH_URL  = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_SEND_URL  = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
REDIRECT_URI    = "http://localhost:5001/oauth"


# ─── 토큰 저장/로드 ─────────────────────────────────────────

def save_token(data: dict):
    TOKEN_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_token() -> dict | None:
    if not TOKEN_PATH.exists():
        return None
    return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))


# ─── 토큰 갱신 ──────────────────────────────────────────────

def refresh_access_token(rest_api_key: str, refresh_token: str) -> dict:
    payload = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token,
    }
    client_secret = os.getenv("KAKAO_CLIENT_SECRET", "")
    if client_secret:
        payload["client_secret"] = client_secret
    res = requests.post(KAKAO_TOKEN_URL, data=payload, timeout=10)
    res.raise_for_status()
    return res.json()


def get_valid_token(rest_api_key: str) -> str | None:
    """유효한 access_token 반환. 갱신 실패 시 None."""
    token_data = load_token()
    if not token_data:
        return None

    # access_token으로 바로 시도 — 만료됐으면 갱신
    try:
        refreshed = refresh_access_token(rest_api_key, token_data["refresh_token"])
        token_data["access_token"] = refreshed["access_token"]
        if "refresh_token" in refreshed:          # refresh_token도 갱신된 경우
            token_data["refresh_token"] = refreshed["refresh_token"]
        save_token(token_data)
        return token_data["access_token"]
    except Exception as e:
        import logging
        logging.getLogger("monitor").warning(f"카카오 토큰 갱신 실패: {e} — 재인증 필요")
        return None


# ─── 최초 인증 (브라우저 OAuth) ─────────────────────────────

def run_auth(rest_api_key: str):
    """브라우저로 카카오 로그인 → 코드 수신 → 토큰 저장"""
    auth_url = KAKAO_AUTH_URL + "?" + urlencode({
        "client_id": rest_api_key,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "talk_message",
    })

    auth_code_holder = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                auth_code_holder["code"] = qs["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write("인증 완료! 이 창을 닫아도 됩니다.".encode("utf-8"))

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", 5001), Handler)
    print(f"\n브라우저에서 카카오 로그인 페이지가 열립니다...")
    webbrowser.open(auth_url)
    print("로그인 완료 후 자동으로 진행됩니다.\n")
    server.handle_request()

    code = auth_code_holder.get("code")
    if not code:
        print("인증 코드를 받지 못했습니다.")
        return

    print(f"  인증 코드 수신: {code[:10]}...")

    # 코드 → 토큰 교환
    payload = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }
    client_secret = os.getenv("KAKAO_CLIENT_SECRET", "")
    if client_secret:
        payload["client_secret"] = client_secret
    res = requests.post(KAKAO_TOKEN_URL, data=payload, timeout=10)

    if res.status_code != 200:
        err = res.json()
        print(f"\n[오류] 토큰 교환 실패 ({res.status_code})")
        print(f"  error: {err.get('error')}")
        print(f"  error_description: {err.get('error_description')}")
        print("\n[확인 사항]")
        print("  1. developers.kakao.com → 내 앱 → 카카오 로그인 → Redirect URI")
        print(f"     정확히 입력됐는지 확인: {REDIRECT_URI}")
        print("  2. 동의항목 → '카카오톡 메시지 전송' → 선택 동의 설정 여부")
        print("  3. 위 항목 수정 후 다시 --auth 실행\n")
        return

    token_data = res.json()
    save_token(token_data)
    print("✓ 카카오 인증 완료! 토큰이 저장되었습니다.")
    print("  이제 주식 모니터를 실행하면 카카오톡으로 알림이 전송됩니다.\n")


# ─── 메시지 전송 ─────────────────────────────────────────────

def send_kakao(title: str, message: str) -> bool:
    """카카오톡 나에게 보내기. 성공 시 True."""
    rest_api_key = os.getenv("KAKAO_REST_API_KEY", "")
    if not rest_api_key:
        return False

    access_token = get_valid_token(rest_api_key)
    if not access_token:
        return False

    template = {
        "object_type": "text",
        "text": f"📊 주식 모니터\n{'─'*20}\n{title}\n{message}",
        "link": {"web_url": "", "mobile_web_url": ""},
    }
    try:
        res = requests.post(
            KAKAO_SEND_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data={"template_object": json.dumps(template, ensure_ascii=False)},
            timeout=10,
        )
        return res.status_code == 200
    except Exception:
        return False


# ─── CLI 인증 실행 ───────────────────────────────────────────

if __name__ == "__main__":
    import sys

    rest_api_key = os.getenv("KAKAO_REST_API_KEY", "")
    if not rest_api_key or rest_api_key == "여기에_카카오_REST_API_키_입력":
        print("먼저 .env 파일에 KAKAO_REST_API_KEY 를 입력하세요.")
        print("\n[카카오 앱 만들기]")
        print("1. https://developers.kakao.com 접속 → 로그인")
        print("2. 상단 '내 애플리케이션' → '애플리케이션 추가하기'")
        print("3. 앱 이름 입력 후 저장 → '앱 키' 탭에서 REST API 키 복사")
        print("4. 왼쪽 메뉴 '카카오 로그인' → 활성화 ON")
        print("5. '카카오 로그인' → 'Redirect URI' → http://localhost:5001/oauth 추가")
        print("6. '동의항목' → '카카오톡 메시지 전송' 선택 동의로 설정")
        print(f"\n7. .env 파일에 추가:\n   KAKAO_REST_API_KEY=복사한_REST_API_키\n")
        sys.exit(1)

    if "--auth" in sys.argv or not TOKEN_PATH.exists():
        run_auth(rest_api_key)
    else:
        # 테스트 메시지 전송
        ok = send_kakao("테스트 알람 🔔", "주식 모니터에서 카카오톡 연결 테스트입니다.")
        print("✓ 카카오톡 전송 성공!" if ok else "✗ 전송 실패. --auth 옵션으로 재인증하세요.")
