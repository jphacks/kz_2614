#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$requiredPythonVersion = [Version]'3.11'
$unsupportedPythonVersion = [Version]'3.14'
$pythonVersionProbe = "import sys; print('%d.%d.%d' % sys.version_info[:3])"
$serverSideRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$requirementsFile = Join-Path $serverSideRoot 'requirements.txt'
$venvPython = Join-Path $serverSideRoot '.venv\Scripts\python.exe'

function Refresh-ProcessPath {
    $pathParts = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine')
        [Environment]::GetEnvironmentVariable('Path', 'User')
        $env:Path
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $env:Path = ($pathParts -split ';' | Where-Object { $_ } | Select-Object -Unique) -join ';'
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$Arguments = @()
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Executable"
    }
}

function Install-WinGetPackage {
    param([Parameter(Mandatory = $true)][string]$PackageId)

    $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw 'winget is required to install Git or Python. Install App Installer, then run this script again.'
    }

    Invoke-CheckedCommand -Executable $winget.Source -Arguments @(
        'install', '--id', $PackageId, '--exact', '--silent',
        '--accept-source-agreements', '--accept-package-agreements'
    )
    Refresh-ProcessPath
}

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$Prefix = @()
    )

    $invokeArguments = @($Prefix) + @('-c', $pythonVersionProbe)
    $versionText = & $Executable @invokeArguments 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $versionText) {
        return $null
    }

    try {
        $version = [Version]::Parse(($versionText | Select-Object -Last 1).Trim())
    }
    catch {
        return $null
    }

    if ($version -lt $requiredPythonVersion -or $version -ge $unsupportedPythonVersion) {
        return $null
    }

    return [PSCustomObject]@{
        Executable = $Executable
        Prefix = @($Prefix)
        Version = $version
    }
}

function Find-Python {
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($selector in @('-3.13', '-3.12', '-3.11')) {
            $candidate = Test-PythonCandidate -Executable $launcher.Source -Prefix @($selector)
            if ($candidate) {
                return $candidate
            }
        }
    }

    foreach ($commandName in @('python.exe', 'python3.exe')) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch '\\WindowsApps\\' } |
            Select-Object -First 1
        if ($command) {
            $candidate = Test-PythonCandidate -Executable $command.Source
            if ($candidate) {
                return $candidate
            }
        }
    }

    return $null
}

if (-not (Test-Path -LiteralPath $requirementsFile)) {
    throw "ServerSide requirements file was not found: $requirementsFile"
}

$git = Get-Command 'git.exe' -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Host 'Git was not found. Installing Git for Windows with winget...'
    Install-WinGetPackage -PackageId 'Git.Git'
    $git = Get-Command 'git.exe' -ErrorAction SilentlyContinue
}

if (-not $git) {
    $gitCandidates = @(
        (Join-Path $env:ProgramFiles 'Git\cmd\git.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe')
    )
    $gitPath = $gitCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($gitPath) {
        $env:Path = (Split-Path -Parent $gitPath) + ';' + $env:Path
        $git = Get-Command 'git.exe' -ErrorAction SilentlyContinue
    }
}

if (-not $git) {
    throw 'Git installation completed, but git.exe is not available. Open a new PowerShell window and run this script again.'
}
Invoke-CheckedCommand -Executable $git.Source -Arguments @('--version')

$python = Find-Python
if (-not $python) {
    Write-Host 'A supported Python version (3.11-3.13) was not found. Installing Python 3.11 with winget...'
    Install-WinGetPackage -PackageId 'Python.Python.3.11'
    $python = Find-Python
}

if (-not $python) {
    throw 'Python 3.11-3.13 is still unavailable. Open a new PowerShell window and run this script again.'
}
Write-Host "Using Python $($python.Version)"

if (Test-Path -LiteralPath $venvPython) {
    $existingVenv = Test-PythonCandidate -Executable $venvPython
    if (-not $existingVenv) {
        throw "The existing virtual environment uses Python outside the supported 3.11-3.13 range: $venvPython. Keep it intact and recreate it manually before rerunning this script."
    }
}
else {
    $venvArguments = @($python.Prefix) + @('-m', 'venv', (Join-Path $serverSideRoot '.venv'))
    Invoke-CheckedCommand -Executable $python.Executable -Arguments $venvArguments
}

Invoke-CheckedCommand -Executable $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-CheckedCommand -Executable $venvPython -Arguments @('-m', 'pip', 'install', '-r', $requirementsFile)

Write-Host "Setup complete. Virtual environment: $(Join-Path $serverSideRoot '.venv')"
