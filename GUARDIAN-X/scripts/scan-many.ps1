[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string[]]$RepositoryPaths,

    [switch]$Strict
)

$ErrorActionPreference = 'Stop'
$launcher = Join-Path $env:LOCALAPPDATA 'Programs\OMEGA-GUARDIAN-X\ogx.cmd'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    $command = Get-Command ogx -ErrorAction SilentlyContinue
    if (-not $command) { throw 'OMEGA GUARDIAN X is not installed. Run the Windows installer first.' }
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
    Write-Host "`n========== OMEGA GUARDIAN X SCAN: $resolved ==========" -ForegroundColor Cyan
    $ogxArgs = @('scan', $resolved)
    if ($Strict) { $ogxArgs += '--strict' }
    & $launcher @ogxArgs
    if ($LASTEXITCODE -ne 0) {
        $failed += $resolved
        Write-Warning "Scan returned exit code $LASTEXITCODE for $resolved"
    }
}
if ($failed.Count -gt 0) {
    Write-Host "`nSome repositories need attention:" -ForegroundColor Yellow
    $failed | ForEach-Object { Write-Host " - $_" }
    exit 1
}
Write-Host "`nAll requested repository scans completed." -ForegroundColor Green
