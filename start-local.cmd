@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
 .venv\Scripts\python.exe launch.py start %*
) else (
 python launch.py start %*
)
pause
