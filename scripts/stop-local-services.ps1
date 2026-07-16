param(
    [ValidateSet('Web', 'Api', 'All')]
    [string]$Scope = 'All'
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)

    $children = Get-CimInstance Win32_Process `
        -Filter "ParentProcessId = $RootProcessId" `
        -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -RootProcessId ([int]$child.ProcessId)
    }
    Stop-Process -Id $RootProcessId -Force -ErrorAction SilentlyContinue
}

function Test-IsCaptionNestProcess {
    param(
        [Parameter(Mandatory = $true)]$Process,
        [Parameter(Mandatory = $true)][ValidateSet('Web', 'Api')][string]$Kind
    )

    $commandLine = [string]$Process.CommandLine
    if ([string]::IsNullOrWhiteSpace($commandLine)) { return $false }
    if ($commandLine.IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        return $false
    }

    if ($Kind -eq 'Web') {
        return $commandLine -match '(?i)[\\/]vite[\\/]bin[\\/]vite\.js'
    }
    return $commandLine -match '(?i)uvicorn' `
        -and $commandLine -match '(?i)sublingo_local\.app:app'
}

$Kinds = if ($Scope -eq 'All') { @('Web', 'Api') } else { @($Scope) }
$Processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
$Targets = foreach ($process in $Processes) {
    foreach ($kind in $Kinds) {
        if (Test-IsCaptionNestProcess -Process $process -Kind $kind) {
            $process
            break
        }
    }
}

foreach ($target in @($Targets | Sort-Object ProcessId -Unique)) {
    Write-Host "Stopping previous CaptionNest process $($target.ProcessId) ($($target.Name))..."
    Stop-ProcessTree -RootProcessId ([int]$target.ProcessId)
}

$Ports = @()
if ($Kinds -contains 'Web') { $Ports += 5175 }
if ($Kinds -contains 'Api') { $Ports += 8765 }

foreach ($port in $Ports) {
    $deadline = (Get-Date).AddSeconds(8)
    do {
        $listener = Get-NetTCPConnection `
            -LocalAddress '127.0.0.1' `
            -LocalPort $port `
            -State Listen `
            -ErrorAction SilentlyContinue
        if (-not $listener) { break }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)

    if ($listener) {
        $owner = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $($listener.OwningProcess)" `
            -ErrorAction SilentlyContinue
        $description = if ($owner) {
            "$($owner.Name) (PID $($owner.ProcessId))"
        }
        else {
            "PID $($listener.OwningProcess)"
        }
        throw "Port $port is occupied by $description. It is not a CaptionNest process, so it was not stopped."
    }
}
