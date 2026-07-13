$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required. Install uv first.'
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'npm is required. Install Node.js first.'
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw 'FFmpeg is required and must be available on PATH.'
}

uv sync --extra asr --extra dev
npm --prefix web install
npm --prefix web run build

Write-Host 'SubLingo Local setup completed. Run .\scripts\dev.ps1 for development.' -ForegroundColor Green
