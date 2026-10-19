# Starts Genranq locally (PostgreSQL + FastAPI backend + React SPA frontend)
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$ParentDir = Split-Path -Parent $PSScriptRoot

$PgRoot = if (Test-Path (Join-Path $ParentDir "pgsql")) { Join-Path $ParentDir "pgsql" } else { Join-Path $Root "pgsql" }
$PgBin = Join-Path $PgRoot "bin"
$PgData = Join-Path $PgRoot "data"
$PgLog = Join-Path $PgRoot "server.log"

$BackendDir = if (Test-Path (Join-Path $Root "backend")) { Join-Path $Root "backend" } else { Join-Path $ParentDir "backend" }
$VenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
$VenvUvicorn = Join-Path $BackendDir ".venv\Scripts\uvicorn.exe"

# 1. Start PostgreSQL if not already running
$pgProc = Get-Process -Name postgres -ErrorAction SilentlyContinue
if (-not $pgProc) {
    Write-Host "Starting PostgreSQL database server..." -ForegroundColor Cyan
    & "$PgBin\pg_ctl.exe" -D "$PgData" -l "$PgLog" start
    for ($i = 0; $i -lt 30; $i++) {
        & "$PgBin\pg_isready.exe" -h localhost -p 5432 -q
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 1
    }
}
Write-Host "PostgreSQL is ready." -ForegroundColor Green

# 2. Start Genranq Backend + Frontend (single-port SPA on :8000)
Write-Host "Starting Genranq on http://localhost:8000..." -ForegroundColor Cyan
$env:EMBEDDED_WORKER = "1"
Set-Location $BackendDir
Start-Process "http://localhost:8000"
& "$VenvUvicorn" app.main:app --host 0.0.0.0 --port 8000
