param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion
)

$ErrorActionPreference = 'Stop'
$Installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$InstallDirectory = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'CaptionNest'))
$LocalAppDataRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd('\') + '\'
$UninstallRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
$ExternalProcessTimeoutSeconds = 180

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'The release installer smoke test requires Windows x64.'
}
if (-not $InstallDirectory.StartsWith(
    $LocalAppDataRoot,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Unsafe install directory: $InstallDirectory"
}
if ([IO.Path]::GetFileName($InstallDirectory) -ne 'CaptionNest') {
    throw "Unexpected install directory: $InstallDirectory"
}

function Get-CaptionNestProcesses {
    return @(
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ProcessName -in @('captionnest', 'captionnest-sidecar') } |
            Where-Object {
                try {
                    $_.Path -and [IO.Path]::GetFullPath($_.Path).StartsWith(
                        $InstallDirectory + '\',
                        [StringComparison]::OrdinalIgnoreCase
                    )
                } catch {
                    $false
                }
            }
    )
}

function Get-CaptionNestUninstallEntries {
    return @(
        Get-ChildItem -LiteralPath $UninstallRoot -ErrorAction SilentlyContinue |
            ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction SilentlyContinue } |
            Where-Object { $_.DisplayName -eq 'CaptionNest' }
    )
}

function Wait-Until([scriptblock]$Condition, [int]$TimeoutSeconds) {
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (& $Condition) {
            return $true
        }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $Deadline)
    return $false
}

function Invoke-HiddenProcessWithTimeout {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,
        [Parameter(Mandatory = $true)]
        [int]$TimeoutSeconds
    )

    $Process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -PassThru `
        -WindowStyle Hidden
    try {
        if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
            $Taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
            $TaskkillArguments = @('/PID', $Process.Id.ToString(), '/T', '/F')
            & $Taskkill @TaskkillArguments 2>$null | Out-Null
            [void]$Process.WaitForExit(5000)
            return [pscustomobject]@{
                ExitCode = $null
                TimedOut = $true
            }
        }
        $Process.WaitForExit()
        return [pscustomobject]@{
            ExitCode = $Process.ExitCode
            TimedOut = $false
        }
    } finally {
        $Process.Dispose()
    }
}

function Get-FailureMessage($Failure) {
    if ($Failure -is [Management.Automation.ErrorRecord]) {
        return $Failure.Exception.Message
    }
    return [string]$Failure
}

function Stop-CaptionNestProcesses {
    $Processes = Get-CaptionNestProcesses
    foreach ($Process in $Processes) {
        if ($Process.ProcessName -eq 'captionnest') {
            [void]$Process.CloseMainWindow()
        }
    }
    if ($Processes.Count -gt 0) {
        Start-Sleep -Seconds 3
    }
    foreach ($Process in (Get-CaptionNestProcesses)) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
}

$Failure = $null
try {
    if (Test-Path -LiteralPath $InstallDirectory) {
        throw "Refusing to overwrite an existing installation: $InstallDirectory"
    }
    if ((Get-CaptionNestUninstallEntries).Count -ne 0) {
        throw 'A CaptionNest uninstall registry entry already exists.'
    }

    $Install = Invoke-HiddenProcessWithTimeout `
        -FilePath $Installer `
        -ArgumentList @('/S') `
        -TimeoutSeconds $ExternalProcessTimeoutSeconds
    if ($Install.TimedOut) {
        throw "Installer timed out after $ExternalProcessTimeoutSeconds seconds."
    }
    if ($Install.ExitCode -ne 0) {
        throw "Installer failed with exit code $($Install.ExitCode)."
    }

    $Installed = Wait-Until -TimeoutSeconds 20 -Condition {
        (Test-Path -LiteralPath (Join-Path $InstallDirectory 'captionnest.exe')) -and
            (Test-Path -LiteralPath (Join-Path $InstallDirectory 'captionnest-sidecar.exe')) -and
            (Test-Path -LiteralPath (Join-Path $InstallDirectory 'uninstall.exe'))
    }
    if (-not $Installed) {
        throw 'The expected installed executables did not appear.'
    }

    $Entries = Get-CaptionNestUninstallEntries
    if ($Entries.Count -ne 1) {
        throw "Expected one uninstall entry; found $($Entries.Count)."
    }
    if ($Entries[0].DisplayVersion -ne $ExpectedVersion) {
        throw "Installed version $($Entries[0].DisplayVersion) does not match $ExpectedVersion."
    }
    if ($Entries[0].Publisher -ne 'CaptionNest contributors') {
        throw "Unexpected installer publisher: $($Entries[0].Publisher)"
    }

    $App = Start-Process `
        -FilePath (Join-Path $InstallDirectory 'captionnest.exe') `
        -PassThru
    $Started = Wait-Until -TimeoutSeconds 30 -Condition {
        $Main = Get-Process -Id $App.Id -ErrorAction SilentlyContinue
        $Sidecars = @(
            Get-CaptionNestProcesses |
                Where-Object { $_.ProcessName -eq 'captionnest-sidecar' }
        )
        $Main -and $Main.Responding -and
            $Main.MainWindowTitle -eq 'CaptionNest' -and
            $Sidecars.Count -eq 1
    }
    if (-not $Started) {
        throw 'CaptionNest did not open a responsive main window with one sidecar process.'
    }

    $Main = Get-Process -Id $App.Id -ErrorAction Stop
    if (-not $Main.CloseMainWindow()) {
        throw 'CaptionNest rejected the normal window close request.'
    }
    $Exited = Wait-Until -TimeoutSeconds 20 -Condition {
        (Get-CaptionNestProcesses).Count -eq 0
    }
    if (-not $Exited) {
        throw 'CaptionNest or its sidecar remained after the main window closed.'
    }
} catch {
    $Failure = $_
} finally {
    Stop-CaptionNestProcesses
    $Uninstaller = Join-Path $InstallDirectory 'uninstall.exe'
    if (Test-Path -LiteralPath $Uninstaller -PathType Leaf) {
        $Uninstall = Invoke-HiddenProcessWithTimeout `
            -FilePath $Uninstaller `
            -ArgumentList @('/S') `
            -TimeoutSeconds $ExternalProcessTimeoutSeconds
        if ($Uninstall.TimedOut -and -not $Failure) {
            $Failure = "Uninstaller timed out after $ExternalProcessTimeoutSeconds seconds."
        } elseif ($Uninstall.ExitCode -ne 0 -and -not $Failure) {
            $Failure = "Uninstaller failed with exit code $($Uninstall.ExitCode)."
        }
        [void](Wait-Until -TimeoutSeconds 20 -Condition {
            -not (Test-Path -LiteralPath $InstallDirectory)
        })
    }
}

$RemainingProcesses = Get-CaptionNestProcesses
$RemainingEntries = Get-CaptionNestUninstallEntries
$DirectoryExists = Test-Path -LiteralPath $InstallDirectory
if ($RemainingProcesses.Count -ne 0 -or $RemainingEntries.Count -ne 0 -or $DirectoryExists) {
    $CleanupSummary = (
        "Cleanup failed: processes=$($RemainingProcesses.Count), " +
        "registry=$($RemainingEntries.Count), directory=$DirectoryExists"
    )
    if ($Failure) {
        throw "$(Get-FailureMessage $Failure) $CleanupSummary"
    }
    throw $CleanupSummary
}
if ($Failure) {
    throw $Failure
}

Write-Host (
    "Installer smoke passed: install, launch, sidecar, normal exit, and uninstall " +
    "for CaptionNest $ExpectedVersion."
) -ForegroundColor Green
