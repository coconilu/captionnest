param(
    [ValidateSet('x86_64-pc-windows-msvc')]
    [string]$TargetTriple = 'x86_64-pc-windows-msvc',
    [string]$PythonExecutable = ''
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

function Assert-PathInsideWorkspace([string]$Path) {
    $Resolved = [IO.Path]::GetFullPath($Path)
    $RootPrefix = $Root.TrimEnd('\') + '\'
    if (-not $Resolved.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the workspace: $Resolved"
    }
    return $Resolved
}

foreach ($Command in @('uv', 'npm', 'cargo', 'rustup')) {
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "Desktop build requires $Command. See docs/development.md."
    }
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'M1-M5 desktop builds support Windows x64 only.'
}

if (-not (Test-Path (Join-Path $Root 'apps\web\node_modules\.bin\tauri.cmd'))) {
    & npm --prefix apps/web install
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install frontend dependencies.' }
}

& rustup target add $TargetTriple
if ($LASTEXITCODE -ne 0) { throw "Failed to install Rust target: $TargetTriple" }

foreach ($Icon in @('32x32.png', '128x128.png', '128x128@2x.png', 'icon.ico')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root "apps\desktop\icons\$Icon") -PathType Leaf)) {
        throw "Missing application icon: apps\desktop\icons\$Icon"
    }
}

& (Join-Path $PSScriptRoot 'check-version-consistency.ps1')

& (Join-Path $PSScriptRoot 'build-sidecar.ps1') `
    -TargetTriple $TargetTriple `
    -PythonExecutable $PythonExecutable

$NsisDirectory = Assert-PathInsideWorkspace (
    Join-Path $Root "apps\desktop\target\$TargetTriple\release\bundle\nsis"
)
if (Test-Path -LiteralPath $NsisDirectory -PathType Container) {
    Remove-Item -LiteralPath $NsisDirectory -Recurse -Force
}

$Tauri = Join-Path $Root 'apps\web\node_modules\.bin\tauri.cmd'
& $Tauri build --config 'apps\desktop\tauri.conf.json' --target $TargetTriple
if ($LASTEXITCODE -ne 0) { throw 'Tauri Windows bundle build failed.' }

$Installers = @(Get-ChildItem -LiteralPath $NsisDirectory -Filter 'CaptionNest_*_x64-setup.exe' -File)
if ($Installers.Count -ne 1) {
    throw "Expected exactly one CaptionNest x64 NSIS installer in $NsisDirectory; found $($Installers.Count)."
}

foreach ($Installer in $Installers) {
    $Hash = (Get-FileHash -LiteralPath $Installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $ChecksumPath = "$($Installer.FullName).sha256"
    "$Hash  $($Installer.Name)" | Set-Content -LiteralPath $ChecksumPath -Encoding ASCII
    Write-Host "Installer: $($Installer.FullName)" -ForegroundColor Green
    Write-Host "Checksum:  $ChecksumPath" -ForegroundColor Green
}
