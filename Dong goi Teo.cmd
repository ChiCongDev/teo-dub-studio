@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "TEODUB_PROJECT=%~dp0"
if not exist "%TEODUB_PROJECT%.venv\Scripts\python.exe" (
  echo Thiếu .venv của Tèo.
  pause
  exit /b 1
)
echo Đóng gói bộ Tèo đầy đủ cho người mới. Bước này tải Python và nén vài GB, có thể mất vài chục phút.
"%TEODUB_PROJECT%.venv\Scripts\python.exe" -X utf8 -B "%TEODUB_PROJECT%scripts\package_bundle.py" %*
echo.
pause
