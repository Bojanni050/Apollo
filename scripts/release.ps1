# =============================================================================
# Build a release of Apollo, optionally start it.
#
#   powershell -ExecutionPolicy Bypass -File scripts\release.ps1
#   apollo.cmd release           (build only)
#   apollo.cmd release-start     (build, then run the built exe)
#
# What a release build is here: the optimized desktop app, compiled by
# `tauri build` from src-tauri\. That step already builds the frontend bundle
# itself (beforeBuildCommand in tauri.conf.json), and it also produces the
# Windows installers (NSIS .exe and MSI) under src-tauri\target\release\.
#
# This script only arranges the prerequisites: a first-run pass (installs,
# frontend bundle) and a Rust toolchain check. Anything missing is installed
# or reported with a clear hint, never silently skipped.
#
# Safe to re-run: every install step skips what is already done, and the
# build simply produces a fresh binary.
# =============================================================================
[CmdletBinding()]
param(
    # Run the built exe at the end, instead of only reporting where it is.
    [switch]$Start
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host ""
Write-Host "Apollo -- release build" -ForegroundColor Cyan
Write-Host "Repository: $script:RepoRoot"
Write-Host ""

# --- 1. Prerequisites: the first-run pass (venv, packages, frontend bundle) --
# Delegated rather than duplicated: first-run.ps1 already skips anything that
# exists, so this only does what is missing on this machine. apollo.cmd has
# usually done this already before calling this script; the call is kept so
# the script also works when run directly.
if ($env:APOLLO_SKIP_FIRST_RUN -ne "1") {
    Write-Step "Preparing prerequisites (each step is skipped when already done)..."
    & (Join-Path $PSScriptRoot "first-run.ps1")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
else {
    Write-Info "Prerequisites already prepared by apollo.cmd; skipping first-run."
}
Write-Host ""

# --- 2. Rust toolchain --------------------------------------------------------
# `tauri build` compiles the Rust shell; without a working cargo it fails with a
# wall of linker errors or a plain "'cargo' is not recognized". Better to say what
# is missing and how to get it.
#
# The check has to PROVE cargo runs, not merely that the file exists. A machine
# can have a perfectly good Rust install that an application control policy (here
# Device Guard) refuses to execute. `Get-Command` resolves the path happily and
# reports success, so a presence-only check passes and the very next line dies
# with a raw Device Guard message that says nothing about what to do next.
#
# Two failures, two different remedies, so they are told apart:
#   * no cargo on PATH at all  -> install Rust;
#   * cargo present but blocked -> ask IT to allow it, installing more of the
#     same toolchain will not help.
$cargo = Get-Command "cargo" -ErrorAction SilentlyContinue
if (-not $cargo) {
    Stop-WithError "Rust is not installed, so the desktop app cannot be compiled." -Hint @(
        "Install it from https://rustup.rs/ (MSVC host toolchain),",
        "plus the 'Desktop development with C++' workload from Visual Studio",
        "Build Tools. Then re-open the terminal and run this again."
    )
}

# Run it and see what happens, and read the failure from cmd rather than from
# PowerShell. Two reasons:
#
#   * $ErrorActionPreference is 'Stop' here, so a blocked executable arrives as a
#     terminating error whose message is PowerShell's own wording -- which is
#     localised ("... geblokkeerd door een beleid voor toepassingsbeheer"). cmd
#     reports the same condition in the OS's own words, so the text can be
#     matched on substance instead of on one language.
#   * that error message also carries the source line and a stack trace, which
#     would end up quoted verbatim in the hint and bury the advice.
$blockedBy = $null
$cargoVersion = $null
try {
    $output = & cmd /c "`"$($cargo.Source)`" --version 2>&1"
    $cargoExit = $LASTEXITCODE
    $output = ($output | Out-String).Trim()
    # Success is a line like "cargo 1.82.0"; anything else is a failure to run.
    if ($cargoExit -eq 0 -and $output -match "cargo\s+\d") {
        $cargoVersion = $output
    }
    else {
        $blockedBy = if ($output) { $output } else { "cargo --version exited with code $cargoExit and printed nothing." }
    }
}
catch {
    $blockedBy = $_.Exception.Message
}
if (-not $cargoVersion) {
    # Matched on substance, not wording: the OS message is localised, so the
    # Dutch and English forms of the same condition are both listed.
    $isPolicy = ($blockedBy -match "Device Guard|blocked by your organization|AppLocker|Application Control|toepassingsbeheer|geblokkeerd")
    if ($isPolicy) {
        Stop-WithError "Rust is installed, but Windows is blocking this machine from running it." -Hint @(
            "cargo is present at $($cargo.Source) and on PATH, but",
            "application control refuses to execute it:",
            "  $blockedBy",
            "",
            "This is a policy on the machine, not a broken install, and it also",
            "blocks rustc -- so the desktop app cannot be compiled here as it is.",
            "Ask IT to allow the Rust toolchain (C:\Users\$env:USERNAME\.cargo\bin),",
            "or run the release build on a machine without that restriction.",
            "",
            "Everything that does not need Rust -- the backend, the frontend",
            "bundle, the dev server -- keeps working."
        )
    }
    Stop-WithError "Rust is installed but cargo could not be run." -Hint @(
        "cargo is present at $($cargo.Source) but failed:",
        "  $blockedBy",
        "",
        "Check that the file is not blocked (Properties -> Unblock) and that it",
        "still runs: cargo --version"
    )
}
Write-Ok "Rust $cargoVersion found."

# --- 3. Build ------------------------------------------------------------------
$npm = Get-NpmPath
if (-not $npm) {
    Stop-WithError "npm is not on PATH, so the release build cannot run."
}

Write-Step "Building the release (tauri build; this also builds the frontend bundle)..."
$releaseExe = Join-Path $script:RepoRoot "src-tauri\target\release\apollo.exe"
$installDir = Join-Path $script:RepoRoot "src-tauri\target\release\bundle"

Push-Location $script:RepoRoot
try {
    & $npm run build
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "The release build failed." -Hint @(
            "The output above names the failing crate or file.",
            "`cargo build` in src-tauri\ gives a fuller error."
        )
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path $releaseExe)) {
    Stop-WithError "The build reported success but $releaseExe is missing."
}
Write-Ok "Release exe: $releaseExe"

# --- 4. Report / start -----------------------------------------------------------
Write-Host ""
Write-Host "Release build complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Executable:   $releaseExe"
if (Test-Path $installDir) {
    Write-Host "  Installers:   $installDir"
}
Write-Host ""

if ($Start) {
    Write-Step "Starting the release exe (backend errors appear below; close the app window to return)..."
    # Foreground on purpose: the desktop shell inherits the backend's stderr
    # (Stdio::inherit in src-tauri\src\lib.rs), so a backend that fails to
    # start prints its reason HERE. A detached start would hide exactly the
    # message that explains a failed startup. The app opens its own window;
    # closing it ends the process and returns control to this console.
    Push-Location (Split-Path $releaseExe -Parent)
    try {
        & $releaseExe
    }
    finally {
        Pop-Location
    }
    Write-Ok "The release exe exited."
}
else {
    Write-Host "Run it directly, or use:  apollo.cmd release-start"
    Write-Host ""
}
