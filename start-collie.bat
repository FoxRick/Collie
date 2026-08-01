@echo off
setlocal

if not exist "%~dp0collie-ui\package.json" (
  echo [Collie] The collie-ui folder was not found next to this launcher.
  exit /b 1
)

cd /d "%~dp0collie-ui"

where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo [Collie] npm.cmd was not found. Install Node.js and make sure it is on PATH.
  exit /b 1
)

call npm.cmd run dev
set "COLLIE_LAUNCH_EXIT_CODE=%ERRORLEVEL%"
if not "%COLLIE_LAUNCH_EXIT_CODE%"=="0" (
  echo [Collie] The development app exited with code %COLLIE_LAUNCH_EXIT_CODE%.
)
exit /b %COLLIE_LAUNCH_EXIT_CODE%
