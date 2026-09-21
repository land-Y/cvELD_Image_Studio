@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Bootstrap.ps1" -Mode verify
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Setup or application stopped with an error. See logs and docs\Manual_ja.html.
  pause
)
rem Verification complete.
if "%EXITCODE%"=="0" pause
exit /b %EXITCODE%
