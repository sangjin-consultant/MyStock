#!/bin/bash
# 클라우드 서버 초기 설정 스크립트 (Ubuntu 22.04 기준)
# 사용법: bash setup_server.sh

set -e

echo "=== 주식 모니터 서버 설정 ==="

# Python 3.11+ 설치
sudo apt-get update -q
sudo apt-get install -y python3 python3-pip python3-venv

# 프로젝트 디렉토리
PROJECT_DIR="$HOME/mystock"
mkdir -p "$PROJECT_DIR"

echo ""
echo "프로젝트 파일을 $PROJECT_DIR 에 업로드하세요."
echo "업로드 후 아래 명령어를 실행하세요:"
echo ""
echo "  cd $PROJECT_DIR"
echo "  python3 -m venv venv"
echo "  source venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  cp .env.example .env   # .env 파일에 API 키 입력"
echo ""
echo "카카오 인증 (최초 1회, 로컬 PC에서 실행):"
echo "  python kakao_notify.py --auth"
echo "  # 생성된 .kakao_token.json 을 서버에 복사"
echo ""
echo "서비스 등록:"
echo "  bash deploy/install_service.sh"
