@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
start "" "%~dp0.venv-web\Scripts\pythonw.exe" -X utf8 -B -m teodub_web.address_setup
exit /b 0
