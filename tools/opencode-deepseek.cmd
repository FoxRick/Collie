@echo off
setlocal

set "OPENCODE_CLI=%APPDATA%\npm\opencode.cmd"

if not exist "%OPENCODE_CLI%" (
  echo OpenCode CLI was not found at "%OPENCODE_CLI%". 1>&2
  echo Reinstall it with: npm install -g opencode-ai@latest 1>&2
  exit /b 1
)

if "%~1"=="" (
  echo Usage: tools\opencode-deepseek.cmd "your task" 1>&2
  exit /b 2
)

call "%OPENCODE_CLI%" run --model deepseek/deepseek-v4-flash %*
exit /b %errorlevel%
