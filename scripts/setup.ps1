# =============================================================================
# First-run setup for Gaia Docs Architect.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# Creates the Python virtualenv, installs the backend and frontend
# dependencies, and writes a development backend/.env. Safe to re-run: each
# step is skipped when it is already done.
#
# scripts\dev.ps1 calls this automatically when something is missing, so
# running it by hand is only needed to prepare a machine in advance.
# =============================================================================
[CmdletBinding()]
param(
    # Rebuild .venv from scratch. The existing one is deleted, so anything
    # installed into it outside this project is lost.
    [switch]$Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host ""
Write-Host "Gaia Docs Architect -- setup" -ForegroundColor Cyan
Write-Host "Repository: $script:RepoRoot"
Write-Host ""

# --- Preflight ---------------------------------------------------------------

if (-not (Get-Command "python" -ErrorAction SilentlyContinue) -and
    -not (Get-Command "py" -ErrorAction SilentlyContinue)) {
    Stop-WithError "Python is not on PATH." -Hint @(
        "Install Python 3.11 or newer from https://www.python.org/downloads/",
        "and tick 'Add python.exe to PATH' during setup."
    )
}

$npm = Get-NpmPath
if (-not $npm) {
    Stop-WithError "Node.js/npm is not on PATH, so the frontend cannot be installed." -Hint @(
        "Install the LTS build from https://nodejs.org/ and re-open the terminal."
    )
}
Write-Ok "Node.js $((& node --version)) and npm $((& npm --version)) found."

# --- Backend: virtualenv -----------------------------------------------------

$python = Get-PythonPath
if ($python -and $Force) {
    Write-Step "Removing the existing virtualenv (-Force)..."
    Remove-Item -Recurse -Force $script:VenvDir
    $python = $null
}

if (-not $python) {
    Write-Step "Creating the virtualenv at .venv..."
    # `py -3` is preferred over `python` because on Windows the `python` alias
    # from the Microsoft Store can be a stub that opens the Store instead of
    # running an interpreter.
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        & py -3 -m venv $script:VenvDir
    }
    else {
        & python -m venv $script:VenvDir
    }
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "Creating the virtualenv failed."
    }
    $python = Get-PythonPath
    if (-not $python) {
        Stop-WithError "The virtualenv was created but $script:PythonExe is missing."
    }
    Write-Ok "Virtualenv ready."
}
else {
    Write-Step "Virtualenv already exists; refreshing backend dependencies..."
}

Write-Step "Installing backend dependencies (editable, with dev extras)..."
Push-Location $script:RepoRoot
try {
    & $python -m pip install --upgrade pip
    # Installed editable so that edits to backend/app take effect without a
    # reinstall. The [dev] extra pulls in pytest and friends for the test suite.
    & $python -m pip install -e ".\backend[dev]"
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "Installing the backend dependencies failed."
    }
}
finally {
    Pop-Location
}
Write-Ok "Backend dependencies installed."

# --- Backend: configuration --------------------------------------------------

if (New-DevelopmentEnv) {
    Write-Ok "Created backend\.env for local development."
    Write-Info "It uses SQLite and no authentication. Add LLM_API_KEY and LLM_MODEL there to enable chat."
}
else {
    Write-Ok "backend\.env already exists; left untouched."
}
Assert-BackendEnvSuitable

# --- Frontend ----------------------------------------------------------------

if (Test-FrontendInstalled) {
    Write-Step "Frontend dependencies already installed; refreshing with npm install..."
}
else {
    Write-Step "Installing frontend dependencies (this can take a few minutes)..."
}

Push-Location $script:FrontendDir
try {
    & $npm install
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "Installing the frontend dependencies failed." -Hint @(
            "Check your network connection and that the npm registry is reachable."
        )
    }
}
finally {
    Pop-Location
}
Write-Ok "Frontend dependencies installed."

# --- Summary -----------------------------------------------------------------

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Start the whole app:   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1"
Write-Host "  Or just double-click:  start.cmd"
Write-Host "  Stop everything:       powershell -ExecutionPolicy Bypass -File scripts\stop.ps1"
Write-Host "  Run the tests:         powershell -ExecutionPolicy Bypass -File scripts\test.ps1"
Write-Host ""
