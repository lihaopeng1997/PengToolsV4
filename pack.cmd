@echo off
rem PengToolsHub release packer.
rem Double-click this file, or run: pack.cmd -NoPause   (PACK_NO_PAUSE=1 also works)
setlocal EnableExtensions
cd /d "%~dp0"
title PengToolsHub pack

set "RC=0"
set "LOG=%~dp0build\pack-last.log"
set "PAUSE_AT_END=1"
set "CHECK_ARG="
for %%A in (%*) do (
  if /i "%%A"=="-NoPause" set "PAUSE_AT_END=0"
  if /i "%%A"=="/nopause" set "PAUSE_AT_END=0"
  if /i "%%A"=="-Check" set "CHECK_ARG=-CheckOnly"
  if /i "%%A"=="/check" set "CHECK_ARG=-CheckOnly"
)
if defined PACK_NO_PAUSE set "PAUSE_AT_END=0"

if not exist "%~dp0run.py" (
  echo [ERROR] run.py not found. Put pack.cmd in the repository root.
  set "RC=1"
  goto :finish
)

if not exist "%~dp0scripts\pack.ps1" (
  echo [ERROR] scripts\pack.ps1 not found. The repository is incomplete.
  set "RC=1"
  goto :finish
)

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] powershell.exe not found in PATH.
  set "RC=1"
  goto :finish
)

echo PengToolsHub pack
echo Repo: %~dp0
echo First run creates .venv-build and downloads PyInstaller; allow 3-10 minutes.
echo Use "pack.cmd -Check -NoPause" to verify the build environment only.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\pack.ps1" %CHECK_ARG%
set "RC=%ERRORLEVEL%"

:finish
echo.
if "%RC%"=="0" (
  echo [OK] Pack finished. Artifact paths are listed above.
) else (
  echo [FAILED] Exit code %RC%.
  echo Log: %LOG%
  echo.
  echo Common causes:
  echo   1. PengToolsHub.exe under dist\ or Installer\ is still running. Close it first.
  echo   2. First run needs network access to install PyInstaller.
  echo   3. Broken build environment: delete .venv-build and run again.
  echo   4. Custom interpreter: point PENGTOOLS_BUILD_PYTHON to Python 3.12 with PyInstaller 6.22.2.
)
if "%PAUSE_AT_END%"=="1" pause
exit /b %RC%
