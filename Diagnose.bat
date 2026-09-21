@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Bootstrap.ps1" -Mode diagnose
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Setup or application stopped with an error. See logs and docs\Manual_ja.html.
  pause
)
if "%EXITCODE%"=="0" pause
rem Diagnosis complete.
exit /b %EXITCODE%
