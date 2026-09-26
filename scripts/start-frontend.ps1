# =============================================================================
# Start the Vite dev server on its own.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start-frontend.ps1
#
# Usually you want scripts\dev.ps1 instead, which starts the backend first and
# then this. The Vite proxy forwards /api to the backend, so starting this
# without one running gives a working page whose API calls fail.
#
# Runs in the background with output redirected to .dev\frontend.log and
# returns once the dev server responds. scripts\stop.ps1 stops it.
# =============================================================================
[CmdletBinding()]
param(
    # 5273 rather than Vite's default 5173, which is often already taken by
    # another project. Keep in step with the default in scripts\dev.ps1 and the
    # port in frontend\vite.config.ts.
    [int]$Port = 5273,

    # Where the Vite proxy forwards /api. Must match the backend's port, or
    # every API call from the page fails with a proxy error.
    [int]$BackendPort = 5274,

    # The host the proxy talks to. Loopback, because the backend binds there.
    [string]$BackendHost = "127.0.0.1",

    [string]$Host_ = "127.0.0.1",

    [int]$TimeoutSeconds = 60,

    [switch]$Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

# --- Preflight ---------------------------------------------------------------

$npm = Get-NpmPath
if (-not $npm) {
    Stop-WithError "npm is not on PATH, so the dev server cannot be started." -Hint @(
        "Install the LTS build from https://nodejs.org/ and re-open the terminal."
    )
}

if (-not (Test-FrontendInstalled)) {
    Write-Step "Frontend dependencies missing; running setup first..."
    & (Join-Path $PSScriptRoot "setup.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (-not (Test-FrontendInstalled)) {
        Stop-WithError "Frontend dependencies are still missing after setup."
    }
}

if ($Force) {
    Stop-SavedServer -PidFile $script:FrontendPidFile -What "frontend" | Out-Null
}
else {
    # Assigned rather than called bare: the function returns $true, and an
    # unassigned return value would be printed to the console as "True".
    $null = Resolve-PortConflict -Port $Port -PidFile $script:FrontendPidFile -What "frontend"
}

# --- Start -------------------------------------------------------------------

Initialize-StateDir
Set-Content -Path $script:FrontendLog -Value "" -Encoding utf8
Set-Content -Path $script:FrontendErrLog -Value "" -Encoding utf8

Write-Step "Starting the Vite dev server on http://${Host_}:$Port ..."

# Passed through the environment rather than a CLI flag because that is what
# vite.config.ts reads (loadEnv with an empty prefix picks up process env too).
# Without it the proxy would target the default in vite.config.ts even when the
# backend was started on a different port.
$env:VITE_BACKEND_URL = "http://${BackendHost}:$BackendPort"

$process = Start-Process `
    -FilePath $npm `
    -ArgumentList @("run", "dev", "--", "--host", $Host_, "--port", "$Port", "--strictPort") `
    -WorkingDirectory $script:FrontendDir `
    -RedirectStandardOutput $script:FrontendLog `
    -RedirectStandardError $script:FrontendErrLog `
    -PassThru `
    -WindowStyle Hidden

Save-Pid -ProcessId $process.Id -Path $script:FrontendPidFile

# --- Wait for readiness ------------------------------------------------------

$url = "http://127.0.0.1:$Port"
$ready = Wait-ForHttp -Url $url -TimeoutSeconds $TimeoutSeconds -What "frontend"

if (-not $ready) {
    Write-Err "The Vite dev server did not respond at $url."
    Show-LogTail -Path $script:FrontendErrLog -Lines 30 -What "frontend stderr"
    Show-LogTail -Path $script:FrontendLog -Lines 15 -What "frontend output"
    Stop-SavedServer -PidFile $script:FrontendPidFile -What "frontend" | Out-Null
    Stop-WithError "Startup failed. Fix the problem above, then run this script again." -Hint @(
        "Common causes:",
        "  port $Port already taken   -> pass -Port with a free port",
        "  a broken dependency       -> scripts\setup.ps1, or delete node_modules and reinstall"
    )
}

Write-Ok "Frontend is up:  $url"
Write-Info "pid $($process.Id), logs in $($script:FrontendLog)"
Write-Info "Proxying /api to http://${BackendHost}:$BackendPort"
if (-not (Test-HttpOk -Url "http://127.0.0.1:$BackendPort/api/health")) {
    Write-Warn "The backend is not answering on port $BackendPort, so API calls from the page will fail."
    Write-Info "Start it with:  scripts\start-backend.ps1"
}
Write-Info "Stop it with:   scripts\stop.ps1"
