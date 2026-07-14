param(
  [string]$DeployRoot = 'C:\Users\Pke\tools\sqx-mcp-win',
  [int]$Port = 8765,
  [string]$PolicyLevel = 'read-only'
)
$ErrorActionPreference = 'Stop'
$repo = Join-Path $DeployRoot 'repo'
$python = Join-Path $DeployRoot '.venv\Scripts\python.exe'
$runtime = Join-Path $DeployRoot 'runtime'
$pidFile = Join-Path $runtime 'sqx-mcp-http.pid'
$logFile = Join-Path $runtime 'sqx-mcp-http.log'
$errorLogFile = Join-Path $runtime 'sqx-mcp-http.err.log'
if (!(Test-Path $python)) { throw "Missing Python: $python" }
if (!(Test-Path (Join-Path $repo 'src\sq_mcp\__main__.py'))) { throw "Missing repo: $repo" }
if (!(Test-Path 'C:\SQX_144_Full\sqcli.exe')) { throw 'Missing C:\SQX_144_Full\sqcli.exe' }
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
if (Test-Path $pidFile) {
  $oldPid = [int](Get-Content $pidFile -Raw)
  if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) { throw "Service already running as PID $oldPid" }
  Remove-Item $pidFile -Force
}
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "Port $Port already has a listener (PID $($listener.OwningProcess))" }
$engineListener = Get-NetTCPConnection -LocalPort 5050 -State Listen -ErrorAction SilentlyContinue
if ($engineListener) { throw "SQX port 5050 already has a listener (PID $($engineListener.OwningProcess)); stop the existing owner first" }
$env:SQX_HOME = 'C:\SQX_144_Full'
$env:SQX_HTTP_URL = 'http://localhost:5050'
$env:SQX_HTTP_PORT = '5050'
$env:SQX_MCP_TRANSPORT = 'streamable-http'
$env:SQX_MCP_HOST = '127.0.0.1'
$env:SQX_MCP_PORT = "$Port"
$env:SQX_MCP_PATH = '/mcp'
$env:SQX_POLICY_LEVEL = $PolicyLevel
$env:SQX_MANAGED_SINGLETON = '1'
$env:SQX_SINGLETON_LOCK = Join-Path $runtime 'sqx-mcp-managed.lock'
$env:SQX_EVIDENCE_DIR = Join-Path $runtime 'evidence'
$proc = Start-Process -FilePath $python -ArgumentList '-m','sq_mcp' -WorkingDirectory $repo -RedirectStandardOutput $logFile -RedirectStandardError $errorLogFile -PassThru -WindowStyle Hidden
Set-Content -Encoding ASCII $pidFile $proc.Id
for ($i=0; $i -lt 120; $i++) {
  Start-Sleep -Milliseconds 500
  if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) { throw "Service exited during startup. See $logFile" }
  if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    [pscustomobject]@{ok=$true; pid=$proc.Id; url="http://127.0.0.1:$Port/mcp"; policy=$PolicyLevel; log=$logFile} | ConvertTo-Json
    exit 0
  }
}
throw "Timed out waiting for HTTP listener on $Port. See $logFile"
