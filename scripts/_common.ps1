
# =============================================================================
# Shared helpers for the Apollo startup scripts.
#
# Dot-sourced by setup.ps1, start-backend.ps1, start-frontend.ps1, dev.ps1 and
# stop.ps1. Not meant to be run directly.
#
# Why these scripts exist: the app is two processes (a FastAPI backend and a
# Vite dev server) that must be started in the right order, from the right
# working directory, with the right environment. Getting that wrong produces
# confusing failures -- a proxy error because the backend is not up yet, a
# security configuration error because APP_ENV was never set, or an
# "Address already in use" from a previous run that was never stopped. The
# scripts do the boring parts so that starting the app is one command.
# =============================================================================

Set-StrictMode -Version Latest

# --- Repository layout -------------------------------------------------------

# scripts/ lives directly under the repository root.
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:BackendDir = Join-Path $script:RepoRoot "backend"
$script:FrontendDir = Join-Path $script:RepoRoot "frontend"
$script:VenvDir = Join-Path $script:RepoRoot ".venv"
$script:VenvScripts = Join-Path $script:VenvDir "Scripts"
$script:PythonExe = Join-Path $script:VenvScripts "python.exe"
$script:UvicornExe = Join-Path $script:VenvScripts "uvicorn.exe"
$script:BackendEnvFile = Join-Path $script:BackendDir ".env"
$script:BackendEnvExample = Join-Path $script:BackendDir ".env.example"

# Runtime state: logs and pid files. Under the repo so they are easy to find and
# delete, and named so .gitignore can exclude them.
$script:StateDir = Join-Path $script:RepoRoot ".dev"
$script:BackendPidFile = Join-Path $script:StateDir "backend.pid"
$script:FrontendPidFile = Join-Path $script:StateDir "frontend.pid"
$script:BackendLog = Join-Path $script:StateDir "backend.log"
$script:BackendErrLog = Join-Path $script:StateDir "backend.err.log"
$script:FrontendLog = Join-Path $script:StateDir "frontend.log"
$script:FrontendErrLog = Join-Path $script:StateDir "frontend.err.log"

# --- Console output ----------------------------------------------------------
# Status prefixes rather than colour, so the output stays readable when
# redirected to a file or shown in a terminal with an unusual palette.

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "  [ok] $Message" -ForegroundColor Green }
function Write-Info { param([string]$Message) Write-Host "  $Message" }
function Write-Warn { param([string]$Message) Write-Host "  [!] $Message" -ForegroundColor Yellow }
function Write-Err  { param([string]$Message) Write-Host "  [x] $Message" -ForegroundColor Red }

function Stop-WithError {
    <#
      .SYNOPSIS
        Print a message and exit with a non-zero status.
      .DESCRIPTION
        Always exits, so callers do not have to remember to follow an error
        with an `exit`. The exit code is 1 unless given.
    #>
    param(
        [Parameter(Mandatory)][string]$Message,
        [string[]]$Hint = @(),
        [int]$Code = 1
    )
    Write-Err $Message
    if ($Hint.Count -gt 0) {
        Write-Host ""
        Write-Host "  Try:" -ForegroundColor Yellow
        foreach ($line in $Hint) { Write-Host "    $line" }
    }
    exit $Code
}

# --- Toolchain discovery -----------------------------------------------------

function Get-PythonPath {
    <#
      .SYNOPSIS
        Path to the interpreter to use, or $null when there is no virtualenv.
      .DESCRIPTION
        The repository's .venv is used exclusively. Falling back to a global
        interpreter would run the app against packages the user did not choose,
        which is exactly the kind of "works on my machine" drift the venv
        exists to prevent -- so a missing venv is reported, not worked around.
    #>
    if (Test-Path $script:PythonExe) {
        return $script:PythonExe
    }
    return $null
}

function Get-UvicornPath {
    if (Test-Path $script:UvicornExe) {
        return $script:UvicornExe
    }
    return $null
}

function Get-NpmPath {
    <#
      .SYNOPSIS
        Path to npm, or $null when Node.js is not installed.
      .DESCRIPTION
        npm.cmd is preferred over npm.ps1: on Windows `npm` is a shell script
        and `npm.ps1` is subject to execution-policy restrictions, so neither is
        reliably launchable from a non-interactive process.
    #>
    $command = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $onPath = Get-Command "npm" -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    return $null
}

function Test-BackendInstalled {
    <#
      .SYNOPSIS
        Whether the backend's dependencies are importable in the virtualenv.
      .DESCRIPTION
        Checks the real thing -- an import -- rather than the mere existence of
        the venv directory, because a venv can exist and still be empty.
    #>
    $python = Get-PythonPath
    if (-not $python) { return $false }

    $probe = "import fastapi, uvicorn, sqlalchemy, alembic, pydantic_settings, psycopg"
    & $python -c $probe 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Test-FrontendInstalled {
    <#
      .SYNOPSIS
        Whether frontend dependencies are installed.
      .DESCRIPTION
        The presence of the Vite package is the signal: package.json alone
        proves nothing, and `npm run dev` failing halfway through a start is a
        worse experience than being told up front.
    #>
    $vite = Join-Path $script:FrontendDir "node_modules\vite\package.json"
    return (Test-Path $vite)
}

# --- Networking --------------------------------------------------------------

function Get-PortOwner {
    <#
      .SYNOPSIS
        The pid listening on a TCP port, or $null if nothing is.
      .DESCRIPTION
        Used to tell "port already taken by my last run" (safe to stop) apart
        from "port taken by something else" (must not be touched).
    #>
    param(
        [Parameter(Mandatory)][int]$Port
    )
    try {
        $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($connection) { return $connection.OwningProcess }
    }
    catch {
        # Get-NetTCPConnection can fail without the NetTCPIP module or without
        # the rights to enumerate connections. Reporting "unknown" makes the
        # caller treat the port as busy, which is the safe direction.
        return $null
    }
    return $null
}

function Test-PortInUse {
    param([Parameter(Mandatory)][int]$Port)
    return ($null -ne (Get-PortOwner -Port $Port))
}

function Test-HttpOk {
    <#
      .SYNOPSIS
        Whether a URL returns a 2xx response.
      .DESCRIPTION
        A real HTTP request rather than a socket connect: the backend binds its
        port before the application finishes starting, and uvicorn's socket is
        open while the security check and database setup are still running.
        Only a successful /api/health response means the app can really serve.
    #>
    param(
        [Parameter(Mandatory)][string]$Url,
        [int]$TimeoutSeconds = 2
    )
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds -ErrorAction Stop
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300)
    }
    catch {
        return $false
    }
}

function Wait-ForHttp {
    <#
      .SYNOPSIS
        Poll a URL until it responds, or give up.
      .OUTPUTS
        $true if the URL became healthy, $false on timeout.
    #>
    param(
        [Parameter(Mandatory)][string]$Url,
        [int]$TimeoutSeconds = 60,
        [string]$What = "service"
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $attempt = 0
    while ((Get-Date) -lt $deadline) {
        $attempt++
        if (Test-HttpOk -Url $Url) {
            return $true
        }
        Start-Sleep -Milliseconds 400
    }
    Write-Warn "$What did not respond at $Url within ${TimeoutSeconds}s ($attempt attempts)."
    return $false
}

# --- Process management ------------------------------------------------------

function Initialize-StateDir {
    if (-not (Test-Path $script:StateDir)) {
        New-Item -ItemType Directory -Force -Path $script:StateDir | Out-Null
    }
}

function Save-Pid {
    <#
      .SYNOPSIS
        Record a pid so scripts\stop.ps1 can find the process later.
      .NOTES
        The parameter is named ProcessId, not Pid, on purpose: $PID is a
        read-only automatic variable in PowerShell, and a parameter called $Pid
        makes every call fail with "Cannot overwrite variable Pid because it is
        read-only or constant".
    #>
    param(
        [Parameter(Mandatory)][int]$ProcessId,
        [Parameter(Mandatory)][string]$Path
    )
    Initialize-StateDir
    Set-Content -Path $Path -Value $ProcessId -Encoding ascii
}

function Get-SavedPid {
    <#
      .SYNOPSIS
        Read a pid file, or $null when it is absent or corrupt.
      .DESCRIPTION
        A pid file can outlive the process it names (a crash, a reboot) and can
        be truncated by an interrupted write. Both cases mean "no usable pid",
        never a bogus one.
    #>
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path $Path)) { return $null }

    $raw = (Get-Content -Path $Path -Raw -ErrorAction SilentlyContinue)
    if (-not $raw) { return $null }

    $parsed = 0
    if (-not [int]::TryParse($raw.Trim(), [ref]$parsed)) { return $null }
    if ($parsed -le 0) { return $null }

    return $parsed
}

function Remove-PidFile {
    param([Parameter(Mandatory)][string]$Path)
    if (Test-Path $Path) {
        Remove-Item -Path $Path -Force -ErrorAction SilentlyContinue
    }
}

function Test-ProcessAlive {
    <#
      .SYNOPSIS
        Whether a pid still refers to a running process.
      .DESCRIPTION
        Get-Process throws for a pid it cannot see (another user's process, or
        one that exited between the check and the call), so failure is treated
        as "not alive" rather than propagated.
    #>
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    return ($null -ne $process)
}

function Stop-ProcessTree {
    <#
      .SYNOPSIS
        Stop a process and everything it spawned.
      .DESCRIPTION
        Both servers are launched through a wrapper (npm.cmd shells out to
        node, and the backend is started via `python -m uvicorn`), so the pid
        recorded is not necessarily the process holding the port. Stopping only
        the recorded pid can leave the real listener alive and the port still
        occupied, so taskkill /T takes down the whole tree. A process that has
        already exited is not an error.
    #>
    param(
        [Parameter(Mandatory)][int]$ProcessId,
        [string]$What = "process"
    )
    if (-not (Test-ProcessAlive -ProcessId $ProcessId)) {
        return $false
    }

    Write-Info "Stopping $What (pid $ProcessId)..."
    & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null
    return $true
}

function Stop-SavedServer {
    <#
      .SYNOPSIS
        Stop a server this repo started, by its recorded pid.
      .OUTPUTS
        $true if a running process was stopped, $false if there was nothing
        to stop.
    #>
    param(
        [Parameter(Mandatory)][string]$PidFile,
        [Parameter(Mandatory)][string]$What
    )
    $pidValue = Get-SavedPid -Path $PidFile
    if (-not $pidValue) {
        Remove-PidFile -Path $PidFile
        return $false
    }

    $stopped = Stop-ProcessTree -ProcessId $pidValue -What $What
    Remove-PidFile -Path $PidFile
    return $stopped
}

# --- Startup guards ----------------------------------------------------------

function Resolve-PortConflict {
    <#
      .SYNOPSIS
        Decide what to do about a port that is already in use.
      .DESCRIPTION
        There are two cases needing opposite handling. A pid we started means a
        previous run is still alive: stop it and carry on. Anything else --
        another application, or a process we cannot identify -- is left strictly
        alone, because killing an unrelated process to start a dev server is a
        genuinely destructive thing to do unasked.
      .OUTPUTS
        $true if the port is free, or was freed by stopping our own process.
    #>
    param(
        [Parameter(Mandatory)][int]$Port,
        [Parameter(Mandatory)][string]$PidFile,
        [Parameter(Mandatory)][string]$What
    )
    $owner = Get-PortOwner -Port $Port
    if ($null -eq $owner) { return $true }

    $saved = Get-SavedPid -Path $PidFile
    if ($saved -and $owner -eq $saved) {
        Write-Warn "Port $Port is held by the previous $What run (pid $owner). Stopping it."
        Stop-ProcessTree -ProcessId $owner -What $What
        Remove-PidFile -Path $PidFile
        Start-Sleep -Milliseconds 700
        if (-not (Test-PortInUse -Port $Port)) { return $true }
    }

    Stop-WithError -Message "Port $Port is already in use by another process (pid $owner), so the $What cannot start." -Hint @(
        "Stop that process yourself, or",
        "find it with:  netstat -ano | findstr :$Port      then   taskkill /PID <pid> /T /F",
        "or start on a different port (see the script parameters)."
    )
}

# --- Configuration -----------------------------------------------------------

function New-DevelopmentEnv {
    <#
      .SYNOPSIS
        Create backend/.env for local development, if it does not exist.
      .DESCRIPTION
        The backend defaults to APP_ENV=production, which correctly refuses to
        start without a session secret, credentials, workspace roots and CORS
        origins. That is right for a deployment and wrong for a first local
        run, so a development file is generated here -- and only when absent, so
        an existing hand-tuned .env is never overwritten.

        The generated file uses SQLite and no authentication, both of which are
        refused in production and exist purely as local conveniences. The file
        is gitignored, and both servers bind to loopback only.
      .OUTPUTS
        $true if a file was created, $false if one already existed.
    #>
    if (Test-Path $script:BackendEnvFile) {
        return $false
    }

    $content = @'
# =============================================================================
# Apollo -- LOCAL DEVELOPMENT configuration
#
# Generated by scripts/setup.ps1 on first run, because the application defaults
# to APP_ENV=production, and a production configuration without credentials, a
# session secret, workspace roots and CORS origins refuses to start on purpose.
#
# This file is for local development ONLY and is covered by .gitignore.
# Do not copy it to a server, and do not commit it.
#
#   APP_ENV=development   relaxes the production startup checks
#   AUTH_ENABLED=false    no login for local work (production refuses this)
#   SQLite database       a file, not a server (production requires PostgreSQL)
#   unrestricted roots    any directory may be registered as a workspace
#
# The LLM keys at the bottom are what the architecture chat needs. Without an
# API key the rest of the app still works; chat reports that no provider is
# configured.
# =============================================================================

APP_ENV=development

# --- Database ----------------------------------------------------------------
# SQLite keeps the local setup to "no database server". The schema is created
# from the models at startup, so there is no migration step to forget.
DATABASE_URL=sqlite:///./gaia_dev.db
DB_MIGRATE_ON_STARTUP=false
DB_REQUIRE_POSTGRES_IN_PRODUCTION=true

# --- Authentication ----------------------------------------------------------
# Disabled: production refuses to start with this off. The server binds to
# 127.0.0.1 only, so it is not reachable from the network. To require a login
# locally, see "Running the whole thing" in README.md.
AUTH_ENABLED=false
AUTH_USERNAME=admin
AUTH_PASSWORD_HASH=
AUTH_PASSWORD=
SESSION_SECRET=
AUTH_API_TOKEN=
SESSION_MAX_AGE_SECONDS=43200
AUTH_COOKIE_NAME=gaia_session

# --- CORS --------------------------------------------------------------------
# Empty falls back to the Vite dev server origins in development. The Vite proxy
# keeps the browser same-origin anyway, so this rarely needs changing.
CORS_ORIGINS=[]
CORS_ALLOW_CREDENTIALS=true

# --- Workspace roots ---------------------------------------------------------
# Unrestricted so any local directory can be registered as a repository while
# working. Set an explicit list (and leave the flag false) to exercise the
# path-sandbox behaviour that production enforces.
ALLOWED_WORKSPACE_ROOTS=[]
ALLOW_UNRESTRICTED_WORKSPACE_ROOTS=true

# --- LLM provider (OpenAI-compatible) ---------------------------------------
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=
LLM_MODEL=
LLM_CONTEXT_TOKENS=128000
LLM_MAX_OUTPUT_TOKENS=8192
LLM_TEMPERATURE=0.2
'@

    Set-Content -Path $script:BackendEnvFile -Value $content -Encoding utf8
    return $true
}

function Get-BackendEnvAppEnv {
    <#
      .SYNOPSIS
        The APP_ENV value in backend/.env, or "production" when unset.
      .DESCRIPTION
        Read textually rather than by importing the application settings,
        because importing them would fail on exactly the misconfiguration this
        check exists to report.
    #>
    if (-not (Test-Path $script:BackendEnvFile)) { return "production" }

    $match = Select-String -Path $script:BackendEnvFile -Pattern '^\s*APP_ENV\s*=\s*(\S+)' |
        Select-Object -First 1
    if (-not $match) { return "production" }

    return $match.Matches[0].Groups[1].Value
}

function Assert-BackendEnvSuitable {
    <#
      .SYNOPSIS
        Warn when backend/.env will not start, or is not in development mode.
      .DESCRIPTION
        A production .env left over from a deployment attempt is the most likely
        cause of a confusing first start, so it is called out by name, together
        with the error the server would otherwise raise on its own.
    #>
    $appEnv = Get-BackendEnvAppEnv
    if ($appEnv -eq "development") {
        Write-Ok "backend/.env is in development mode."
        return
    }

    if ($appEnv -eq "production") {
        Write-Warn "backend/.env has APP_ENV=production, so the backend will refuse to start without credentials."
        Write-Info "For local work set APP_ENV=development in backend/.env (see backend/.env.example)."
    }
    else {
        Write-Warn "backend/.env has an unrecognised APP_ENV='$appEnv'. The backend will refuse to start."
        Write-Info "Valid values are 'development' and 'production'."
    }
}

# --- Miscellaneous -----------------------------------------------------------

function Open-Browser {
    <#
      .SYNOPSIS
        Open a URL in the default browser, ignoring failure.
      .DESCRIPTION
        A convenience, never a requirement: a machine with no browser
        association (a container, a locked-down desktop) must still be able to
        start the app, so failure here is silent and the URL is printed anyway.
    #>
    param([Parameter(Mandatory)][string]$Url)
    try {
        Start-Process $Url -ErrorAction Stop | Out-Null
    }
    catch {
        Write-Info "Could not open a browser automatically. Open $Url yourself."
    }
}

function Show-LogTail {
    <#
      .SYNOPSIS
        Print the last lines of a log file, if it has any.
      .DESCRIPTION
        When a startup fails, the reason is in the log. Showing the tail turns
        "it did not start" into an actionable message without making the user
        go and find the file.
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [int]$Lines = 25,
        [string]$What = "log"
    )
    if (-not (Test-Path $Path)) { return }

    $content = Get-Content -Path $Path -Tail $Lines -ErrorAction SilentlyContinue
    if (-not $content) { return }

    Write-Host ""
    Write-Host "  --- last $Lines lines of ${What} ($Path) ---" -ForegroundColor DarkGray
    foreach ($line in $content) { Write-Host "  $line" -ForegroundColor DarkGray }
    Write-Host "  --- end of $What ---" -ForegroundColor DarkGray
    Write-Host ""
}

function Wait-ForKeyPress {
    <#
      .SYNOPSIS
        Block until a key is pressed, so a double-clicked window stays open.
      .DESCRIPTION
        Without this, a script that fails prints its error and the window
        vanishes before the message can be read. Only used by the .cmd entry
        points.
    #>
    Write-Host "  Press any key to close this window..." -ForegroundColor DarkGray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}


