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
#   2. npm install (repository root) -- @tauri-apps/cli, which desktop.cmd
#      runs through `npm run dev`. Without it that fails with "'tauri' is not
#      recognized as an internal or external command".
#   3. npm run build -- the production bundle at frontend\dist.
#
# The root install and the build are the steps that setup alone does not do.
# Without the build the desktop app fails to start: the Rust shell runs
# `python -m app.serve`, which mounts frontend\dist and refuses to start with
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

# npm is needed by both remaining steps, so resolve it once, up front.
$npm = Get-NpmPath
if (-not $npm) {
    Stop-WithError "npm is not on PATH, so the root install and the frontend build cannot run." -Hint @(
        "Install the LTS build from https://nodejs.org/ and re-open the terminal."
    )
}

# --- 2. Desktop shell: @tauri-apps/cli at the repository root ------------------
# A tiny install (one package), but without it `npm run dev` in desktop.cmd
# fails with "'tauri' is not recognized as an internal or external command".
# setup.ps1 only installs frontend\node_modules, and package.json at the root
# is a different tree.
$tauriCli = Join-Path $script:RepoRoot "node_modules\.bin\tauri.cmd"
if (Test-Path $tauriCli) {
    Write-Step "The Tauri CLI is installed; refreshing the root install..."
}
else {
    Write-Step "Installing the root npm dependencies (the Tauri CLI for desktop.cmd)..."
}

Push-Location $script:RepoRoot
try {
    & $npm install
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "Installing the root npm dependencies failed." -Hint @(
            "Check your network connection and that the npm registry is reachable."
        )
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path $tauriCli)) {
    Stop-WithError "npm install reported success but $tauriCli is missing."
}
Write-Ok "Tauri CLI ready: $tauriCli"

# --- 3. Build the frontend bundle ---------------------------------------------
# `npm run build` runs `tsc --noEmit && vite build`, so a type error fails
# the build here -- with the file and line -- rather than as a blank page
# inside the desktop window.
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
