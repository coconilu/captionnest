$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

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
    npm --prefix apps/web run dev
}
finally {
    # uv -> uvicorn reloader -> server worker is a process tree on Windows.
    # Killing only uv leaves old workers listening on the same inherited socket.
    Stop-ProcessTree -RootProcessId $Api.Id
}
