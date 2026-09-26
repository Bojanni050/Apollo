# =============================================================================
# Start the FastAPI backend on its own.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
#
# Usually you want scripts\dev.ps1 instead, which starts this and the frontend
# in the right order. Use this one when iterating on backend code with the
# frontend already running, or when the API is all you need.
#
# The server runs in the background with output redirected to .dev\backend.log,
# so this script returns once /api/health responds. Ctrl+C here does NOT stop
# it -- run scripts\stop.ps1 for that.
# =============================================================================
[CmdletBinding()]
param(
    # 5274 rather than uvicorn's default 8000, which is often already taken by
    # something else on a developer machine. Keep in step with the default in
    # scripts\dev.ps1 and the proxy target in frontend\vite.config.ts.
    [int]$Port = 5274,

    # Bind address. 127.0.0.1 (the default) keeps the API -- which can read and
    # write Markdown files on this machine -- off the network. Changing this is
    # a deliberate act and should not be done casually.
    [string]$Host_ = "127.0.0.1",

    # Seconds to wait for /api/health before giving up.
    [int]$TimeoutSeconds = 60,

    # Start even if something is already listening on the port.
    [switch]$Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

# --- Preflight ---------------------------------------------------------------

$python = Get-PythonPath
if (-not $python) {
    Write-Step "No virtualenv found; running setup first..."
    & (Join-Path $PSScriptRoot "setup.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $python = Get-PythonPath
    if (-not $python) { Stop-WithError "The virtualenv is still missing after setup." }
}

if (-not (Test-BackendInstalled)) {
    Stop-WithError "The backend dependencies are not installed in .venv." -Hint @(
        "Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1"
    )
}

# Generate a development .env on first run. Done here as well as in setup.ps1
# so that starting the app on a machine where the dependencies already exist
# still works: without it the backend defaults to APP_ENV=production and
# refuses to start, which is correct behaviour but a confusing first run.
if (New-DevelopmentEnv) {
    Write-Ok "Created backend\.env for local development."
    Write-Info "It uses SQLite and no authentication. Add LLM_API_KEY and LLM_MODEL there to enable chat."
}

Assert-BackendEnvSuitable

if ($Force) {
    Stop-SavedServer -PidFile $script:BackendPidFile -What "backend" | Out-Null
}
else {
    # Assigned rather than called bare: the function returns $true, and an
    # unassigned return value would be printed to the console as "True".
    $null = Resolve-PortConflict -Port $Port -PidFile $script:BackendPidFile -What "backend"
}

# --- Start -------------------------------------------------------------------

Initialize-StateDir
# Truncate the previous run's logs. Without this, a failure below is diagnosed
# against output from an earlier, successful start.
Set-Content -Path $script:BackendLog -Value "" -Encoding utf8
Set-Content -Path $script:BackendErrLog -Value "" -Encoding utf8

Write-Step "Starting the backend on http://${Host_}:$Port ..."

# `python -m uvicorn` rather than the uvicorn.exe shim: it guarantees the
# interpreter running the server is this project's virtualenv, and it keeps the
# module importable as `app.main` because the working directory is backend/.
$arguments = @(
    "-m", "uvicorn", "app.main:app",
    "--host", $Host_,
    "--port", "$Port",
    "--reload"
)

$process = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $script:BackendDir `
    -RedirectStandardOutput $script:BackendLog `
    -RedirectStandardError $script:BackendErrLog `
    -PassThru `
    -WindowStyle Hidden

Save-Pid -ProcessId $process.Id -Path $script:BackendPidFile

# --- Wait for readiness ------------------------------------------------------

$healthUrl = "http://127.0.0.1:$Port/api/health"
$ready = Wait-ForHttp -Url $healthUrl -TimeoutSeconds $TimeoutSeconds -What "backend"

if (-not $ready) {
    Write-Err "The backend did not become healthy at $healthUrl."
    Show-LogTail -Path $script:BackendErrLog -Lines 30 -What "backend stderr"
    Stop-SavedServer -PidFile $script:BackendPidFile -What "backend" | Out-Null
    Stop-WithError "Startup failed. Fix the problem above, then run this script again." -Hint @(
        "Common causes:",
        "  APP_ENV=production in backend\.env  -> set it to development",
        "  a database that is not reachable    -> check DATABASE_URL",
        "  a missing LLM_API_KEY is fine and does not block startup"
    )
}

Write-Ok "Backend is up:  $healthUrl"
Write-Info "pid $($process.Id), logs in $($script:BackendLog)"
Write-Info "API docs:       http://${Host_}:$Port/docs"
Write-Info "Stop it with:   scripts\stop.ps1"
