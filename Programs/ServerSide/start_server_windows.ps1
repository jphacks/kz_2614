#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$BindAddress = '127.0.0.1',
    [ValidateRange(1, 65535)][int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$serverSideRoot = $PSScriptRoot
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $serverSideRoot '..\..')).Path
$python = Join-Path $serverSideRoot '.venv\Scripts\python.exe'
$serverModule = Join-Path $serverSideRoot 'dashboard\server.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw "ServerSide virtual environment was not found. Run the setup script first: Programs/ServerSide/STTLLMTTS/third_party/setup_windows.ps1"
}
if (-not (Test-Path -LiteralPath $serverModule)) {
    throw "ServerSide HTTP entry point was not found: $serverModule"
}
if (-not (Test-Path -LiteralPath (Join-Path $serverSideRoot '.env'))) {
    Write-Warning 'Programs/ServerSide/.env was not found. The server will use its defaults; copy .env.example if you need custom settings.'
}

Write-Host "Starting the ServerSide web application on ${BindAddress}:$Port. Press Ctrl+C to stop."
if ($BindAddress -ne '127.0.0.1' -and $BindAddress -ne '::1' -and $BindAddress -ne 'localhost') {
    Write-Host 'Use a restricted private network for non-loopback binding.'
}

$exitCode = 1
Push-Location -LiteralPath $repoRoot
try {
    & $python -m Programs.ServerSide.dashboard.server --host $BindAddress --port $Port
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
