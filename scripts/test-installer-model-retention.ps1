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

function Get-UiElement {
    param(
        [Parameter(Mandatory = $true)]$Root,
        [Parameter(Mandatory = $true)]$Condition,
        [int]$TimeoutSeconds = 30
    )
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $Element = $Root.FindFirst(
            [System.Windows.Automation.TreeScope]::Descendants,
            $Condition
        )
        if ($null -ne $Element) { return $Element }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw 'Timed out waiting for an installer UI element.'
}

function Get-ProcessWindow {
    param([Parameter(Mandatory = $true)]$Process)
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    $LastNewWindows = @()
    do {
        $Process.Refresh()
        if ($Process.MainWindowHandle -ne 0) {
            return [System.Windows.Automation.AutomationElement]::FromHandle(
                $Process.MainWindowHandle
            )
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
            return [System.Windows.Automation.AutomationElement]::FromHandle(
                $Candidates[0].Handle
            )
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

function Invoke-UiElement {
    param([Parameter(Mandatory = $true)]$Element)
    $Pattern = $Element.GetCurrentPattern(
        [System.Windows.Automation.InvokePattern]::Pattern
    )
    $Pattern.Invoke()
}

function Set-UiCheckbox {
    param(
        [Parameter(Mandatory = $true)]$Checkbox,
        [Parameter(Mandatory = $true)][bool]$Checked
    )
    $Pattern = $Checkbox.GetCurrentPattern(
        [System.Windows.Automation.TogglePattern]::Pattern
    )
    $IsChecked = $Pattern.Current.ToggleState -eq `
        [System.Windows.Automation.ToggleState]::On
    if ($IsChecked -ne $Checked) { $Pattern.Toggle() }
}

function Get-ButtonCondition {
    param([string]$AutomationId)
    $TypeCondition = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button
        )
    $IdCondition = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
            $AutomationId
        )
    return [System.Windows.Automation.AndCondition]::new($TypeCondition, $IdCondition)
}

function Complete-GuiUpgradeWithDefault {
    $Process = Start-OwnedProcess -FilePath $UpgradeInstaller
    $Window = Get-ProcessWindow -Process $Process
    $WindowHandle = [IntPtr]$Window.Current.NativeWindowHandle
    $NextCondition = Get-ButtonCondition -AutomationId '1'
    Invoke-UiElement (Get-UiElement -Root $Window -Condition $NextCondition)

    $RadioCondition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::RadioButton
    )
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $Radios = @($Window.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            $RadioCondition
        ))
        if ($Radios.Count -eq 2) { break }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $Deadline)
    if ($Radios.Count -ne 2) { throw 'The reinstall page did not expose two choices.' }
    $Selections = @($Radios | ForEach-Object {
        $_.GetCurrentPattern(
            [System.Windows.Automation.SelectionItemPattern]::Pattern
        ).Current.IsSelected
    })
    if ($Selections[0] -or -not $Selections[1]) {
        throw "GUI upgrade default was not in-place: $($Selections -join ',')."
    }
    Invoke-UiElement (Get-UiElement -Root $Window -Condition $NextCondition)

    $Deadline = [DateTime]::UtcNow.AddSeconds(180)
    while (
        [CaptionNestNativeMethods]::IsWindow($WindowHandle) -and
        [DateTime]::UtcNow -lt $Deadline
    ) {
        $Window = [System.Windows.Automation.AutomationElement]::FromHandle($WindowHandle)
        $CheckboxCondition = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::CheckBox
        )
        foreach ($Checkbox in @($Window.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            $CheckboxCondition
        ))) {
            Set-UiCheckbox -Checkbox $Checkbox -Checked $false
        }
        $Next = $Window.FindFirst(
            [System.Windows.Automation.TreeScope]::Descendants,
            $NextCondition
        )
        if ($null -ne $Next -and $Next.Current.IsEnabled) {
            Invoke-UiElement $Next
        }
        Start-Sleep -Milliseconds 500
    }
    Wait-NativeWindowClosed -WindowHandle $WindowHandle -TimeoutSeconds 5
    if (-not $Process.HasExited) { Wait-ProcessExit -Process $Process -TimeoutSeconds 5 }
}

function Invoke-CurrentUninstallerGui {
    param(
        [ValidateSet('cancel', 'keep', 'delete')][string]$Decision
    )
    $Uninstaller = Join-Path $InstallRoot 'uninstall.exe'
    $Process = Start-OwnedProcess -FilePath $Uninstaller
    $Window = Get-ProcessWindow -Process $Process
    $WindowHandle = [IntPtr]$Window.Current.NativeWindowHandle
    $CheckboxCondition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::CheckBox
    )
    $Checkbox = Get-UiElement -Root $Window -Condition $CheckboxCondition
    if ($Decision -eq 'cancel') {
        Invoke-UiElement (Get-UiElement -Root $Window -Condition (Get-ButtonCondition '2'))
        Wait-NativeWindowClosed -WindowHandle $WindowHandle
        if (-not $Process.HasExited) {
            Wait-ProcessExit -Process $Process -AllowedExitCodes @(0, 1)
        }
        return
    }
    Set-UiCheckbox -Checkbox $Checkbox -Checked ($Decision -eq 'delete')
    Invoke-UiElement (Get-UiElement -Root $Window -Condition (Get-ButtonCondition '1'))
    $Deadline = [DateTime]::UtcNow.AddSeconds(180)
    while (
        [CaptionNestNativeMethods]::IsWindow($WindowHandle) -and
        [DateTime]::UtcNow -lt $Deadline
    ) {
        $Window = [System.Windows.Automation.AutomationElement]::FromHandle($WindowHandle)
        if ($null -ne $Window) {
            $Next = $Window.FindFirst(
                [System.Windows.Automation.TreeScope]::Descendants,
                (Get-ButtonCondition '1')
            )
            if ($null -ne $Next -and $Next.Current.IsEnabled) {
                Invoke-UiElement $Next
            }
        }
        Start-Sleep -Milliseconds 500
    }
    Wait-NativeWindowClosed -WindowHandle $WindowHandle -TimeoutSeconds 5
    if (-not $Process.HasExited) { Wait-ProcessExit -Process $Process -TimeoutSeconds 5 }
}

function Invoke-AffectedUninstallerGuiConfirm {
    $Uninstaller = Join-Path $InstallRoot 'uninstall.exe'
    $Process = Start-OwnedProcess -FilePath $Uninstaller
    $Window = Get-ProcessWindow -Process $Process
    $MainWindowHandle = [IntPtr]$Window.Current.NativeWindowHandle
    $CheckboxCondition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::CheckBox
    )
    $Checkbox = Get-UiElement -Root $Window -Condition $CheckboxCondition
    Set-UiCheckbox -Checkbox $Checkbox -Checked $true
    Invoke-UiElement (Get-UiElement -Root $Window -Condition (Get-ButtonCondition '1'))

    $Confirmed = $false
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $Popup = [CaptionNestNativeMethods]::GetLastActivePopup($MainWindowHandle)
        if ($Popup -ne [IntPtr]::Zero -and $Popup -ne $MainWindowHandle) {
            $OkButton = [CaptionNestNativeMethods]::GetDlgItem($Popup, 1)
            if ($OkButton -ne [IntPtr]::Zero) {
                [void][CaptionNestNativeMethods]::SendMessage(
                    $OkButton,
                    0x00F5,
                    [IntPtr]::Zero,
                    [IntPtr]::Zero
                )
                $Confirmed = $true
                break
            }
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $Deadline)
    if (-not $Confirmed) {
        throw 'Affected uninstaller did not expose its explicit deletion confirmation.'
    }
    Wait-NativeWindowClosed -WindowHandle $MainWindowHandle
    if (-not $Process.HasExited) { Wait-ProcessExit -Process $Process }
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

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
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
}

public static class CaptionNestNativeMethods
{
    private delegate bool EnumWindowsCallback(IntPtr window, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsCallback callback, IntPtr parameter);

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
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsWindow(IntPtr window);

    [DllImport("user32.dll")]
    public static extern IntPtr GetLastActivePopup(IntPtr window);

    [DllImport("user32.dll")]
    public static extern IntPtr GetDlgItem(IntPtr dialog, int itemId);

    [DllImport("user32.dll")]
    public static extern IntPtr SendMessage(
        IntPtr window,
        uint message,
        IntPtr wordParameter,
        IntPtr longParameter
    );

    public static CaptionNestWindowInfo[] EnumerateTopLevelWindows()
    {
        var windows = new List<CaptionNestWindowInfo>();
        EnumWindows((window, parameter) =>
        {
            var title = new StringBuilder(512);
            var className = new StringBuilder(256);
            GetWindowText(window, title, title.Capacity);
            GetClassName(window, className, className.Capacity);
            uint processId;
            GetWindowThreadProcessId(window, out processId);
            windows.Add(new CaptionNestWindowInfo
            {
                Handle = window,
                Title = title.ToString().Replace("\r", " ").Replace("\n", " "),
                ClassName = className.ToString(),
                ProcessId = processId,
                Visible = IsWindowVisible(window)
            });
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
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
