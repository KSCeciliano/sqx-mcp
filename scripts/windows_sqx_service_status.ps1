param([string]$DeployRoot = 'C:\Users\Pke\tools\sqx-mcp-win')
$runtime = Join-Path $DeployRoot 'runtime'
$pidFile = Join-Path $runtime 'sqx-mcp-http.pid'
$pidValue = if (Test-Path $pidFile) { [int](Get-Content $pidFile -Raw) } else { $null }
$processes = @(Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -eq 'python.exe' -and $_.CommandLine -match '(?i)-m\s+sq_mcp') -or
  ($_.Name -eq 'sqcli.exe' -and $_.ExecutablePath -like 'C:\SQX_144_Full*')
} | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine)
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 5050,8765 } | Select-Object LocalAddress,LocalPort,OwningProcess)
[pscustomobject]@{ok=$true; launcher_pid=$pidValue; processes=$processes; listeners=$listeners; url='http://127.0.0.1:8765/mcp'} | ConvertTo-Json -Depth 5
