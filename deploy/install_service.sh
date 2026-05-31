#!/bin/bash
# systemd 서비스 등록 — 서버 재시작 시 자동 복구
# 사용법: bash deploy/install_service.sh

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
USER_NAME="$(whoami)"

SERVICE_FILE="/etc/systemd/system/stock-monitor.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Stock Monitor Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$PROJECT_DIR
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV_PYTHON $PROJECT_DIR/stock_monitor.py
Restart=on-failure
RestartSec=60
StandardOutput=append:$PROJECT_DIR/monitor.log
StandardError=append:$PROJECT_DIR/monitor.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable stock-monitor
sudo systemctl start  stock-monitor

echo "서비스 상태:"
sudo systemctl status stock-monitor --no-pager

echo ""
echo "유용한 명령어:"
echo "  sudo systemctl status  stock-monitor   # 상태 확인"
echo "  sudo systemctl restart stock-monitor   # 재시작"
echo "  sudo journalctl -u stock-monitor -f    # 실시간 로그"
echo "  tail -f $PROJECT_DIR/monitor.log       # 모니터 로그"
echo "  tail -f $PROJECT_DIR/alerts.log        # 알람 로그"
