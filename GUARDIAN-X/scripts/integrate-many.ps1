[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string[]]$RepositoryPaths,

    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$launcher = Join-Path $env:LOCALAPPDATA 'Programs\OMEGA-GUARDIAN-X\ogx.cmd'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    $command = Get-Command ogx -ErrorAction SilentlyContinue
    if (-not $command) {
        throw 'OMEGA GUARDIAN X is not installed. Run the Windows installer first.'
    }
    $launcher = $command.Source
}

$failed = @()
foreach ($repo in $RepositoryPaths) {
    $resolved = (Resolve-Path -LiteralPath $repo -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
        Write-Warning "Skipping non-directory: $resolved"
        $failed += $resolved
        continue
    }

    Write-Host "`n========== OGX INTEGRATION PLAN: $resolved ==========" -ForegroundColor Cyan
    & $launcher integrate plan $resolved
    if ($LASTEXITCODE -ne 0) {
        $failed += $resolved
        Write-Warning "Plan failed for $resolved"
        continue
    }

    if ($Apply) {
        $answer = Read-Host "Type APPLY to create only missing OGX control files in this repository"
        if ($answer -ceq 'APPLY') {
            & $launcher integrate apply $resolved --confirm
            if ($LASTEXITCODE -ne 0) {
                $failed += $resolved
                Write-Warning "Integration failed for $resolved"
            }
        } else {
            Write-Host 'Skipped; no project files changed.' -ForegroundColor Yellow
        }
    }
}

if ($failed.Count -gt 0) {
    Write-Host "`nSome repositories could not be processed:" -ForegroundColor Yellow
    $failed | ForEach-Object { Write-Host " - $_" }
    exit 1
}
Write-Host "`nOGX integration planning completed." -ForegroundColor Green
