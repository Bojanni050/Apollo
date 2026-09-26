# =============================================================================
# Start the desktop app.
#
#   powershell -ExecutionPolicy Bypass -File scripts\desktop.ps1
#   apollo.cmd desktop
#
# The Tauri 2 app in src-tauri\ starts the Python API as a child process,
# waits for it to answer /api/health, and opens a native window on it. The
# API is stopped when the window closes.
#
# Expects scripts\first-run.ps1 to have run (apollo.cmd desktop does that
# automatically). Checks the prerequisites and reports what is missing with
# a clear hint, rather than failing with a wall of tool errors.
# =============================================================================
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host ""
Write-Host "Apollo -- desktop app" -ForegroundColor Cyan
Write-Host "Repository: $script:RepoRoot"
Write-Host ""

# --- Prerequisites ---------------------------------------------------------------
$python = Get-PythonPath
if (-not $python) {
    Stop-WithError "No virtualenv found at $script:VenvDir" -Hint @(
        "Run apollo.cmd desktop (or first-run.cmd) first: it installs",
        "everything and builds the frontend bundle."
    )
}

$tauriCli = Join-Path $script:RepoRoot "node_modules\.bin\tauri.cmd"
if (-not (Test-Path $tauriCli)) {
    Stop-WithError "The Tauri CLI is not installed at node_modules\.bin\tauri.cmd" -Hint @(
        "Run apollo.cmd desktop (or first-run.cmd) first: it installs the Tauri CLI."
    )
}

if (-not (Get-Command "cargo" -ErrorAction SilentlyContinue)) {
    Stop-WithError "Rust is not installed, so the desktop app cannot be built." -Hint @(
        "Install it from https://rustup.rs/ (MSVC host toolchain),",
        "then re-open the terminal and run this again.",
        "",
        "The app still runs without Rust:  apollo.cmd start"
    )
}
Write-Ok "Prerequisites found (venv, Tauri CLI, Rust)."

# --- Start -----------------------------------------------------------------------
Write-Step "Starting the desktop app (first start compiles the Rust shell; that takes a while)..."
$npm = Get-NpmPath
if (-not $npm) {
    Stop-WithError "npm is not on PATH, so the desktop app cannot start."
}

Push-Location $script:RepoRoot
try {
    & $npm run dev
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($code -ne 0) {
    Write-Host ""
    Write-Warn "The desktop app exited with code $code."
    Write-Info "`apollo.cmd release` gives a fuller error, or use `apollo.cmd start`"
    Write-Info "for the browser version."
    exit $code
}
Write-Ok "Desktop app closed."
