param(
    [string]$PythonExecutable = '',
    [string]$WheelDirectory = 'tooling\packaging\dist\media-wheel'
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

function Resolve-WorkspacePath([string]$Path) {
    $Candidate = if ([IO.Path]::IsPathFullyQualified($Path)) {
        $Path
    } else {
        Join-Path $Root $Path
    }
    $Resolved = [IO.Path]::GetFullPath($Candidate)
    $RootPrefix = $Root.TrimEnd('\') + '\'
    if (-not $Resolved.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to read a path outside the workspace: $Resolved"
    }
    return $Resolved
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required to install the CaptionNest media wheel.'
}
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $Root 'apps\sidecar\.venv\Scripts\python.exe'
}
$PythonExecutable = if ([IO.Path]::IsPathFullyQualified($PythonExecutable)) {
    [IO.Path]::GetFullPath($PythonExecutable)
} else {
    [IO.Path]::GetFullPath((Join-Path $Root $PythonExecutable))
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable not found: $PythonExecutable"
}

$WheelRoot = Resolve-WorkspacePath $WheelDirectory
if (-not (Test-Path -LiteralPath $WheelRoot -PathType Container)) {
    throw "Media wheel directory not found: $WheelRoot"
}
$Wheels = @(Get-ChildItem -LiteralPath $WheelRoot -Filter 'av-*.whl' -File)
if ($Wheels.Count -ne 1) {
    throw "Expected exactly one cached PyAV wheel; found $($Wheels.Count)."
}
$Wheel = $Wheels[0]
$ProvenancePath = Join-Path $WheelRoot 'MEDIA_WHEEL_PROVENANCE.json'
if (-not (Test-Path -LiteralPath $ProvenancePath -PathType Leaf)) {
    throw "Media wheel provenance not found: $ProvenancePath"
}

try {
    $Provenance = Get-Content -LiteralPath $ProvenancePath -Raw | ConvertFrom-Json
} catch {
    throw "Unable to parse media wheel provenance: $($_.Exception.Message)"
}
if ($Provenance.schema -ne 1) {
    throw "Unsupported media wheel provenance schema: $($Provenance.schema)"
}
if ($Provenance.wheel -ne $Wheel.Name) {
    throw "Provenance references $($Provenance.wheel), but cache contains $($Wheel.Name)."
}
$ExpectedHash = ([string]$Provenance.wheel_sha256).ToLowerInvariant()
$ActualHash = (Get-FileHash -LiteralPath $Wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not $ExpectedHash -or $ExpectedHash -ne $ActualHash) {
    throw "Cached media wheel hash mismatch. Expected $ExpectedHash, got $ActualHash."
}

$InstallArguments = @(
    'pip', 'install', '--python', $PythonExecutable,
    '--reinstall', '--no-deps', $Wheel.FullName
)
& uv @InstallArguments
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to install the cached LGPL media wheel.'
}

& (Join-Path $PSScriptRoot 'check-media-license.ps1') `
    -PythonExecutable $PythonExecutable

Write-Host "Verified LGPL media wheel: $($Wheel.FullName)" -ForegroundColor Green
Write-Host "Wheel SHA-256: $ActualHash" -ForegroundColor Green
