@echo off
setlocal
set DEPLOY_ROOT=C:\Users\Pke\tools\sqx-mcp-win
set REPO=%DEPLOY_ROOT%\repo
set PY=%DEPLOY_ROOT%\.venv\Scripts\python.exe
set SQX_HOME=C:\SQX_144_Full
set SQX_HTTP_URL=http://localhost:5050
set SQX_HTTP_PORT=5050
if not exist "%PY%" (
  echo ERROR: Missing Python launcher: %PY%
  exit /b 1
)
if not exist "%REPO%\src\sq_mcp\__main__.py" (
  echo ERROR: Missing sqx-mcp repo: %REPO%
  exit /b 1
)
if not exist "%SQX_HOME%\sqcli.exe" (
  echo ERROR: Missing SQX install or sqcli.exe: %SQX_HOME%
  exit /b 1
)
cd /d "%REPO%"
call "%PY%" -m sq_mcp
endlocal
