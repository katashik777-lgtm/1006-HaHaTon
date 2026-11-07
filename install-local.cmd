@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
 python launch.py install --local-demo
) else (
 py -3 launch.py install --local-demo
)
pause
