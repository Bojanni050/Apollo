# =============================================================================
# Stop everything the startup scripts started.
#
#   powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
#
# Only processes this repo recorded a pid for are touched. Anything else
# listening on the ports is reported and left alone, because killing an
# unrelated process is not something a stop script should decide to do.
# =============================================================================
[CmdletBinding()]
param(
    # Also delete .dev\ and its logs.
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host ""
Write-Host "Stopping Apollo..." -ForegroundColor Cyan
Write-Host ""

$stoppedAny = $false

# --- Our own processes -------------------------------------------------------

if (Stop-SavedServer -PidFile $script:BackendPidFile -What "backend") {
    $stoppedAny = $true
}
else {
    Write-Info "Backend was not running (no recorded process)."
}

if (Stop-SavedServer -PidFile $script:FrontendPidFile -What "frontend") {
    $stoppedAny = $true
}
else {
    Write-Info "Frontend was not running (no recorded process)."
}

# --- Anything still holding the ports ---------------------------------------
# A process can outlive its pid file: the file is removed, the machine reboots
# mid-run, or the server was started by hand with the documented commands. Those
# are reported, never killed -- the user is told exactly which pid to stop.

# Only the default ports are reported here. A server started on some other port
# (via -Port) is still stopped correctly, through its pid file; this list only
# exists to point out a leftover listener that the pid files did not account
# for.
$ports = @(
    @{ Port = 5274; What = "backend (default port)" },
    @{ Port = 5273; What = "frontend (default port)" }
)

foreach ($entry in $ports) {
    $owner = Get-PortOwner -Port $entry.Port
    if ($null -eq $owner) { continue }

    # One exception to "never kill what we did not record": a process that is
    # unmistakably one of THIS repo's own servers (the backend through its
    # .venv python or its uvicorn command line, the frontend through node
    # running vite from this repo's node_modules) survived its pid file -- a
    # crash, .dev\ removed, a reboot without cleanup. Stopping it is what "stop
    # Apollo" means; leaving it means the next start fails with "port already
    # in use" until the pid is hunted down by hand.
    $kind = if ($entry.What -like "backend*") { "backend" } else { "frontend" }
    if (Test-IsApolloServer -ProcessId $owner -What $kind) {
        Write-Host ""
        Write-Warn "Port $($entry.Port) is held by an orphaned Apollo $kind from this repository (pid $owner). Stopping it."
        Stop-ProcessTree -ProcessId $owner -What "orphaned $kind"
        $stoppedAny = $true
        continue
    }

    Write-Host ""
    Write-Warn "Port $($entry.Port) is still in use by pid $owner -- not started by these scripts, so it was left alone."
    $details = Get-ProcessDetails -ProcessId $owner
    if ($details) {
        Write-Info "That process is: $($details.Name)"
        if ($details.ExecutablePath) { Write-Info "  executable: $($details.ExecutablePath)" }
        if ($details.CommandLine) { Write-Info "  command line: $($details.CommandLine)" }
    }
    else {
        Write-Info "Its details could not be read (it may belong to another user or have just exited)."
    }
    Write-Info "To stop it:  taskkill /PID $owner /T /F"
}

# --- Cleanup -----------------------------------------------------------------

if ($Clean) {
    Write-Host ""
    Write-Step "Removing $($script:StateDir)..."
    if (Test-Path $script:StateDir) {
        Remove-Item -Recurse -Force $script:StateDir -ErrorAction SilentlyContinue
    }
    Write-Ok "Logs and pid files removed."
}

Write-Host ""
if ($stoppedAny) {
    Write-Host "Stopped." -ForegroundColor Green
}
else {
    Write-Host "Nothing was running." -ForegroundColor Yellow
}
Write-Host ""
