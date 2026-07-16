$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path (Join-Path $Root 'apps\web\dist\index.html'))) {
    npm --prefix apps/web run build
}

$Url = 'http://127.0.0.1:8765'
Write-Host "Starting CaptionNest: $Url" -ForegroundColor Cyan
Start-Process $Url
uv run --project apps/sidecar --extra asr uvicorn sublingo_local.app:app --host 127.0.0.1 --port 8765
