#!/bin/bash
# 로컬 → 서버 파일 업로드 (scp 사용)
# 사용법: bash deploy/upload_to_server.sh <서버IP> [유저명]
# 예시:   bash deploy/upload_to_server.sh 12.34.56.78 ubuntu

SERVER_IP="${1:?서버 IP를 입력하세요}"
SERVER_USER="${2:-ubuntu}"
REMOTE_DIR="~/mystock"

LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "업로드: $LOCAL_DIR → $SERVER_USER@$SERVER_IP:$REMOTE_DIR"

# 서버에 디렉토리 생성
ssh "$SERVER_USER@$SERVER_IP" "mkdir -p $REMOTE_DIR/deploy"

# 소스 파일 업로드 (민감 파일 제외 후 .env, 토큰은 별도)
scp "$LOCAL_DIR"/*.py          "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/"
scp "$LOCAL_DIR/requirements.txt" "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/"
scp "$LOCAL_DIR/config.json"   "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/"
scp "$LOCAL_DIR/deploy/"*.sh   "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/deploy/"

# .env 및 카카오 토큰 (민감 파일 — 별도 전송)
echo ""
echo ".env 와 .kakao_token.json 을 전송합니다..."
scp "$LOCAL_DIR/.env"                "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/.env"
scp "$LOCAL_DIR/.kakao_token.json"   "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/.kakao_token.json" 2>/dev/null || \
    echo "  .kakao_token.json 없음 — 서버에서 python kakao_notify.py --auth 실행 필요"

echo ""
echo "업로드 완료!"
echo "서버에서 실행:"
echo "  ssh $SERVER_USER@$SERVER_IP"
echo "  cd mystock"
echo "  python3 -m venv venv && source venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  bash deploy/install_service.sh"
