@echo off
chcp 65001 > nul
title 주식 모니터링 에이전트
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python stock_monitor.py
pause
