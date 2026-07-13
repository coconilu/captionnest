$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path (Join-Path $Root 'web\dist\index.html'))) {
    npm --prefix web run build
}

$Url = 'http://127.0.0.1:8765'
Write-Host "Starting SubLingo Local: $Url" -ForegroundColor Cyan
Start-Process $Url
uv run --extra asr uvicorn sublingo_local.app:app --host 127.0.0.1 --port 8765
