$ErrorActionPreference = 'SilentlyContinue'
$targets = Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -match 'sqcli.exe|StrategyQuantX.exe|StrategyQuantX_ui.exe') -or
  ($_.ExecutablePath -like 'C:\Users\Pke\tools\sqx-mcp-win*') -or
  ($_.ExecutablePath -like 'C:\SQX_144_Full*')
}
$targets | ForEach-Object { cmd /c "taskkill /F /T /PID $($_.ProcessId)" | Out-Null }
Start-Sleep -Seconds 5
$result = [pscustomobject]@{
  remaining = @(Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match 'sqcli.exe|StrategyQuantX.exe|StrategyQuantX_ui.exe') -or
    ($_.ExecutablePath -like 'C:\Users\Pke\tools\sqx-mcp-win*') -or
    ($_.ExecutablePath -like 'C:\SQX_144_Full*')
  } | Select-Object ProcessId,Name,ExecutablePath,CommandLine)
  port5050 = @(Get-NetTCPConnection -LocalPort 5050 -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,State,OwningProcess)
}
$result | ConvertTo-Json -Depth 5
