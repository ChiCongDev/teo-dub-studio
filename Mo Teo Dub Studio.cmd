@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "TEODUB_PROJECT=%~dp0"
if not exist "%TEODUB_PROJECT%.venv\Scripts\pythonw.exe" (
  echo Chưa có môi trường chạy Tèo Dub Studio trong thư mục .venv.
  echo Xem mục cài đặt trong README.md rồi mở lại ứng dụng.
  pause
  exit /b 1
)
start "" "%TEODUB_PROJECT%.venv\Scripts\pythonw.exe" -X utf8 -B "%TEODUB_PROJECT%launch.py"
exit /b 0
