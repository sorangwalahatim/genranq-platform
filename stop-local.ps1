# Stops local Genranq services (Uvicorn and PostgreSQL)
$ErrorActionPreference = "SilentlyContinue"

Write-Host "Stopping Uvicorn and Python processes for Genranq..." -ForegroundColor Yellow
Get-Process -Name uvicorn, python | Where-Object { $_.Path -like "*genranq*" } | Stop-Process -Force

$Root = $PSScriptRoot
$ParentDir = Split-Path -Parent $PSScriptRoot
$PgRoot = if (Test-Path (Join-Path $ParentDir "pgsql")) { Join-Path $ParentDir "pgsql" } else { Join-Path $Root "pgsql" }
$PgBin = Join-Path $PgRoot "bin"
$PgData = Join-Path $PgRoot "data"

if (Test-Path "$PgBin\pg_ctl.exe") {
    Write-Host "Stopping PostgreSQL..." -ForegroundColor Yellow
    & "$PgBin\pg_ctl.exe" -D "$PgData" stop -m fast
}

Write-Host "All Genranq local services stopped." -ForegroundColor Green
