$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required. Install uv first.'
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'npm is required. Install Node.js first.'
}

uv sync --project apps/sidecar --extra asr --extra dev
npm --prefix apps/web install
npm --prefix apps/web run build

Write-Host 'CaptionNest setup completed. PyAV provides media decoding; system FFmpeg is not required.' -ForegroundColor Green
Write-Host 'Run .\scripts\dev.ps1 for development.' -ForegroundColor Green
