# =============================================================================
# Run the test suites.
#
#   powershell -ExecutionPolicy Bypass -File scripts\test.ps1
#
# Backend:  pytest from backend/, with pythonpath=["."] from pyproject.toml so
#           the `app` package imports without an install.
# Frontend: `npm run build`, which is `tsc --noEmit && vite build` -- a full
#           type check plus a production build, since the frontend has no unit
#           tests. The Playwright smoke scripts need a running app; start it
#           with scripts\dev.ps1 first and run them from frontend/ by hand.
#
# The PostgreSQL-marked tests are skipped unless TEST_DATABASE_URL is set, so
# this works with no database server. See README.md for the Docker setup.
# =============================================================================
[CmdletBinding()]
param(
    # Backend only, or frontend only.
    [ValidateSet("all", "backend", "frontend")]
    [string]$Target = "all",

    # Extra arguments passed through to pytest.
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host ""
Write-Host "Gaia Docs Architect -- tests" -ForegroundColor Cyan
Write-Host ""

$failed = $false

# --- Backend -----------------------------------------------------------------

if ($Target -eq "all" -or $Target -eq "backend") {
    $python = Get-PythonPath
    if (-not $python) {
        Stop-WithError "No .venv virtualenv, so the backend tests cannot run." -Hint @(
            "Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1"
        )
    }

    Write-Step "Backend tests (pytest)"
    Push-Location $script:BackendDir
    try {
        & $python -m pytest @PytestArgs
        if ($LASTEXITCODE -ne 0) {
            $failed = $true
            Write-Err "Backend tests failed (exit $LASTEXITCODE)."
        }
        else {
            Write-Ok "Backend tests passed."
        }
    }
    finally {
        Pop-Location
    }
    Write-Host ""
}

# --- Frontend ----------------------------------------------------------------

if ($Target -eq "all" -or $Target -eq "frontend") {
    $npm = Get-NpmPath
    if (-not $npm) {
        Stop-WithError "npm is not on PATH, so the frontend build cannot run." -Hint @(
            "Install the LTS build from https://nodejs.org/."
        )
    }

    if (-not (Test-FrontendInstalled)) {
        Stop-WithError "Frontend dependencies are not installed." -Hint @(
            "Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1"
        )
    }

    Write-Step "Frontend type check and production build (npm run build)"
    Push-Location $script:FrontendDir
    try {
        & $npm run build
        if ($LASTEXITCODE -ne 0) {
            $failed = $true
            Write-Err "Frontend build failed (exit $LASTEXITCODE)."
        }
        else {
            Write-Ok "Frontend build passed."
        }
    }
    finally {
        Pop-Location
    }
    Write-Host ""
}

if ($failed) {
    Write-Host "Some tests failed." -ForegroundColor Red
    Write-Host ""
    exit 1
}

Write-Host "All tests passed." -ForegroundColor Green
Write-Host ""
exit 0
