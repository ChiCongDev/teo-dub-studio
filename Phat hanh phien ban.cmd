@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "TEODUB_PROJECT=%~dp0"
if not exist "%TEODUB_PROJECT%.venv\Scripts\python.exe" (
  echo Thiếu .venv của Tèo.
  pause
  exit /b 1
)
echo Phát hành phiên bản mới của Tèo (chỉ dùng trên máy của chủ dự án).
echo Lần đầu hãy chạy:  "Phat hanh phien ban.cmd" --set-token
echo Muốn thử mà chưa đăng:  "Phat hanh phien ban.cmd" --dry-run
echo.
"%TEODUB_PROJECT%.venv\Scripts\python.exe" -X utf8 -B "%TEODUB_PROJECT%scripts\release.py" %*
echo.
pause
