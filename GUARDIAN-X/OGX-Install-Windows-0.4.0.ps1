[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WheelPath,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\OMEGA-GUARDIAN-X')
)

$ErrorActionPreference = 'Stop'
$resolvedWheel = (Resolve-Path -LiteralPath $WheelPath -ErrorAction Stop).Path
if (-not $resolvedWheel.EndsWith('.whl', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'WheelPath must point to a .whl package.'
}

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python launcher (py) was not found. Install Python 3.11+ first.' }
& $python.Source -3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }

$null = New-Item -ItemType Directory -Path $InstallRoot -Force
$venvPython = Join-Path $InstallRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $python.Source -3 -m venv (Join-Path $InstallRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the dedicated OGX virtual environment.' }
}

& $venvPython -m pip install --no-index --no-deps $resolvedWheel
if ($LASTEXITCODE -ne 0) { throw 'OGX wheel installation failed.' }

$launcher = Join-Path $InstallRoot 'ogx.cmd'
@'
@echo off
"%~dp0.venv\Scripts\ogx.exe" %*
'@ | Set-Content -LiteralPath $launcher -Encoding ASCII

Write-Host 'OMEGA GUARDIAN X installed outside all target repositories.' -ForegroundColor Green
Write-Host "Install directory: $InstallRoot"
Write-Host "Launcher: $launcher"
Write-Host ("Example: & `"{0}`" scan `"D:\path\to\project`"" -f $launcher)
Write-Host 'No target project files were created or modified.'
