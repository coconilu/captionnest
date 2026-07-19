param(
    [Parameter(Mandatory = $true)]
    [string]$Tag,

    [string]$ContractRef = 'origin/main'
)

$ErrorActionPreference = 'Stop'
$WorkflowPath = '.github/workflows/release.yml'
$LegacyContractError = @"
$Tag uses a legacy or unknown immutable release workflow contract and cannot be rerun safely. Keep the tag unchanged and publish a new version.
"@.Trim()

function Get-WorkflowLines {
    param(
        [Parameter(Mandatory = $true)]
        [string]$GitRef
    )

    $Spec = "${GitRef}:$WorkflowPath"
    $Lines = @(& git @('show', $Spec) 2>$null)
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return ,$Lines
}

function Get-WorkflowBlob {
    param(
        [Parameter(Mandatory = $true)]
        [string]$GitRef
    )

    $Spec = "${GitRef}:$WorkflowPath"
    $Output = @(& git @('rev-parse', $Spec) 2>$null)
    if ($LASTEXITCODE -ne 0 -or $Output.Count -ne 1) {
        return $null
    }
    return $Output[0].Trim()
}

function Get-WorkflowDispatchSchema {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string[]]$Lines
    )

    $InWorkflowDispatch = $false
    $InInputs = $false
    $CurrentInput = $null
    $Inputs = @{}

    foreach ($Line in $Lines) {
        if (-not $InWorkflowDispatch) {
            if ($Line -eq '  workflow_dispatch:') {
                $InWorkflowDispatch = $true
            }
            continue
        }

        if (-not $InInputs) {
            if ($Line -eq '    inputs:') {
                $InInputs = $true
                continue
            }
            if ($Line -match '^\S') {
                break
            }
            continue
        }

        if ($Line -match '^      (?<name>[A-Za-z][A-Za-z0-9_-]*):\s*$') {
            $CurrentInput = $Matches.name
            if ($Inputs.ContainsKey($CurrentInput)) {
                return $null
            }
            $Inputs[$CurrentInput] = @{}
            continue
        }
        if ($Line -match '^        (?<property>required|type):\s*(?<value>\S+)\s*$') {
            if ($null -eq $CurrentInput) {
                return $null
            }
            $Value = $Matches.value.Trim("'`"").ToLowerInvariant()
            $Inputs[$CurrentInput][$Matches.property] = $Value
            continue
        }
        if ($Line -match '^(?:\S|    \S)') {
            break
        }
    }

    if (-not $InInputs) {
        return $null
    }
    return $Inputs
}

function Test-CurrentDispatchSchema {
    param(
        [AllowNull()]
        [hashtable]$Schema
    )

    if ($null -eq $Schema -or $Schema.Count -ne 2) {
        return $false
    }
    if (-not $Schema.ContainsKey('version') -or -not $Schema.ContainsKey('prerelease')) {
        return $false
    }
    return (
        $Schema.version.required -eq 'true' -and
        $Schema.version.type -eq 'string' -and
        $Schema.prerelease.required -eq 'true' -and
        $Schema.prerelease.type -eq 'boolean'
    )
}

$ContractLines = Get-WorkflowLines -GitRef $ContractRef
$ContractBlob = Get-WorkflowBlob -GitRef $ContractRef
if ($null -eq $ContractLines -or $null -eq $ContractBlob) {
    throw "Unable to read the current release workflow contract from $ContractRef."
}
$ContractSchema = Get-WorkflowDispatchSchema -Lines $ContractLines
if (-not (Test-CurrentDispatchSchema -Schema $ContractSchema)) {
    throw "Current release workflow at $ContractRef does not declare the required version/string and prerelease/boolean dispatch contract."
}

$TagLines = Get-WorkflowLines -GitRef $Tag
$TagBlob = Get-WorkflowBlob -GitRef $Tag
if ($null -eq $TagLines -or $null -eq $TagBlob) {
    throw $LegacyContractError
}
$TagSchema = Get-WorkflowDispatchSchema -Lines $TagLines
if (-not (Test-CurrentDispatchSchema -Schema $TagSchema)) {
    throw $LegacyContractError
}
if ($TagBlob -ne $ContractBlob) {
    throw $LegacyContractError
}

Write-Output "$Tag uses the current immutable release workflow contract."
