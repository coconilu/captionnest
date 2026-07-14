param(
    [Parameter(Mandatory = $true)]
    [string]$VcpkgRoot,
    [string]$PythonExecutable = '',
    [string]$OutputDirectory = 'packaging\dist\media-wheel'
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

$PyAvVersion = '18.0.0'
$PyAvSourceUrl = 'https://files.pythonhosted.org/packages/ae/a4/570a5a35c8638aba01e739925846c35fdd6b0756a15526766d0a4dd3b7df/av-18.0.0.tar.gz'
$PyAvSourceSha256 = '4ef7e72c3d3a872584a1215173b16e0226811037f40dcdbf75992631098df1ba'
$VcpkgBaseline = 'db4723bd0a99eab031f1a3dee4336dca43049c87'
$FfmpegVersion = '8.1.2'
$Triplet = 'x64-windows'

function Resolve-WorkspacePath([string]$Path) {
    $Candidate = if ([IO.Path]::IsPathFullyQualified($Path)) {
        $Path
    } else {
        Join-Path $Root $Path
    }
    $Resolved = [IO.Path]::GetFullPath($Candidate)
    $RootPrefix = $Root.TrimEnd('\') + '\'
    if (-not $Resolved.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the workspace: $Resolved"
    }
    return $Resolved
}

function Invoke-Checked(
    [string]$FilePath,
    [string[]]$ArgumentList,
    [string]$FailureMessage
) {
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'The CaptionNest media wheel supports Windows x64 only.'
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required to prepare the PyAV build environment.'
}

if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $Root '.venv\Scripts\python.exe'
}
$PythonExecutable = if ([IO.Path]::IsPathFullyQualified($PythonExecutable)) {
    [IO.Path]::GetFullPath($PythonExecutable)
} else {
    [IO.Path]::GetFullPath((Join-Path $Root $PythonExecutable))
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable not found: $PythonExecutable"
}

$VcpkgRoot = if ([IO.Path]::IsPathFullyQualified($VcpkgRoot)) {
    [IO.Path]::GetFullPath($VcpkgRoot)
} else {
    [IO.Path]::GetFullPath((Join-Path $Root $VcpkgRoot))
}
$VcpkgExecutable = Join-Path $VcpkgRoot 'vcpkg.exe'
if (-not (Test-Path -LiteralPath $VcpkgExecutable -PathType Leaf)) {
    throw "Bootstrapped vcpkg executable not found: $VcpkgExecutable"
}

$ManifestRoot = Join-Path $Root 'packaging\media-runtime'
$WorkRoot = Resolve-WorkspacePath 'packaging\build\media-wheel'
$InstallRoot = Resolve-WorkspacePath 'packaging\build\media-runtime-installed'
$OutputRoot = Resolve-WorkspacePath $OutputDirectory
$SourceArchive = Join-Path $WorkRoot "av-$PyAvVersion.tar.gz"
$SourceRoot = Join-Path $WorkRoot "av-$PyAvVersion"
$RawWheelRoot = Join-Path $WorkRoot 'raw-wheel'
$VcpkgBin = Join-Path $InstallRoot "$Triplet\bin"

foreach ($Directory in @($WorkRoot, $InstallRoot, $OutputRoot)) {
    if (Test-Path -LiteralPath $Directory) {
        $null = Resolve-WorkspacePath $Directory
        Remove-Item -LiteralPath $Directory -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
}
New-Item -ItemType Directory -Path $RawWheelRoot -Force | Out-Null

$VcpkgArguments = @(
    'install',
    "--x-manifest-root=$ManifestRoot",
    "--x-install-root=$InstallRoot",
    "--triplet=$Triplet",
    '--clean-after-build'
)
Invoke-Checked $VcpkgExecutable $VcpkgArguments 'vcpkg failed to build the LGPL FFmpeg runtime.'

if (-not (Test-Path -LiteralPath $VcpkgBin -PathType Container)) {
    throw "vcpkg runtime directory not found: $VcpkgBin"
}

$BuildDependencies = @(
    'pip', 'install', '--python', $PythonExecutable,
    'Cython==3.2.8',
    'setuptools==83.0.0',
    'wheel==0.47.0',
    'delvewheel==1.13.0'
)
Invoke-Checked 'uv' $BuildDependencies 'Unable to install the pinned PyAV build dependencies.'

Invoke-WebRequest -Uri $PyAvSourceUrl -OutFile $SourceArchive -UseBasicParsing
$ActualSourceHash = (Get-FileHash -LiteralPath $SourceArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualSourceHash -ne $PyAvSourceSha256) {
    throw "PyAV source hash mismatch. Expected $PyAvSourceSha256, got $ActualSourceHash."
}

Invoke-Checked 'tar.exe' @('-xzf', $SourceArchive, '-C', $WorkRoot) 'Unable to extract PyAV source.'
if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
    throw "Extracted PyAV source directory not found: $SourceRoot"
}

$OriginalPath = $env:PATH
try {
    $env:PATH = "$VcpkgBin;$OriginalPath"
    Push-Location $SourceRoot
    try {
        $BuildArguments = @(
            'setup.py',
            'bdist_wheel',
            '--dist-dir', $RawWheelRoot,
            "--ffmpeg-dir=$InstallRoot\$Triplet"
        )
        Invoke-Checked $PythonExecutable $BuildArguments 'PyAV source wheel build failed.'
    } finally {
        Pop-Location
    }
} finally {
    $env:PATH = $OriginalPath
}

$RawWheels = @(Get-ChildItem -LiteralPath $RawWheelRoot -Filter 'av-*.whl' -File)
if ($RawWheels.Count -ne 1) {
    throw "Expected exactly one raw PyAV wheel; found $($RawWheels.Count)."
}

$RepairArguments = @(
    '-m', 'delvewheel', 'repair',
    '--add-path', $VcpkgBin,
    '--wheel-dir', $OutputRoot,
    $RawWheels[0].FullName
)
Invoke-Checked $PythonExecutable $RepairArguments 'delvewheel failed to vendor FFmpeg DLLs.'

$RepairedWheels = @(Get-ChildItem -LiteralPath $OutputRoot -Filter 'av-*.whl' -File)
if ($RepairedWheels.Count -ne 1) {
    throw "Expected exactly one repaired PyAV wheel; found $($RepairedWheels.Count)."
}
$Wheel = $RepairedWheels[0]
$WheelHash = (Get-FileHash -LiteralPath $Wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()

$InstallArguments = @(
    'pip', 'install', '--python', $PythonExecutable,
    '--reinstall', '--no-deps', $Wheel.FullName
)
Invoke-Checked 'uv' $InstallArguments 'Unable to install the repaired PyAV wheel.'

$VerifyCode = @'
from av import _core

licenses = {item.get("license", "") for item in _core.library_meta.values()}
configurations = " ".join(
    item.get("configuration", "") for item in _core.library_meta.values()
)
print("FFmpeg licenses:", "; ".join(sorted(licenses)))
if "--enable-gpl" in configurations or "--enable-nonfree" in configurations:
    raise SystemExit("The custom FFmpeg build unexpectedly enabled GPL or nonfree components.")
'@
Invoke-Checked $PythonExecutable @('-c', $VerifyCode) 'The repaired PyAV wheel failed verification.'

$Provenance = [ordered]@{
    schema = 1
    generated_at = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    wheel = $Wheel.Name
    wheel_sha256 = $WheelHash
    pyav_version = $PyAvVersion
    pyav_source_url = $PyAvSourceUrl
    pyav_source_sha256 = $PyAvSourceSha256
    ffmpeg_version = $FfmpegVersion
    vcpkg_baseline = $VcpkgBaseline
    vcpkg_triplet = $Triplet
    vcpkg_manifest = 'packaging/media-runtime/vcpkg.json'
}
$ProvenancePath = Join-Path $OutputRoot 'MEDIA_WHEEL_PROVENANCE.json'
$Provenance | ConvertTo-Json | Set-Content -LiteralPath $ProvenancePath -Encoding UTF8

Write-Host "LGPL PyAV wheel: $($Wheel.FullName)" -ForegroundColor Green
Write-Host "Wheel SHA-256:  $WheelHash" -ForegroundColor Green
Write-Host "Provenance:     $ProvenancePath" -ForegroundColor Green
