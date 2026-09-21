@echo off
setlocal DisableDelayedExpansion
title cvELD Image Studio - Close this window to stop
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Bootstrap.ps1" -Mode run
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Setup or application stopped with an error. See logs and docs\Manual_ja.html.
  pause
)
if "run"=="diagnose" pause
if "run"=="verify" pause
exit /b %EXITCODE%
