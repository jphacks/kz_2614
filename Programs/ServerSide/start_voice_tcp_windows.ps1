param([string]$BindAddress = '127.0.0.1', [ValidateRange(1,65535)][int]$Port = 8766)
$ErrorActionPreference = 'Stop'
$Python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$Launcher = Join-Path $PSScriptRoot '../../scripts/start_voice_tcp.py'
& $Python $Launcher --host $BindAddress --port $Port
exit $LASTEXITCODE
