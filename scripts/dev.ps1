$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Api = Start-Process uv -ArgumentList @(
    'run', '--extra', 'asr', 'uvicorn', 'sublingo_local.app:app',
    '--host', '127.0.0.1', '--port', '8765', '--reload'
) -WorkingDirectory $Root -PassThru -WindowStyle Hidden

try {
    npm --prefix web run dev
}
finally {
    if (-not $Api.HasExited) {
        Stop-Process -Id $Api.Id -Force
    }
}
