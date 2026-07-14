param(
    [ValidateSet('x86_64-pc-windows-msvc')]
    [string]$TargetTriple = 'x86_64-pc-windows-msvc'
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

foreach ($Command in @('uv', 'npm', 'cargo', 'rustup')) {
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "Desktop development requires $Command. See docs/development.md."
    }
}

$Tauri = Join-Path $Root 'web\node_modules\.bin\tauri.cmd'
if (-not (Test-Path -LiteralPath $Tauri -PathType Leaf)) {
    & npm --prefix web install
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install frontend dependencies.' }
}

& rustup target add $TargetTriple
if ($LASTEXITCODE -ne 0) { throw "Failed to install Rust target: $TargetTriple" }

& (Join-Path $PSScriptRoot 'build-sidecar.ps1') -TargetTriple $TargetTriple

& $Tauri dev --config 'src-tauri\tauri.conf.json' --target $TargetTriple
if ($LASTEXITCODE -ne 0) { throw 'Tauri desktop development process exited with an error.' }
