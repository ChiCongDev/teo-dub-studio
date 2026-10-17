$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -in @('127.0.0.1', '0.0.0.0', '::', '::1') } | Select-Object -First 1
if ($null -eq $listener) { Write-Output 'null'; exit 0 }
$serviceProcess = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$listener.OwningProcess)
[pscustomobject]@{ pid = [int]$serviceProcess.ProcessId; executable = $serviceProcess.ExecutablePath; command = $serviceProcess.CommandLine } | ConvertTo-Json -Compress
