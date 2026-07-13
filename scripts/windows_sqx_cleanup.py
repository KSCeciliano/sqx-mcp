import asyncio, subprocess, time, json
from pathlib import Path

PS_SCRIPT = r'''
$ErrorActionPreference = 'SilentlyContinue'

function Get-HermesTargets {
  Get-CimInstance Win32_Process |
    Where-Object {
      ($_.Name -match 'sqcli.exe|StrategyQuantX.exe|StrategyQuantX_ui.exe') -or
      ($_.ExecutablePath -like 'C:\\Users\\Pke\\tools\\sqx-mcp-win*') -or
      ($_.ExecutablePath -like 'C:\\SQX_144_Full*')
    } |
    Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine
}

function Kill-ByIds($ids) {
  foreach ($id in $ids) {
    cmd /c "taskkill /F /T /PID $id" | Out-Null
  }
}

$rounds = @()
for ($round=1; $round -le 4; $round++) {
  $snapshot = Get-HermesTargets
  $rounds += [pscustomobject]@{
    round = $round
    count = @($snapshot).Count
    pids = @($snapshot | ForEach-Object { $_.ProcessId })
    names = @($snapshot | ForEach-Object { $_.Name })
  }

  $wrapper = @($snapshot | Where-Object { $_.ExecutablePath -like 'C:\\Users\\Pke\\tools\\sqx-mcp-win*' } | ForEach-Object { $_.ProcessId })
  if ($wrapper.Count -gt 0) { Kill-ByIds $wrapper }
  Start-Sleep -Seconds 2

  $sqx = @(Get-HermesTargets | Where-Object { $_.Name -match 'sqcli.exe|StrategyQuantX.exe|StrategyQuantX_ui.exe' } | ForEach-Object { $_.ProcessId })
  if ($sqx.Count -gt 0) { Kill-ByIds $sqx }
  Start-Sleep -Seconds 4
}

$final = Get-HermesTargets
$port = Get-NetTCPConnection -LocalPort 5050 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,State,OwningProcess

$result = [pscustomobject]@{
  rounds = $rounds
  final_count = @($final).Count
  final = @($final)
  port5050 = @($port)
}
$result | ConvertTo-Json -Depth 6
'''


def main():
    script_path = Path('/tmp/hermes_sqx_cleanup.ps1')
    script_path.write_text(PS_SCRIPT, encoding='utf-8')
    win_script = subprocess.check_output(['wslpath', '-w', str(script_path)], text=True).strip()
    out = subprocess.check_output([
        '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe',
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', win_script
    ], text=True, errors='replace')
    print(out)

if __name__ == '__main__':
    main()
