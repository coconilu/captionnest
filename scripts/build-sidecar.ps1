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

if (-not $PythonExecutable -and -not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required to build the sidecar.'
}
if ($PythonExecutable) {
    $PythonExecutable = [IO.Path]::GetFullPath($PythonExecutable)
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        throw "Python executable not found: $PythonExecutable"
    }
}

$DistRoot = Assert-PathInsideWorkspace (Join-Path $Root 'packaging\dist')
$WorkRoot = Assert-PathInsideWorkspace (Join-Path $Root 'packaging\build')
$SidecarDist = Join-Path $DistRoot 'captionnest-sidecar'
$DestinationRoot = Assert-PathInsideWorkspace (Join-Path $Root 'src-tauri\binaries')
$TargetExecutable = Join-Path $DestinationRoot "captionnest-sidecar-$TargetTriple.exe"
$TargetInternal = Join-Path $DestinationRoot '_internal'

& (Join-Path $PSScriptRoot 'check-media-license.ps1') `
    -OutputPath 'packaging\dist\FFMPEG_BUILD_INFO.txt' `
    -PythonExecutable $PythonExecutable

$PyInstallerArguments = @(
    '--noconfirm', '--clean',
    '--distpath', $DistRoot,
    '--workpath', $WorkRoot,
    (Join-Path $Root 'packaging\captionnest-sidecar.spec')
)
if ($PythonExecutable) {
    & $PythonExecutable -m PyInstaller @PyInstallerArguments
} else {
    & uv run --extra asr --extra desktop pyinstaller @PyInstallerArguments
}
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller sidecar build failed.'
}

$BuiltExecutable = Join-Path $SidecarDist 'captionnest-sidecar.exe'
$BuiltInternal = Join-Path $SidecarDist '_internal'
if (-not (Test-Path -LiteralPath $BuiltExecutable -PathType Leaf)) {
    throw "Sidecar executable not found: $BuiltExecutable"
}
if (-not (Test-Path -LiteralPath $BuiltInternal -PathType Container)) {
    throw "PyInstaller onedir dependency directory not found: $BuiltInternal"
}

New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
if (Test-Path -LiteralPath $TargetExecutable) {
    Remove-Item -LiteralPath $TargetExecutable -Force
}
if (Test-Path -LiteralPath $TargetInternal) {
    $null = Assert-PathInsideWorkspace $TargetInternal
    Remove-Item -LiteralPath $TargetInternal -Recurse -Force
}
Copy-Item -LiteralPath $BuiltExecutable -Destination $TargetExecutable
Copy-Item -LiteralPath $BuiltInternal -Destination $TargetInternal -Recurse

Write-Host "Sidecar ready: $TargetExecutable" -ForegroundColor Green
