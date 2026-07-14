param([string]$DeployRoot = 'C:\Users\Pke\tools\sqx-mcp-win')
$ErrorActionPreference = 'SilentlyContinue'
$runtime = Join-Path $DeployRoot 'runtime'
$pidFile = Join-Path $runtime 'sqx-mcp-http.pid'
$managedPid = $null
if (Test-Path $pidFile) { $managedPid = [int](Get-Content $pidFile -Raw) }
if ($managedPid) { cmd /c "taskkill /F /T /PID $managedPid" | Out-Null }
Start-Sleep -Seconds 3
Get-CimInstance Win32_Process | Where-Object {
  ($_.ExecutablePath -like "$DeployRoot*") -or
  ($_.ExecutablePath -like 'C:\SQX_144_Full*' -and $_.Name -match 'sqcli.exe|StrategyQuantX.exe|StrategyQuantX_ui.exe')
} | ForEach-Object { cmd /c "taskkill /F /T /PID $($_.ProcessId)" | Out-Null }
Start-Sleep -Seconds 3
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -eq 'python.exe' -and $_.CommandLine -match '(?i)-m\s+sq_mcp') -or
  ($_.ExecutablePath -like "$DeployRoot*") -or
  ($_.ExecutablePath -like 'C:\SQX_144_Full*' -and $_.Name -match 'sqcli.exe|StrategyQuantX.exe|StrategyQuantX_ui.exe')
} | ForEach-Object { cmd /c "taskkill /F /T /PID $($_.ProcessId)" | Out-Null }
Start-Sleep -Seconds 3
$remaining = @(Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -eq 'python.exe' -and $_.CommandLine -match '(?i)-m\s+sq_mcp') -or
  ($_.ExecutablePath -like "$DeployRoot*") -or
  ($_.ExecutablePath -like 'C:\SQX_144_Full*' -and $_.Name -match 'sqcli.exe|StrategyQuantX.exe|StrategyQuantX_ui.exe')
} | Select-Object ProcessId,Name,ExecutablePath,CommandLine)
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 5050,8765 } | Select-Object LocalAddress,LocalPort,OwningProcess)
[pscustomobject]@{ok=($remaining.Count -eq 0 -and $listeners.Count -eq 0); remaining=$remaining; listeners=$listeners} | ConvertTo-Json -Depth 5
