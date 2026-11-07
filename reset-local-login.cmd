@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python -m spr.cli local-reset
pause
