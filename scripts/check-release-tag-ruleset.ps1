[CmdletBinding(DefaultParameterSetName = 'GitHub')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'GitHub')]
    [string]$Repository,

    [Parameter(Mandatory = $true, ParameterSetName = 'Fixture')]
    [string]$RulesetDetailsPath,

    [string]$SummaryPath,

    [string]$Phase = 'Release validation'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-RulesetDetails {
    if ($PSCmdlet.ParameterSetName -eq 'Fixture') {
        $Fixture = Get-Content -LiteralPath $RulesetDetailsPath -Raw | ConvertFrom-Json
        return @($Fixture)
    }

    $ListArgs = @('api', "repos/$Repository/rulesets")
    $ListJson = & gh @ListArgs
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to query repository tag rulesets.'
    }

    $Details = @()
    foreach ($Summary in @($ListJson | ConvertFrom-Json)) {
        if ($Summary.target -ne 'tag' -or $Summary.enforcement -ne 'active') {
            continue
        }
        $DetailArgs = @('api', "repos/$Repository/rulesets/$($Summary.id)")
        $DetailJson = & gh @DetailArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect tag ruleset $($Summary.id)."
        }
        $Details += $DetailJson | ConvertFrom-Json
    }
    return $Details
}

$StructurallyProtected = @()
$AdminVerified = @()
$AdminVisibilityLimited = @()

foreach ($Ruleset in @(Get-RulesetDetails)) {
    $RuleTypes = @($Ruleset.rules.type)
    $IncludesReleaseTags = @($Ruleset.conditions.ref_name.include) -contains 'refs/tags/v*'
    $HasNoExclusions = @($Ruleset.conditions.ref_name.exclude).Count -eq 0
    if (
        $Ruleset.target -ne 'tag' -or
        $Ruleset.enforcement -ne 'active' -or
        -not $IncludesReleaseTags -or
        -not $HasNoExclusions -or
        $RuleTypes -notcontains 'update' -or
        $RuleTypes -notcontains 'deletion'
    ) {
        continue
    }

    $BypassProperty = $Ruleset.PSObject.Properties['bypass_actors']
    $CurrentUserProperty = $Ruleset.PSObject.Properties['current_user_can_bypass']
    $BypassVisible = $null -ne $BypassProperty
    $CurrentUserVisible = $null -ne $CurrentUserProperty
    $CurrentUserSafe = (
        $CurrentUserVisible -and
        $CurrentUserProperty.Value -is [string] -and
        $CurrentUserProperty.Value -ceq 'never'
    )

    if ($BypassVisible) {
        if (-not $CurrentUserVisible) {
            throw 'RULESET_ADMIN_VISIBILITY_UNSAFE: current_user_can_bypass is missing while bypass_actors is visible.'
        }
        if (
            $null -eq $BypassProperty.Value -or
            @($BypassProperty.Value).Count -ne 0 -or
            -not $CurrentUserSafe
        ) {
            continue
        }
        $AdminVerified += $Ruleset
    } else {
        if ($CurrentUserVisible -and -not $CurrentUserSafe) {
            throw 'RULESET_ADMIN_VISIBILITY_UNSAFE: current_user_can_bypass must be never when bypass_actors is hidden.'
        }
        $AdminVisibilityLimited += $Ruleset
    }

    $StructurallyProtected += $Ruleset
}

if ($StructurallyProtected.Count -eq 0) {
    throw 'RULESET_PROTECTION_MISSING: An active refs/tags/v* ruleset must prohibit update and deletion; any visible bypass fields must be empty/never.'
}

if ($AdminVerified.Count -gt 0) {
    $VisibilityMessage = 'No-bypass fields were visible and verified: bypass_actors=[] and current_user_can_bypass=never.'
    Write-Host $VisibilityMessage
} else {
    $VisibilityMessage = 'bypass_actors was not visible to this GITHUB_TOKEN. An empty administrator bypass list remains an external administrator prerequisite and was not verified by this workflow; current_user_can_bypass, when visible, was verified as never.'
    Write-Warning $VisibilityMessage
}

Write-Host 'Verified active tag ruleset structure: refs/tags/v*, update, and deletion.'

if ($SummaryPath) {
    @"
## Tag ruleset prerequisite - $Phase

- Workflow-visible structure: verified active tag ruleset for ``refs/tags/v*`` with update and deletion restrictions.
- Admin-only bypass state: $VisibilityMessage
"@ | Add-Content -LiteralPath $SummaryPath
}
