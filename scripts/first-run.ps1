# =============================================================================
# First run of Apollo: install everything, then build the frontend bundle.
#
#   powershell -ExecutionPolicy Bypass -File scripts\first-run.ps1
#   first-run.cmd   (or double-click first-run.bat)
#
# Everything a fresh clone needs before the app can start, in one command:
#
#   1. scripts\setup.ps1 -- the Python virtualenv, the backend packages,
#      the frontend packages and a development backend\.env. Every step is
#      skipped when it is already done.
#   2. npm run build -- the production bundle at frontend\dist.
#
# The build is the step that setup alone does not do, and without it the
# desktop app fails to start: the Rust shell runs `python -m app.serve`,
# which mounts frontend\dist and refuses to start with
# "No frontend build at ...\frontend\dist" until this script has run once.
#
# Safe to re-run: the install steps skip what is done, and the build simply
# produces a fresh bundle.
# =============================================================================
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host ""
Write-Host "Apollo -- first run" -ForegroundColor Cyan
Write-Host "Repository: $script:RepoRoot"
Write-Host ""

# --- 1. Install: virtualenv, backend, frontend, .env --------------------------
# Delegated rather than duplicated: setup.ps1 already skips anything that
# exists, so on a half-prepared machine this only does what is missing.
Write-Step "Installing dependencies (each step is skipped when already done)..."
& (Join-Path $PSScriptRoot "setup.ps1")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
Write-Host ""

# --- 2. Build the frontend bundle ---------------------------------------------
# `npm run build` runs `tsc --noEmit && vite build`, so a type error fails
# the build here -- with the file and line -- rather than as a blank page
# inside the desktop window.
$npm = Get-NpmPath
if (-not $npm) {
    Stop-WithError "npm is not on PATH, so the frontend cannot be built." -Hint @(
        "Install the LTS build from https://nodejs.org/ and re-open the terminal."
    )
}

$distIndex = Join-Path $script:FrontendDir "dist\index.html"
if (Test-Path $distIndex) {
    Write-Step "A frontend build exists; rebuilding it..."
}
else {
    Write-Step "Building the frontend bundle (the desktop app serves frontend\dist)..."
}

Push-Location $script:FrontendDir
try {
    & $npm run build
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "Building the frontend failed." -Hint @(
            "The output above names the file and line for TypeScript errors.",
            "Fix them, then run this script again."
        )
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path $distIndex)) {
    Stop-WithError "The build reported success but $distIndex is missing."
}
Write-Ok "Frontend bundle ready: $distIndex"

# --- Summary ------------------------------------------------------------------
Write-Host ""
Write-Host "First run complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Start the browser version:  start.cmd"
Write-Host "  Start the desktop app:     desktop.cmd   (also needs Rust + MSVC build tools)"
Write-Host "  Stop everything:           stop.cmd"
Write-Host ""
Write-Host "  Re-run this script any time; it refreshes the install and the build."
Write-Host ""
