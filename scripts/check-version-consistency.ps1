param(
    [string]$ExpectedVersion
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Get-TomlVersion([string]$Path, [string]$Section) {
    $Content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $EscapedSection = [Regex]::Escape($Section)
    $SectionMatch = [Regex]::Match(
        $Content,
        "(?ms)^\[$EscapedSection\]\s*(.*?)(?=^\[|\z)"
    )
    if (-not $SectionMatch.Success) {
        throw "Missing [$Section] in $Path"
    }
    $VersionMatch = [Regex]::Match(
        $SectionMatch.Groups[1].Value,
        '(?m)^version\s*=\s*["'']([^"'']+)["'']\s*$'
    )
    if (-not $VersionMatch.Success) {
        throw "Missing version in [$Section] of $Path"
    }
    return $VersionMatch.Groups[1].Value
}

function Get-TomlPackageVersion([string]$Path, [string]$PackageName) {
    $Content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $Packages = [Regex]::Matches(
        $Content,
        '(?ms)^\[\[package\]\]\s*(.*?)(?=^\[\[package\]\]|\z)'
    )
    $Versions = @(
        $Packages | ForEach-Object {
            $Block = $_.Groups[1].Value
            $Name = [Regex]::Match($Block, '(?m)^name\s*=\s*["'']([^"'']+)["'']\s*$')
            if ($Name.Success -and $Name.Groups[1].Value -eq $PackageName) {
                $Version = [Regex]::Match(
                    $Block,
                    '(?m)^version\s*=\s*["'']([^"'']+)["'']\s*$'
                )
                if (-not $Version.Success) {
                    throw "Missing version for package $PackageName in $Path"
                }
                $Version.Groups[1].Value
            }
        }
    )
    if ($Versions.Count -ne 1) {
        throw "Expected one package $PackageName in $Path; found $($Versions.Count)"
    }
    return $Versions[0]
}

function Get-JsonVersion([string]$Path, [string]$Pattern, [string]$Label) {
    $Content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $Matches = [Regex]::Matches($Content, $Pattern)
    if ($Matches.Count -ne 1) {
        throw "Expected one $Label version in $Path; found $($Matches.Count)"
    }
    return $Matches[0].Groups['version'].Value
}

$PackageLockPath = Join-Path $Root 'apps\web\package-lock.json'
$PackageLockVersion = Get-JsonVersion `
    $PackageLockPath `
    '(?ms)\A\{\s*"name"\s*:\s*"captionnest-web"\s*,\s*"version"\s*:\s*"(?<version>[^"]+)"' `
    'WebLock'
$PackageLockRootVersion = Get-JsonVersion `
    $PackageLockPath `
    '(?ms)"packages"\s*:\s*\{\s*""\s*:\s*\{\s*"name"\s*:\s*"captionnest-web"\s*,\s*"version"\s*:\s*"(?<version>[^"]+)"' `
    'WebLockRoot'
$Versions = [ordered]@{
    Python = Get-TomlVersion (Join-Path $Root 'apps\sidecar\pyproject.toml') 'project'
    PythonLock = Get-TomlPackageVersion (Join-Path $Root 'apps\sidecar\uv.lock') 'captionnest'
    Web = (Get-Content -LiteralPath (Join-Path $Root 'apps\web\package.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
    WebLock = $PackageLockVersion
    WebLockRoot = $PackageLockRootVersion
    Tauri = (Get-Content -LiteralPath (Join-Path $Root 'apps\desktop\tauri.conf.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
    Cargo = Get-TomlVersion (Join-Path $Root 'apps\desktop\Cargo.toml') 'package'
    CargoLock = Get-TomlPackageVersion (Join-Path $Root 'apps\desktop\Cargo.lock') 'captionnest-desktop'
}

$UniqueVersions = @($Versions.Values | Sort-Object -Unique)
if ($UniqueVersions.Count -ne 1) {
    throw "Version mismatch: $($Versions | ConvertTo-Json -Compress)"
}

$Version = $UniqueVersions[0]
if ($ExpectedVersion -and $Version -ne $ExpectedVersion) {
    throw "Release tag version $ExpectedVersion does not match project version $Version"
}

Write-Host "Version consistency check passed: $Version" -ForegroundColor Green
