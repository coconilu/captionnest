param(
    [Parameter(Mandatory = $true)]
    [string]$OldInstallerPath,
    [Parameter(Mandatory = $true)]
    [string]$CurrentInstallerPath,
    [Parameter(Mandatory = $true)]
    [string]$UpgradeInstallerPath,
    [string]$ExpectedVersion = '0.2.8',
    [string]$UpgradeExpectedVersion = '0.2.9',
    [string]$OldInstallerSha256 = '8c8a48778c420a99a342e79974d3edb6e315858dd13dbcfe7546ceb0bcc176d6'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'This destructive installer lifecycle test is restricted to a disposable GitHub-hosted runner.'
}
if (-not $env:RUNNER_TEMP -or -not $env:LOCALAPPDATA) {
    throw 'RUNNER_TEMP and LOCALAPPDATA must be available.'
}

$OldInstaller = (Resolve-Path -LiteralPath $OldInstallerPath).Path
$CurrentInstaller = (Resolve-Path -LiteralPath $CurrentInstallerPath).Path
$UpgradeInstaller = (Resolve-Path -LiteralPath $UpgradeInstallerPath).Path
$RunnerTemp = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\')
$EvidenceRoot = Join-Path $RunnerTemp 'captionnest-installer-lifecycle'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'CaptionNest'
$AppDataRoot = Join-Path $env:LOCALAPPDATA 'io.github.coconilu.captionnest'
$ModelsRoot = Join-Path $AppDataRoot 'models'
$MarkerPath = Join-Path $ModelsRoot 'small\model.bin'
$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CaptionNest'
$ManufacturerKey = 'HKCU:\Software\CaptionNest contributors\CaptionNest'
$script:OwnedProcesses = @()

function Assert-DisposableRunnerState {
    $ResolvedEvidence = [IO.Path]::GetFullPath($EvidenceRoot)
    if (-not $ResolvedEvidence.StartsWith("$RunnerTemp\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Evidence path escaped RUNNER_TEMP: $ResolvedEvidence"
    }
    foreach ($Path in @($InstallRoot, $AppDataRoot, $UninstallKey, $ManufacturerKey)) {
        if (Test-Path -LiteralPath $Path) {
            throw "Refusing to run because CaptionNest state already exists: $Path"
        }
    }
    $Existing = @(Get-Process -Name 'captionnest', 'captionnest-sidecar' -ErrorAction SilentlyContinue)
    if ($Existing.Count -gt 0) {
        throw 'Refusing to run while CaptionNest processes already exist.'
    }
}

function Assert-OldInstallerIdentity {
    $Actual = (Get-FileHash -LiteralPath $OldInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $OldInstallerSha256.ToLowerInvariant()) {
        throw "Affected installer SHA-256 mismatch. Expected $OldInstallerSha256, got $Actual."
    }
}

function Wait-ProcessExit {
    param(
        [Parameter(Mandatory = $true)]$Process,
        [int]$TimeoutSeconds = 180,
        [int[]]$AllowedExitCodes = @(0)
    )
    if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
        & taskkill.exe @('/PID', $Process.Id.ToString(), '/T', '/F') | Out-Null
        throw "Process $($Process.Id) timed out after $TimeoutSeconds seconds."
    }
    if ($Process.ExitCode -notin $AllowedExitCodes) {
        throw "Process $($Process.Id) exited with $($Process.ExitCode)."
    }
}

function Start-OwnedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    $Process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -PassThru
    $script:OwnedProcesses += $Process
    return $Process
}

function Invoke-Installer {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string[]]$Arguments = @()
    )
    $Process = Start-OwnedProcess -FilePath $Path -ArgumentList $Arguments
    Wait-ProcessExit -Process $Process
}

function Get-InstalledVersion {
    if (-not (Test-Path -LiteralPath $UninstallKey)) {
        return $null
    }
    return (Get-ItemProperty -LiteralPath $UninstallKey).DisplayVersion
}

function Assert-InstalledVersion {
    param([string]$Version = $ExpectedVersion)
    $Actual = Get-InstalledVersion
    if ($Actual -ne $Version) {
        throw "Expected installed version $Version, got '$Actual'."
    }
}

function Write-ModelFixture {
    $ModelRoot = Join-Path $ModelsRoot 'small'
    New-Item -ItemType Directory -Path $ModelRoot -Force | Out-Null
    $Payload = [Text.Encoding]::UTF8.GetBytes('test')
    foreach ($Name in @('config.json', 'model.bin', 'tokenizer.json')) {
        [IO.File]::WriteAllBytes((Join-Path $ModelRoot $Name), $Payload)
    }
    $Hash = (Get-FileHash -LiteralPath (Join-Path $ModelRoot 'model.bin') -Algorithm SHA256).Hash.ToLowerInvariant()
    $Manifest = [ordered]@{
        manifest_version = 1
        repo_id = 'Systran/faster-whisper-small'
        revision = '536b0662742c02347bc0e980a01041f333bce120'
        files = [ordered]@{
            'config.json' = [ordered]@{ size = 4 }
            'model.bin' = [ordered]@{ size = 4; sha256 = $Hash }
            'tokenizer.json' = [ordered]@{ size = 4 }
        }
    }
    $Manifest | ConvertTo-Json -Depth 6 | Set-Content `
        -LiteralPath (Join-Path $ModelRoot '.captionnest-model-manifest.json') `
        -Encoding utf8NoBOM
}

function Assert-ModelPresent {
    if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) {
        throw "Recognition model marker was deleted: $MarkerPath"
    }
}

function Assert-ModelAbsent {
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        if (-not (Test-Path -LiteralPath $MarkerPath)) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $Deadline)
    if (Test-Path -LiteralPath $MarkerPath) {
        throw "Recognition model marker was retained after explicit deletion: $MarkerPath"
    }
}

function Get-ProcessWindow {
    param([Parameter(Mandatory = $true)]$Process)
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    $LastNewWindows = @()
    do {
        $Process.Refresh()
        if (
            $Process.MainWindowHandle -ne 0 -and
            [CaptionNestNativeMethods]::IsWindow([IntPtr]$Process.MainWindowHandle)
        ) {
            return [IntPtr]$Process.MainWindowHandle
        }
        $LastNewWindows = @(
            [CaptionNestNativeMethods]::EnumerateTopLevelWindows() | Where-Object {
                $_.Handle.ToInt64() -notin $script:BaselineWindowHandles
            }
        )
        $Candidates = @($LastNewWindows | Where-Object {
            $_.Title -like '*CaptionNest*' -or $_.ClassName -eq '#32770'
        })
        if ($Candidates.Count -gt 1) {
            $Details = $Candidates | ForEach-Object {
                "title='$($_.Title)' class='$($_.ClassName)' pid=$($_.ProcessId) visible=$($_.Visible)"
            }
            throw "Multiple CaptionNest GUI windows matched: $($Details -join '; ')."
        }
        if ($Candidates.Count -eq 1) {
            return $Candidates[0].Handle
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $Deadline)
    $Diagnostics = @($LastNewWindows | Select-Object -First 20 | ForEach-Object {
        "title='$($_.Title)' class='$($_.ClassName)' pid=$($_.ProcessId) visible=$($_.Visible)"
    })
    if ($Diagnostics.Count -eq 0) { $Diagnostics = @('<none>') }
    throw (
        "No CaptionNest GUI window appeared for launcher process $($Process.Id). " +
        "New top-level windows: $($Diagnostics -join '; ')."
    )
}

function Wait-NativeWindowClosed {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$WindowHandle,
        [int]$TimeoutSeconds = 180
    )
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while (
        [CaptionNestNativeMethods]::IsWindow($WindowHandle) -and
        [DateTime]::UtcNow -lt $Deadline
    ) {
        Start-Sleep -Milliseconds 250
    }
    if ([CaptionNestNativeMethods]::IsWindow($WindowHandle)) {
        throw "CaptionNest GUI window did not close within $TimeoutSeconds seconds."
    }
}

function Get-NativeChildDiagnostics {
    param([Parameter(Mandatory = $true)][IntPtr]$WindowHandle)
    $Items = @(
        [CaptionNestNativeMethods]::EnumerateChildWindows($WindowHandle) |
            Select-Object -First 20 |
            ForEach-Object {
                "text='$($_.Title)' class='$($_.ClassName)' id=$($_.ControlId) " +
                "style=0x$('{0:X}' -f $_.Style) visible=$($_.Visible) enabled=$($_.Enabled)"
            }
    )
    if ($Items.Count -eq 0) { return '<none>' }
    return $Items -join '; '
}

function Get-NativeWindowSignature {
    param([Parameter(Mandatory = $true)][IntPtr]$WindowHandle)
    return (
        @([CaptionNestNativeMethods]::EnumerateChildWindows($WindowHandle)) |
            Sort-Object { $_.Handle.ToInt64() } |
            ForEach-Object {
                "$($_.Handle.ToInt64())|$($_.ClassName)|$($_.ControlId)|$($_.Title)|" +
                "$($_.Style)|$($_.Visible)|$($_.Enabled)"
            }
    ) -join ';'
}

function Invoke-NativeControlMessage {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$ControlHandle,
        [Parameter(Mandatory = $true)][uint32]$Message,
        [IntPtr]$WordParameter = [IntPtr]::Zero,
        [IntPtr]$LongParameter = [IntPtr]::Zero,
        [Parameter(Mandatory = $true)][IntPtr]$WindowHandle,
        [Parameter(Mandatory = $true)][string]$Description
    )
    $Result = [IntPtr]::Zero
    $Succeeded = [CaptionNestNativeMethods]::SendMessageTimeout(
        $ControlHandle,
        $Message,
        $WordParameter,
        $LongParameter,
        0x0002,
        2000,
        [ref]$Result
    )
    if ($Succeeded -eq [IntPtr]::Zero) {
        $NativeError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "$Description native message 0x$('{0:X}' -f $Message) failed or timed out (Win32=$NativeError). Native child controls: $Diagnostics"
    }
    return $Result
}

function Get-NativeControlsByType {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$WindowHandle,
        [Parameter(Mandatory = $true)][int[]]$ButtonTypes,
        [Parameter(Mandatory = $true)][string]$Description,
        [int]$TimeoutSeconds = 30
    )
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $Controls = @(
            [CaptionNestNativeMethods]::EnumerateChildWindows($WindowHandle) |
                Where-Object {
                    $_.ClassName -eq 'Button' -and
                    [int]($_.Style -band 0xF) -in $ButtonTypes
                }
        )
        if ($Controls.Count -gt 0) { return $Controls }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $Deadline)
    $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
    throw (
        "Timed out waiting for $Description (button types $($ButtonTypes -join ',')). " +
        "Native child controls: $Diagnostics"
    )
}

function Invoke-NativeButton {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$WindowHandle,
        [Parameter(Mandatory = $true)][int]$ControlId,
        [Parameter(Mandatory = $true)][string]$Description
    )
    $Button = [CaptionNestNativeMethods]::GetDlgItem($WindowHandle, $ControlId)
    $MatchingButtons = @(
        [CaptionNestNativeMethods]::EnumerateChildWindows($WindowHandle) |
            Where-Object {
                $_.ClassName -eq 'Button' -and $_.ControlId -eq $ControlId
            }
    )
    if (
        $Button -eq [IntPtr]::Zero -or
        $MatchingButtons.Count -ne 1 -or
        $MatchingButtons[0].Handle -ne $Button
    ) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "$Description button $ControlId was not uniquely identifiable. Native child controls: $Diagnostics"
    }
    if (-not [CaptionNestNativeMethods]::IsWindowEnabled($Button)) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "$Description button $ControlId was not enabled. Native child controls: $Diagnostics"
    }
    $ParentDialog = [CaptionNestNativeMethods]::GetParent($Button)
    if ($ParentDialog -eq [IntPtr]::Zero -or -not [CaptionNestNativeMethods]::IsWindow($ParentDialog)) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "$Description button $ControlId parent dialog was unavailable. Native child controls: $Diagnostics"
    }
    Write-Host "GUI-ACTION: $Description dispatch"
    if (-not [CaptionNestNativeMethods]::PostMessage(
        $ParentDialog,
        0x0111,
        [IntPtr]($ControlId -band 0xFFFF),
        $Button
    )) {
        $NativeError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "$Description button dispatch failed (Win32=$NativeError). Native child controls: $Diagnostics"
    }
    Write-Host "GUI-ACTION: $Description dispatched"
}

function Set-NativeCheckbox {
    param(
        [Parameter(Mandatory = $true)]$Control,
        [Parameter(Mandatory = $true)][IntPtr]$WindowHandle,
        [Parameter(Mandatory = $true)][bool]$Checked,
        [Parameter(Mandatory = $true)][string]$Description
    )
    $State = if ($Checked) { 1 } else { 0 }
    [void](Invoke-NativeControlMessage `
        -ControlHandle $Control.Handle `
        -Message 0x00F1 `
        -WordParameter ([IntPtr]$State) `
        -WindowHandle $WindowHandle `
        -Description "$Description checkbox set")
    $Actual = (Invoke-NativeControlMessage `
        -ControlHandle $Control.Handle `
        -Message 0x00F0 `
        -WindowHandle $WindowHandle `
        -Description "$Description checkbox readback").ToInt64()
    if ($Actual -ne $State) {
        throw "$Description checkbox state was $Actual after requesting $State."
    }
}

function Get-CaptionNestInteractiveWindow {
    param([Parameter(Mandatory = $true)]$Process)
    $WindowHandle = Get-ProcessWindow -Process $Process
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $InitialControls = @(
            [CaptionNestNativeMethods]::EnumerateChildWindows($WindowHandle)
        )
        $PrimaryButton = [CaptionNestNativeMethods]::GetDlgItem($WindowHandle, 1)
        if ($InitialControls.Count -gt 0 -and $PrimaryButton -ne [IntPtr]::Zero) {
            break
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $Deadline)
    if ($InitialControls.Count -eq 0 -or $PrimaryButton -eq [IntPtr]::Zero) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "CaptionNest GUI page did not become ready. Native child controls: $Diagnostics"
    }
    $LanguageSelectors = @(
        $InitialControls |
            Where-Object { $_.ClassName -eq 'ComboBox' -and $_.ControlId -eq 1002 }
    )
    if ($LanguageSelectors.Count -gt 1) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "Multiple NSIS language selectors matched. Native child controls: $Diagnostics"
    }
    if ($LanguageSelectors.Count -eq 1) {
        $LanguageSelector = $LanguageSelectors[0]
        $LanguageCount = (Invoke-NativeControlMessage `
            -ControlHandle $LanguageSelector.Handle `
            -Message 0x0146 `
            -WindowHandle $WindowHandle `
            -Description 'language selector choice count').ToInt64()
        if ($LanguageCount -le 0) {
            $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
            throw "NSIS language selector contained $LanguageCount choices. Native child controls: $Diagnostics"
        }

        $SelectedLanguage = (Invoke-NativeControlMessage `
            -ControlHandle $LanguageSelector.Handle `
            -Message 0x0147 `
            -WindowHandle $WindowHandle `
            -Description 'language selector current choice').ToInt64()
        if ($SelectedLanguage -eq -1) {
            [void](Invoke-NativeControlMessage `
                -ControlHandle $LanguageSelector.Handle `
                -Message 0x014E `
                -WindowHandle $WindowHandle `
                -Description 'language selector choose index zero')
            $SelectedLanguage = (Invoke-NativeControlMessage `
                -ControlHandle $LanguageSelector.Handle `
                -Message 0x0147 `
                -WindowHandle $WindowHandle `
                -Description 'language selector choice readback').ToInt64()
        }
        if ($SelectedLanguage -lt 0 -or $SelectedLanguage -ge $LanguageCount) {
            $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
            throw "NSIS language selector did not retain a valid selection (selected=$SelectedLanguage, count=$LanguageCount). Native child controls: $Diagnostics"
        }

        Invoke-NativeButton `
            -WindowHandle $WindowHandle `
            -ControlId 1 `
            -Description 'language selector default OK'
        $Deadline = [DateTime]::UtcNow.AddSeconds(30)
        do {
            if (-not [CaptionNestNativeMethods]::IsWindow($WindowHandle)) {
                return Get-ProcessWindow -Process $Process
            }
            $RemainingSelectors = @(
                [CaptionNestNativeMethods]::EnumerateChildWindows($WindowHandle) |
                    Where-Object {
                        $_.ClassName -eq 'ComboBox' -and $_.ControlId -eq 1002
                    }
            )
            if ($RemainingSelectors.Count -eq 0) {
                Write-Host 'GUI-ACTION: language selector transition observed'
                return $WindowHandle
            }
            Start-Sleep -Milliseconds 200
        } while ([DateTime]::UtcNow -lt $Deadline)
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "Language selector did not advance. Native child controls: $Diagnostics"
    }
    return $WindowHandle
}

function Complete-GuiUpgradeWithDefault {
    $Process = Start-OwnedProcess -FilePath $UpgradeInstaller
    $WindowHandle = Get-CaptionNestInteractiveWindow -Process $Process
    Invoke-NativeButton -WindowHandle $WindowHandle -ControlId 1 -Description 'upgrade welcome next'

    $Radios = @(Get-NativeControlsByType `
        -WindowHandle $WindowHandle `
        -ButtonTypes @(4, 9) `
        -Description 'upgrade reinstall radio choices')
    Write-Host 'GUI-ACTION: upgrade welcome transition observed'
    if ($Radios.Count -ne 2) { throw 'The reinstall page did not expose two choices.' }
    $Selections = @($Radios | ForEach-Object {
        (Invoke-NativeControlMessage `
            -ControlHandle $_.Handle `
            -Message 0x00F0 `
            -WindowHandle $WindowHandle `
            -Description 'upgrade reinstall radio readback').ToInt64() -eq 1
    })
    if ($Selections[0] -or -not $Selections[1]) {
        throw "GUI upgrade default was not in-place: $($Selections -join ',')."
    }
    $LastClickedSignature = Get-NativeWindowSignature -WindowHandle $WindowHandle
    Invoke-NativeButton -WindowHandle $WindowHandle -ControlId 1 -Description 'upgrade choice next'

    $ObservedChoiceTransition = $false
    $LastClickWasCompletion = $false
    $CompletionClickAttempts = 0
    $LastClickAt = [DateTime]::UtcNow
    $Deadline = [DateTime]::UtcNow.AddSeconds(180)
    while (
        [CaptionNestNativeMethods]::IsWindow($WindowHandle) -and
        [DateTime]::UtcNow -lt $Deadline
    ) {
        $Controls = @([CaptionNestNativeMethods]::EnumerateChildWindows($WindowHandle))
        $CurrentSignature = Get-NativeWindowSignature -WindowHandle $WindowHandle
        if ($CurrentSignature -eq $LastClickedSignature) {
            if (
                $LastClickWasCompletion -and
                $CompletionClickAttempts -lt 2 -and
                [DateTime]::UtcNow -ge $LastClickAt.AddSeconds(2)
            ) {
                Invoke-NativeButton `
                    -WindowHandle $WindowHandle `
                    -ControlId 1 `
                    -Description 'upgrade finish retry'
                $CompletionClickAttempts += 1
                $LastClickAt = [DateTime]::UtcNow
            } elseif (
                $LastClickWasCompletion -and
                $CompletionClickAttempts -eq 2 -and
                [DateTime]::UtcNow -ge $LastClickAt.AddSeconds(5)
            ) {
                $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
                throw "GUI upgrade finish page did not transition after one re-dispatch. Native child controls: $Diagnostics"
            }
            Start-Sleep -Milliseconds 200
            continue
        }
        if (-not $ObservedChoiceTransition) {
            Write-Host 'GUI-ACTION: upgrade choice transition observed'
            $ObservedChoiceTransition = $true
        }
        foreach ($Checkbox in @(
            $Controls |
                Where-Object {
                    $_.ClassName -eq 'Button' -and
                    [int]($_.Style -band 0xF) -in @(2, 3, 5, 6)
                }
        )) {
            Set-NativeCheckbox `
                -Control $Checkbox `
                -WindowHandle $WindowHandle `
                -Checked $false `
                -Description 'upgrade finish option'
        }
        $Next = [CaptionNestNativeMethods]::GetDlgItem($WindowHandle, 1)
        if (
            $Next -ne [IntPtr]::Zero -and
            [CaptionNestNativeMethods]::IsWindowEnabled($Next)
        ) {
            $IsCompletionPage = @(
                $Controls | Where-Object {
                    $_.ControlId -eq 1201 -and $_.Visible
                }
            ).Count -eq 1
            $ActionDescription = if ($IsCompletionPage) {
                'upgrade finish'
            } else {
                'upgrade page next'
            }
            Invoke-NativeButton `
                -WindowHandle $WindowHandle `
                -ControlId 1 `
                -Description $ActionDescription
            $LastClickedSignature = $CurrentSignature
            $LastClickWasCompletion = $IsCompletionPage
            $CompletionClickAttempts = if ($IsCompletionPage) { 1 } else { 0 }
            $LastClickAt = [DateTime]::UtcNow
        }
        Start-Sleep -Milliseconds 200
    }
    if ([CaptionNestNativeMethods]::IsWindow($WindowHandle)) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "GUI upgrade did not close within 180 seconds. Native child controls: $Diagnostics"
    }
    Wait-NativeWindowClosed -WindowHandle $WindowHandle -TimeoutSeconds 5
    if (-not $Process.HasExited) { Wait-ProcessExit -Process $Process -TimeoutSeconds 5 }
    Write-Host 'GUI-ACTION: GUI upgrade window closed'
}

function Invoke-CurrentUninstallerGui {
    param(
        [ValidateSet('cancel', 'keep', 'delete')][string]$Decision
    )
    $Uninstaller = Join-Path $InstallRoot 'uninstall.exe'
    $Process = Start-OwnedProcess -FilePath $Uninstaller
    $WindowHandle = Get-CaptionNestInteractiveWindow -Process $Process
    $Checkboxes = @(
        Get-NativeControlsByType `
            -WindowHandle $WindowHandle `
            -ButtonTypes @(2, 3, 5, 6) `
            -Description 'current uninstall data checkbox'
    )
    if ($Checkboxes.Count -ne 1) {
        throw "Expected one uninstall data checkbox; found $($Checkboxes.Count)."
    }
    $Checkbox = $Checkboxes[0]
    if ($Decision -eq 'cancel') {
        Invoke-NativeButton `
            -WindowHandle $WindowHandle `
            -ControlId 2 `
            -Description 'current uninstall cancel'
        Wait-NativeWindowClosed -WindowHandle $WindowHandle
        if (-not $Process.HasExited) {
            Wait-ProcessExit -Process $Process -AllowedExitCodes @(0, 1)
        }
        Write-Host 'GUI-ACTION: current uninstall cancel window closed'
        return
    }
    Set-NativeCheckbox `
        -Control $Checkbox `
        -WindowHandle $WindowHandle `
        -Checked ($Decision -eq 'delete') `
        -Description "current uninstall $Decision"
    $ConfirmButtonHandle = [CaptionNestNativeMethods]::GetDlgItem($WindowHandle, 1)
    $ConfirmButtonInfo = @(
        [CaptionNestNativeMethods]::EnumerateChildWindows($WindowHandle) |
            Where-Object { $_.Handle -eq $ConfirmButtonHandle }
    )
    if ($ConfirmButtonHandle -eq [IntPtr]::Zero -or $ConfirmButtonInfo.Count -ne 1) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "Current uninstaller confirm button was not uniquely identifiable. Native child controls: $Diagnostics"
    }
    $ConfirmButtonTitle = $ConfirmButtonInfo[0].Title
    Invoke-NativeButton `
        -WindowHandle $WindowHandle `
        -ControlId 1 `
        -Description "current uninstall $Decision confirm"
    $SawNonReadyFinish = $false
    $ClickedFinish = $false
    $Deadline = [DateTime]::UtcNow.AddSeconds(180)
    while (
        [CaptionNestNativeMethods]::IsWindow($WindowHandle) -and
        [DateTime]::UtcNow -lt $Deadline
    ) {
        $Finish = [CaptionNestNativeMethods]::GetDlgItem($WindowHandle, 1)
        if ($Finish -eq [IntPtr]::Zero -or -not [CaptionNestNativeMethods]::IsWindowEnabled($Finish)) {
            $SawNonReadyFinish = $true
            Start-Sleep -Milliseconds 200
            continue
        }
        $FinishInfo = @(
            [CaptionNestNativeMethods]::EnumerateChildWindows($WindowHandle) |
                Where-Object { $_.Handle -eq $Finish }
        )
        $CompletionTextChanged = (
            $FinishInfo.Count -eq 1 -and
            $FinishInfo[0].Title -ne $ConfirmButtonTitle
        )
        $CompletionControlChanged = $Finish -ne $ConfirmButtonHandle
        if ($SawNonReadyFinish -or $CompletionTextChanged -or $CompletionControlChanged) {
            Write-Host "GUI-ACTION: current uninstall $Decision completion state observed"
            Invoke-NativeButton `
                -WindowHandle $WindowHandle `
                -ControlId 1 `
                -Description "current uninstall $Decision finish"
            $ClickedFinish = $true
            break
        }
        Start-Sleep -Milliseconds 200
    }
    if ([CaptionNestNativeMethods]::IsWindow($WindowHandle) -and -not $ClickedFinish) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $WindowHandle
        throw "Current uninstaller $Decision did not reach a distinct enabled completion state. Native child controls: $Diagnostics"
    }
    Wait-NativeWindowClosed -WindowHandle $WindowHandle -TimeoutSeconds 5
    if (-not $Process.HasExited) { Wait-ProcessExit -Process $Process -TimeoutSeconds 5 }
    Write-Host "GUI-ACTION: current uninstall $Decision window closed"
}

function Invoke-AffectedUninstallerGuiConfirm {
    $Uninstaller = Join-Path $InstallRoot 'uninstall.exe'
    $Process = Start-OwnedProcess -FilePath $Uninstaller
    $MainWindowHandle = Get-CaptionNestInteractiveWindow -Process $Process
    $Checkboxes = @(
        Get-NativeControlsByType `
            -WindowHandle $MainWindowHandle `
            -ButtonTypes @(2, 3, 5, 6) `
            -Description 'affected uninstall data checkbox'
    )
    if ($Checkboxes.Count -ne 1) {
        throw "Expected one affected-uninstall data checkbox; found $($Checkboxes.Count)."
    }
    Set-NativeCheckbox `
        -Control $Checkboxes[0] `
        -WindowHandle $MainWindowHandle `
        -Checked $true `
        -Description 'affected uninstall explicit deletion'
    $ConfirmButtonHandle = [CaptionNestNativeMethods]::GetDlgItem($MainWindowHandle, 1)
    $ConfirmButtonInfo = @(
        [CaptionNestNativeMethods]::EnumerateChildWindows($MainWindowHandle) |
            Where-Object { $_.Handle -eq $ConfirmButtonHandle }
    )
    if ($ConfirmButtonHandle -eq [IntPtr]::Zero -or $ConfirmButtonInfo.Count -ne 1) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $MainWindowHandle
        throw "Affected uninstaller confirm button was not uniquely identifiable. Native child controls: $Diagnostics"
    }
    $ConfirmButtonTitle = $ConfirmButtonInfo[0].Title
    Invoke-NativeButton `
        -WindowHandle $MainWindowHandle `
        -ControlId 1 `
        -Description 'affected uninstall confirm'

    $Confirmed = $false
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $Popup = [CaptionNestNativeMethods]::GetLastActivePopup($MainWindowHandle)
        if ($Popup -ne [IntPtr]::Zero -and $Popup -ne $MainWindowHandle) {
            $OkButton = [CaptionNestNativeMethods]::GetDlgItem($Popup, 1)
            if ($OkButton -ne [IntPtr]::Zero) {
                Invoke-NativeButton `
                    -WindowHandle $Popup `
                    -ControlId 1 `
                    -Description 'affected uninstall deletion confirmation OK'
                $Confirmed = $true
                break
            }
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $Deadline)
    if (-not $Confirmed) {
        throw 'Affected uninstaller did not expose its explicit deletion confirmation.'
    }
    $SawNonReadyFinish = $false
    $ClickedFinish = $false
    $Deadline = [DateTime]::UtcNow.AddSeconds(180)
    while (
        [CaptionNestNativeMethods]::IsWindow($MainWindowHandle) -and
        [DateTime]::UtcNow -lt $Deadline
    ) {
        $Finish = [CaptionNestNativeMethods]::GetDlgItem($MainWindowHandle, 1)
        if ($Finish -eq [IntPtr]::Zero -or -not [CaptionNestNativeMethods]::IsWindowEnabled($Finish)) {
            $SawNonReadyFinish = $true
            Start-Sleep -Milliseconds 200
            continue
        }
        $FinishInfo = @(
            [CaptionNestNativeMethods]::EnumerateChildWindows($MainWindowHandle) |
                Where-Object { $_.Handle -eq $Finish }
        )
        $CompletionTextChanged = (
            $FinishInfo.Count -eq 1 -and
            $FinishInfo[0].Title -ne $ConfirmButtonTitle
        )
        $CompletionControlChanged = $Finish -ne $ConfirmButtonHandle
        if ($SawNonReadyFinish -or $CompletionTextChanged -or $CompletionControlChanged) {
            Write-Host 'GUI-ACTION: affected uninstall completion state observed'
            Invoke-NativeButton `
                -WindowHandle $MainWindowHandle `
                -ControlId 1 `
                -Description 'affected uninstall finish'
            $ClickedFinish = $true
            break
        }
        Start-Sleep -Milliseconds 200
    }
    if (
        [CaptionNestNativeMethods]::IsWindow($MainWindowHandle) -and
        -not $ClickedFinish
    ) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $MainWindowHandle
        throw "Affected uninstaller did not reach a distinct enabled completion state. Native child controls: $Diagnostics"
    }
    $Deadline = [DateTime]::UtcNow.AddSeconds(5)
    while (
        [CaptionNestNativeMethods]::IsWindow($MainWindowHandle) -and
        [DateTime]::UtcNow -lt $Deadline
    ) {
        Start-Sleep -Milliseconds 200
    }
    if ([CaptionNestNativeMethods]::IsWindow($MainWindowHandle)) {
        $Diagnostics = Get-NativeChildDiagnostics -WindowHandle $MainWindowHandle
        throw "Affected uninstaller remained open after its completion button was clicked. Native child controls: $Diagnostics"
    }
    if (-not $Process.HasExited) { Wait-ProcessExit -Process $Process -TimeoutSeconds 5 }
    Write-Host 'GUI-ACTION: affected uninstall window closed'
    Assert-ModelAbsent
}

function Assert-InstalledAppAndModelReady {
    $App = Join-Path $InstallRoot 'captionnest.exe'
    $Sidecar = Join-Path $InstallRoot 'captionnest-sidecar.exe'
    foreach ($Path in @($App, $Sidecar)) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "Installed product file is missing: $Path"
        }
    }

    $AppProcess = Start-OwnedProcess -FilePath $App
    [void](Get-ProcessWindow -Process $AppProcess)
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $SidecarProcess = @(Get-Process -Name 'captionnest-sidecar' -ErrorAction SilentlyContinue)
        if ($SidecarProcess.Count -gt 0) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $Deadline)
    if ($SidecarProcess.Count -eq 0) { throw 'Installed desktop app did not start its sidecar.' }
    $AppProcess.CloseMainWindow() | Out-Null
    if (-not $AppProcess.WaitForExit(10000)) { $AppProcess.Kill() }
    $Deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $DesktopSidecars = @(
            Get-Process -Name 'captionnest-sidecar' -ErrorAction SilentlyContinue
        )
        if ($DesktopSidecars.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $Deadline)
    foreach ($DesktopSidecar in $DesktopSidecars) {
        & taskkill.exe @('/PID', $DesktopSidecar.Id.ToString(), '/T', '/F') | Out-Null
    }

    $PortListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $PortListener.Start()
    $Port = ([Net.IPEndPoint]$PortListener.LocalEndpoint).Port
    $PortListener.Stop()
    $Token = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
    $PreviousToken = $env:CAPTIONNEST_SESSION_TOKEN
    try {
        $env:CAPTIONNEST_SESSION_TOKEN = $Token
        $ApiProcess = Start-OwnedProcess -FilePath $Sidecar -ArgumentList @(
            '--host', '127.0.0.1', '--port', $Port.ToString(), '--data-dir', $AppDataRoot
        )
        $Headers = @{ 'X-CaptionNest-Session' = $Token }
        $Deadline = [DateTime]::UtcNow.AddSeconds(30)
        do {
            try {
                $Response = Invoke-RestMethod `
                    -Uri "http://127.0.0.1:$Port/api/models" `
                    -Headers $Headers `
                    -TimeoutSec 2
                break
            } catch {
                Start-Sleep -Milliseconds 250
            }
        } while ([DateTime]::UtcNow -lt $Deadline)
        $Small = @($Response.items | Where-Object { $_.id -eq 'small' })
        if ($Small.Count -ne 1 -or $Small[0].status -ne 'ready') {
            throw 'Installed sidecar did not report the retained small model as ready.'
        }
        & taskkill.exe @('/PID', $ApiProcess.Id.ToString(), '/T', '/F') | Out-Null
    } finally {
        $env:CAPTIONNEST_SESSION_TOKEN = $PreviousToken
    }
}

function Remove-OwnedDirectoryWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$TimeoutSeconds = 30
    )
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (-not (Test-Path -LiteralPath $Path)) { return }
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        } catch {
            # NSIS can still be deleting children after its launcher exits.
            # Retry the owned disposable-runner path until it is stable/absent.
            if ([DateTime]::UtcNow -ge $Deadline) {
                throw "Unable to remove owned CaptionNest path '$Path': $($_.Exception.Message)"
            }
            Start-Sleep -Milliseconds 250
        }
    } while ([DateTime]::UtcNow -lt $Deadline)
    if (Test-Path -LiteralPath $Path) {
        throw "Owned CaptionNest path still exists after $TimeoutSeconds seconds: $Path"
    }
}

function Remove-OwnedCaptionNestState {
    foreach ($ProcessName in @('captionnest', 'captionnest-sidecar')) {
        Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | ForEach-Object {
            & taskkill.exe @('/PID', $_.Id.ToString(), '/T', '/F') | Out-Null
            if (-not $_.WaitForExit(10000)) {
                throw "Owned process $($_.Id) did not exit during cleanup."
            }
        }
    }
    foreach ($Path in @($InstallRoot, $AppDataRoot)) {
        Remove-OwnedDirectoryWithRetry -Path $Path
    }
    Remove-Item -LiteralPath $UninstallKey, $ManufacturerKey -Recurse -Force -ErrorAction SilentlyContinue
}

function Install-AffectedVersionWithModel {
    Invoke-Installer -Path $OldInstaller -Arguments @('/S')
    Write-ModelFixture
    Assert-ModelPresent
}

function Test-UpgradeMode {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Arguments = @(),
        [switch]$Gui
    )
    Write-Host "LIFECYCLE: upgrade-$Name"
    Install-AffectedVersionWithModel
    if ($Gui) {
        Complete-GuiUpgradeWithDefault
    } else {
        Invoke-Installer -Path $UpgradeInstaller -Arguments $Arguments
    }
    Assert-InstalledVersion -Version $UpgradeExpectedVersion
    Assert-ModelPresent
    Assert-InstalledAppAndModelReady
    Invoke-Installer -Path (Join-Path $InstallRoot 'uninstall.exe') -Arguments @('/S')
    Assert-ModelPresent
    Remove-OwnedCaptionNestState
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public sealed class CaptionNestWindowInfo
{
    public IntPtr Handle { get; set; }
    public string Title { get; set; }
    public string ClassName { get; set; }
    public uint ProcessId { get; set; }
    public bool Visible { get; set; }
    public bool Enabled { get; set; }
    public int ControlId { get; set; }
    public long Style { get; set; }
}

public static class CaptionNestNativeMethods
{
    private delegate bool EnumWindowsCallback(IntPtr window, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsCallback callback, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern bool EnumChildWindows(
        IntPtr parent,
        EnumWindowsCallback callback,
        IntPtr parameter
    );

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr window, StringBuilder text, int count);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(IntPtr window, StringBuilder text, int count);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll")]
    public static extern bool IsWindowEnabled(IntPtr window);

    [DllImport("user32.dll")]
    private static extern int GetDlgCtrlID(IntPtr window);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    private static extern IntPtr GetWindowLongPtr(IntPtr window, int index);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsWindow(IntPtr window);

    [DllImport("user32.dll")]
    public static extern IntPtr GetLastActivePopup(IntPtr window);

    [DllImport("user32.dll")]
    public static extern IntPtr GetDlgItem(IntPtr dialog, int itemId);

    [DllImport("user32.dll")]
    public static extern IntPtr GetParent(IntPtr window);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool PostMessage(
        IntPtr window,
        uint message,
        IntPtr wordParameter,
        IntPtr longParameter
    );

    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr SendMessageTimeout(
        IntPtr window,
        uint message,
        IntPtr wordParameter,
        IntPtr longParameter,
        uint flags,
        uint timeoutMilliseconds,
        out IntPtr result
    );

    public static CaptionNestWindowInfo[] EnumerateTopLevelWindows()
    {
        var windows = new List<CaptionNestWindowInfo>();
        EnumWindows((window, parameter) =>
        {
            windows.Add(DescribeWindow(window));
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }

    public static CaptionNestWindowInfo[] EnumerateChildWindows(IntPtr parent)
    {
        var windows = new List<CaptionNestWindowInfo>();
        EnumChildWindows(parent, (window, parameter) =>
        {
            windows.Add(DescribeWindow(window));
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }

    private static CaptionNestWindowInfo DescribeWindow(IntPtr window)
    {
        var title = new StringBuilder(512);
        var className = new StringBuilder(256);
        GetWindowText(window, title, title.Capacity);
        GetClassName(window, className, className.Capacity);
        uint processId;
        GetWindowThreadProcessId(window, out processId);
        return new CaptionNestWindowInfo
        {
            Handle = window,
            Title = title.ToString().Replace("\r", " ").Replace("\n", " "),
            ClassName = className.ToString(),
            ProcessId = processId,
            Visible = IsWindowVisible(window),
            Enabled = IsWindowEnabled(window),
            ControlId = GetDlgCtrlID(window),
            Style = GetWindowLongPtr(window, -16).ToInt64()
        };
    }
}
'@
$script:BaselineWindowHandles = @(
    [CaptionNestNativeMethods]::EnumerateTopLevelWindows() |
        ForEach-Object { $_.Handle.ToInt64() }
)
Assert-DisposableRunnerState
Assert-OldInstallerIdentity
New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null

try {
    Write-Host 'LIFECYCLE: affected-explicit-uninstall'
    Install-AffectedVersionWithModel
    Invoke-AffectedUninstallerGuiConfirm
    Remove-OwnedCaptionNestState

    Test-UpgradeMode -Name 'gui-default' -Gui
    Test-UpgradeMode -Name 'silent' -Arguments @('/S')
    Test-UpgradeMode -Name 'passive' -Arguments @('/P')
    Test-UpgradeMode -Name 'update' -Arguments @('/UPDATE', '/P')

    Write-Host 'LIFECYCLE: current-uninstall-cancel-keep-delete'
    Invoke-Installer -Path $CurrentInstaller -Arguments @('/S')
    Write-ModelFixture
    Invoke-CurrentUninstallerGui -Decision cancel
    Assert-InstalledVersion
    Assert-ModelPresent
    Invoke-CurrentUninstallerGui -Decision keep
    Assert-ModelPresent
    Remove-OwnedCaptionNestState

    Invoke-Installer -Path $CurrentInstaller -Arguments @('/S')
    Write-ModelFixture
    Invoke-CurrentUninstallerGui -Decision delete
    Assert-ModelAbsent
    Write-Host 'All isolated CaptionNest installer lifecycle scenarios passed.' -ForegroundColor Green
} finally {
    foreach ($Process in @($script:OwnedProcesses)) {
        if ($null -ne $Process -and -not $Process.HasExited) {
            & taskkill.exe @('/PID', $Process.Id.ToString(), '/T', '/F') | Out-Null
        }
    }
    Remove-OwnedCaptionNestState
    if (Test-Path -LiteralPath $EvidenceRoot) {
        Remove-Item -LiteralPath $EvidenceRoot -Recurse -Force
    }
}
