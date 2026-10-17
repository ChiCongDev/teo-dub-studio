@echo off
if not exist "%~dp0tools\web-data-root.txt" (
  echo Hay mo Teo Web truoc de tao tai khoan quan tri.
  pause
  exit /b 1
)
for /f "usebackq delims=" %%R in ("%~dp0tools\web-data-root.txt") do notepad.exe "%%R\TAI_KHOAN_QUAN_TRI.txt"
 