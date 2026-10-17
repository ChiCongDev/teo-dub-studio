$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$webData = Join-Path $env:LOCALAPPDATA 'TeoDubWeb'
$rootFile = Join-Path $PSScriptRoot 'web-data-root.txt'
if (Test-Path -LiteralPath $rootFile) {
    $webData = (Get-Content -Raw -LiteralPath $rootFile -Encoding utf8).Trim()
}
$pythonExe = Join-Path $projectDir '.venv-web\Scripts\python.exe'
New-Item -ItemType Directory -Path $webData -Force | Out-Null
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 2
} catch {
    $health = $null
}
if ($null -eq $health -or $health.status -ne 'ok') {
    Push-Location -LiteralPath $projectDir
    try {
        & $pythonExe -X utf8 -B -m teodub_web.background --root $webData
        if ($LASTEXITCODE -ne 0) { throw 'Tèo Web chưa mở được. Xem thông báo trên và nhật ký trên máy.' }
    } finally {
        Pop-Location
    }
}
Start-Process 'http://127.0.0.1:8765/'
