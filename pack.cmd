@echo off
setlocal
cd /d "%~dp0"
title PengToolsHub pack
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\pack.ps1"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo Pack finished. Paths are printed above.
) else (
  echo Pack did not finish. Exit %RC%. Log: %CD%\build\pack-last.log
)
echo.
pause
exit /b %RC%
