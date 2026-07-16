$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

& (Join-Path $PSScriptRoot 'stop-local-services.ps1') -Scope All

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

$Api = Start-Process uv -ArgumentList @(
    'run', '--project', 'apps/sidecar', '--extra', 'asr',
    'uvicorn', 'sublingo_local.app:app',
    '--host', '127.0.0.1', '--port', '8765', '--reload'
) -WorkingDirectory $Root -PassThru -WindowStyle Hidden

try {
    $deadline = (Get-Date).AddSeconds(30)
    $ready = $false
    do {
        Start-Sleep -Milliseconds 300
        if ($Api.HasExited) {
            throw "CaptionNest API exited before it became ready (exit code $($Api.ExitCode))."
        }
        try {
            $health = Invoke-RestMethod `
                -Uri 'http://127.0.0.1:8765/api/health' `
                -TimeoutSec 2 `
                -ErrorAction Stop
            $ready = $health.status -eq 'ok'
        }
        catch {
            $ready = $false
        }
    } while (-not $ready -and (Get-Date) -lt $deadline)

    if (-not $ready) { throw 'CaptionNest API did not become ready within 30 seconds.' }

    & npm --prefix apps/web run dev -- --host 127.0.0.1 --port 5175 --strictPort
    if ($LASTEXITCODE -ne 0) { throw 'CaptionNest web development server exited with an error.' }
}
finally {
    # uv -> uvicorn reloader -> server worker is a process tree on Windows.
    # Killing only uv leaves old workers listening on the same inherited socket.
    Stop-ProcessTree -RootProcessId $Api.Id
}
