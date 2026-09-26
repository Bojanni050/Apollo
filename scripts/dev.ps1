# =============================================================================
# Start the whole app: backend first, then the frontend, then the browser.
#
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
#   start.cmd
#
# Order matters. The Vite dev server proxies /api to the backend, so the page
# renders either way but every request fails until the backend is listening.
# This script waits for the backend to answer /api/health before starting the
# frontend, so the page is working the moment it opens.
#
# Both servers run in the background with logs in .dev\, so this script returns
# as soon as the app is ready. Stop them with scripts\stop.ps1.
# =============================================================================
[CmdletBinding()]
param(
    # 5273/5274 rather than Vite's 5173 and uvicorn's 8000: on a developer
    # machine those defaults are commonly taken by other projects, and a port
    # clash is the single most annoying thing about a two-process dev setup.
    # The two are adjacent so they are easy to remember together.
    [int]$BackendPort = 5274,
    [int]$FrontendPort = 5273,
    [string]$Host_ = "127.0.0.1",

    # Do not launch a browser. Useful over SSH, in CI, or when you want to open
    # the page yourself.
    [switch]$NoBrowser,

    # Start even if the ports are already taken by a previous run.
    [switch]$Force,

    # Stop whatever this repo started, then exit.
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host ""
Write-Host "Gaia Docs Architect" -ForegroundColor Cyan
Write-Host "Repository: $script:RepoRoot"
Write-Host ""

# --- --Stop: delegate and exit -----------------------------------------------

if ($Stop) {
    & (Join-Path $PSScriptRoot "stop.ps1")
    exit $LASTEXITCODE
}

# --- Preflight ---------------------------------------------------------------

$problems = @()

if (-not (Get-PythonPath)) {
    $problems += "No .venv virtualenv."
}
elseif (-not (Test-BackendInstalled)) {
    $problems += "Backend dependencies are not installed in .venv."
}

if (-not (Get-NpmPath)) {
    $problems += "npm is not on PATH."
}
elseif (-not (Test-FrontendInstalled)) {
    $problems += "Frontend dependencies are not installed (no node_modules)."
}

if ($problems.Count -gt 0) {
    Write-Step "Setup needed: $($problems -join ' ')"
    Write-Info "Running scripts\setup.ps1 (skipped automatically when already done)..."
    Write-Host ""
    & (Join-Path $PSScriptRoot "setup.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host ""
}

# --- Start the backend -------------------------------------------------------

Write-Step "Starting the backend..."
& (Join-Path $PSScriptRoot "start-backend.ps1") -Port $BackendPort -Host_ $Host_ -Force:$Force
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Err "The backend did not start, so the frontend was not started."
    Write-Info "Fix the error above and run this script again."
    exit $LASTEXITCODE
}
Write-Host ""

# --- Start the frontend ------------------------------------------------------
# Skipped rather than aborted if it fails: the API is useful on its own (the
# interactive /docs page), and the user may prefer to run the frontend
# themselves with more logging.

Write-Step "Starting the frontend..."
& (Join-Path $PSScriptRoot "start-frontend.ps1") `
    -Port $FrontendPort `
    -BackendPort $BackendPort `
    -Host_ $Host_ `
    -Force:$Force

$frontendUp = ($LASTEXITCODE -eq 0)

# --- Done --------------------------------------------------------------------

$appUrl = "http://127.0.0.1:$FrontendPort"
$apiUrl = "http://127.0.0.1:$BackendPort"

Write-Host ""
if ($frontendUp) {
    Write-Host "The app is running." -ForegroundColor Green
}
else {
    Write-Host "The backend is running, but the frontend failed to start." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  App      $appUrl" -ForegroundColor Cyan
Write-Host "  API      $apiUrl"
Write-Host "  API docs $apiUrl/docs"
Write-Host "  Logs     $script:StateDir"
Write-Host "  Stop     powershell -ExecutionPolicy Bypass -File scripts\stop.ps1"
Write-Host ""

if ($frontendUp -and -not $NoBrowser) {
    Open-Browser -Url $appUrl
}
