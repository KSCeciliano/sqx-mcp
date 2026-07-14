param(
  [string]$RepoUrl = 'https://github.com/KSCeciliano/sqx-mcp.git',
  [string]$Branch = 'fix/hermes-windows-stability',
  [string]$DeployRoot = 'C:\Users\Pke\tools\sqx-mcp-win'
)
$ErrorActionPreference = 'Stop'
$repoPath = Join-Path $DeployRoot 'repo'
$venvPath = Join-Path $DeployRoot '.venv'
$backup = Join-Path $DeployRoot ('repo_backup_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
if (Test-Path $repoPath) { Move-Item $repoPath $backup }
& git clone --branch $Branch $RepoUrl $repoPath
if (-not (Test-Path (Join-Path $venvPath 'Scripts\python.exe'))) { py -3.11 -m venv $venvPath }
& (Join-Path $venvPath 'Scripts\python.exe') -m pip install -U pip
& (Join-Path $venvPath 'Scripts\python.exe') -m pip install -e "$repoPath[dev]"
$wrapper = Join-Path $DeployRoot 'run-sqxwin.cmd'
@"
@echo off
setlocal
set DEPLOY_ROOT=$DeployRoot
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
"@ | Set-Content -Encoding ASCII $wrapper
$head = (& git -C $repoPath rev-parse HEAD).Trim()
Write-Output ("DEPLOYED_HEAD=" + $head)
Write-Output ("BACKUP_REPO=" + $backup)
Write-Output ("WRAPPER=" + $wrapper)
Write-Output ("PYTHON=" + (Join-Path $venvPath 'Scripts\python.exe'))
Write-Output ("REPO=" + $repoPath)
