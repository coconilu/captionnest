$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DataDir = Join-Path $Root 'data'
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
Set-Location $Root

& npm --prefix apps/web run build
if ($LASTEXITCODE -ne 0) { throw 'CaptionNest web build failed.' }

& (Join-Path $PSScriptRoot 'stop-local-services.ps1') -Scope All

$env:CAPTIONNEST_DATA_DIR = $DataDir
$StdoutLog = Join-Path $DataDir 'captionnest-serve.stdout.log'
$StderrLog = Join-Path $DataDir 'captionnest-serve.stderr.log'
$Api = Start-Process uv -ArgumentList @(
    'run', '--project', 'apps/sidecar', '--extra', 'asr',
    'uvicorn', 'sublingo_local.app:app',
    '--host', '127.0.0.1', '--port', '8765'
) -WorkingDirectory $Root `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog

$deadline = (Get-Date).AddSeconds(30)
$health = $null
do {
    Start-Sleep -Milliseconds 300
    if ($Api.HasExited) {
        $details = if (Test-Path -LiteralPath $StderrLog) {
            Get-Content -Raw -Encoding utf8 $StderrLog
        }
        else {
            ''
        }
        throw "CaptionNest exited before it became ready (exit code $($Api.ExitCode)). $details"
    }
    try {
        $health = Invoke-RestMethod `
            -Uri 'http://127.0.0.1:8765/api/health' `
            -TimeoutSec 2 `
            -ErrorAction Stop
    }
    catch {
        $health = $null
    }
} while (-not $health -and (Get-Date) -lt $deadline)

if (-not $health) { throw 'CaptionNest did not become ready within 30 seconds.' }

Write-Host "CaptionNest is running at http://127.0.0.1:8765/ (launcher PID $($Api.Id))." `
    -ForegroundColor Green
