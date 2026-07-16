param(
    [string]$OutputPath,
    [switch]$AllowGpl,
    [string]$PythonExecutable = ''
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

$PythonCode = @'
import json
from importlib import metadata
from pathlib import Path

import av
from av import _core


package_dir = Path(av.__file__).resolve().parent
site_packages = package_dir.parent
candidate_dirs = {package_dir, site_packages / 'av.libs'}
candidate_dirs.update(site_packages.glob('av*.libs'))

bundled_dlls = set()
try:
    distribution = metadata.distribution('av')
    for item in distribution.files or []:
        path = Path(distribution.locate_file(item))
        if path.suffix.lower() == '.dll' and path.is_file():
            bundled_dlls.add(path.name)
except metadata.PackageNotFoundError:
    pass

for directory in candidate_dirs:
    if not directory.is_dir():
        continue
    for path in directory.rglob('*'):
        if path.suffix.lower() == '.dll' and path.is_file():
            bundled_dlls.add(path.name)

print(
    json.dumps(
        {
            'pyav': av.__version__,
            'libraries': _core.library_meta,
            'bundled_dlls': sorted(bundled_dlls, key=str.casefold),
        },
        default=str,
    )
)
'@

$Arguments = @('-c', $PythonCode)
if ($PythonExecutable) {
    $PythonExecutable = [IO.Path]::GetFullPath($PythonExecutable)
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        throw "Python executable not found: $PythonExecutable"
    }
    $Json = & $PythonExecutable @Arguments
} else {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'uv is required when -PythonExecutable is not provided.'
    }
    $Json = & uv run --project apps/sidecar --extra asr python @Arguments
}
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect the PyAV/FFmpeg build metadata.'
}

$Metadata = $Json | ConvertFrom-Json
$Libraries = @($Metadata.libraries.PSObject.Properties.Value)
$Licenses = @($Libraries | ForEach-Object { $_.license } | Sort-Object -Unique)
$Configurations = @($Libraries | ForEach-Object { $_.configuration } | Sort-Object -Unique)
$BundledDlls = @($Metadata.bundled_dlls | Sort-Object -Unique)
$ConfigurationText = $Configurations -join ' '
$KnownGplConfigPattern = '(?i)--enable-lib(?:x264|x265|xvid|vidstab|rubberband|xavs2?)(?:\s|=|$)'
$KnownGplDllPattern = '(?i)^(?:lib)?(?:x264|x265|xvid(?:core)?|vidstab|rubberband|xavs2?)(?:[-_.].*)?\.dll$'
$KnownGplConfigMatches = @(
    [regex]::Matches($ConfigurationText, $KnownGplConfigPattern) |
        ForEach-Object { $_.Value.Trim() } |
        Sort-Object -Unique
)
$KnownGplDllMatches = @($BundledDlls | Where-Object { $_ -match $KnownGplDllPattern })
$ReportedGplLicenses = @($Licenses | Where-Object { $_ -match '(?i)\bGPL\b' })
$GplIndicators = @()

if ($Configurations -match '(?i)--enable-gpl(?:\s|=|$)') {
    $GplIndicators += 'FFmpeg configuration: --enable-gpl'
}
foreach ($License in $ReportedGplLicenses) {
    $GplIndicators += "FFmpeg reported license: $License"
}
foreach ($Configuration in $KnownGplConfigMatches) {
    $GplIndicators += "known GPL library configuration: $Configuration"
}
foreach ($Dll in $KnownGplDllMatches) {
    $GplIndicators += "bundled known GPL library DLL: $Dll"
}
$GplIndicators = @($GplIndicators | Sort-Object -Unique)
$HasNonfree = [bool]($Configurations -match '(?i)--enable-nonfree(?:\s|=|$)')
$HasGplIndicators = $GplIndicators.Count -gt 0
$HasIncompleteMetadata = $Licenses.Count -eq 0 -or $Configurations.Count -eq 0
$HasMissingDllInventory = $env:OS -eq 'Windows_NT' -and $BundledDlls.Count -eq 0
$OverrideStatus = if ($AllowGpl) {
    'ENABLED - publisher accepted responsibility for a completed GPL release review'
} else {
    'disabled'
}
$GateDecision = if ($HasNonfree) {
    'REJECTED - nonfree FFmpeg configuration is not redistributable'
} elseif ($HasIncompleteMetadata) {
    'REJECTED - PyAV did not expose complete FFmpeg license metadata'
} elseif ($HasMissingDllInventory) {
    'REJECTED - no bundled PyAV DLLs could be inventoried on Windows'
} elseif ($HasGplIndicators -and -not $AllowGpl) {
    'REJECTED - GPL indicators require an explicit completed release review'
} elseif ($HasGplIndicators) {
    'OVERRIDDEN - GPL indicators accepted by explicit publisher review'
} else {
    'PASSED - no configured or bundled GPL indicators detected'
}

$Lines = @(
    'CaptionNest bundled media runtime',
    "Generated: $([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))",
    "PyAV: $($Metadata.pyav)",
    "FFmpeg license: $($Licenses -join '; ')",
    "FFmpeg configuration: $($Configurations -join ' | ')",
    "Bundled DLL count: $($BundledDlls.Count)",
    "Bundled DLLs: $($BundledDlls -join '; ')",
    "GPL indicators: $($GplIndicators -join '; ')",
    "GPL review override: $OverrideStatus",
    "License gate decision: $GateDecision"
)

if ($OutputPath) {
    $ResolvedOutput = [IO.Path]::GetFullPath((Join-Path $Root $OutputPath))
    $OutputDirectory = Split-Path -Parent $ResolvedOutput
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    $Lines | Set-Content -LiteralPath $ResolvedOutput -Encoding UTF8
    Write-Host "Media license evidence written to $ResolvedOutput" -ForegroundColor Green
}

$Lines

if ($HasNonfree) {
    throw 'FFmpeg uses --enable-nonfree and cannot be redistributed.'
}
if ($HasIncompleteMetadata) {
    throw 'PyAV did not expose complete FFmpeg license/configuration metadata; refusing to publish.'
}
if ($HasMissingDllInventory) {
    throw 'No bundled PyAV DLLs could be inventoried on Windows; refusing to publish.'
}
if ($HasGplIndicators -and -not $AllowGpl) {
    $Summary = $GplIndicators -join '; '
    throw "GPL indicators detected: $Summary. Use an LGPL-only media wheel, or complete the GPL release obligations before explicitly using -AllowGpl."
}
