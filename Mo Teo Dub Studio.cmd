@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "TEODUB_PROJECT=%~dp0"
set "TEODUB_PYTHONW=%TEODUB_PROJECT%.venv\Scripts\pythonw.exe"
if not exist "%TEODUB_PYTHONW%" set "TEODUB_PYTHONW=%TEODUB_PROJECT%runtime\python\pythonw.exe"
if not exist "%TEODUB_PYTHONW%" (
  echo Chưa có môi trường chạy Tèo Dub Studio trong thư mục .venv hoặc runtime.
  echo Xem mục cài đặt trong README.md rồi mở lại ứng dụng.
  pause
  exit /b 1
)
start "" "%TEODUB_PYTHONW%" -X utf8 -B "%TEODUB_PROJECT%launch.py"
exit /b 0
