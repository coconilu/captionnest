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

$Versions = [ordered]@{
    Python = Get-TomlVersion (Join-Path $Root 'pyproject.toml') 'project'
    Web = (Get-Content -LiteralPath (Join-Path $Root 'web\package.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
    Tauri = (Get-Content -LiteralPath (Join-Path $Root 'src-tauri\tauri.conf.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
    Cargo = Get-TomlVersion (Join-Path $Root 'src-tauri\Cargo.toml') 'package'
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
