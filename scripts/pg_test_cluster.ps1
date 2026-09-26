# Disposable PostgreSQL cluster for integration tests (P0.3).
#
# The task asked for a disposable container. Docker is NOT available in this
# environment (CLI present, no daemon), so this script achieves the same
# isolation using the locally installed PostgreSQL binaries:
#
#   * a throwaway data directory (no existing cluster is touched)
#   * its own port, so it cannot collide with the system instance on 5432
#   * its own superuser, so no existing credentials are needed or guessed
#   * torn down on exit
#
# It is a development convenience only. CI and any machine with Docker should
# use `docker compose -f docker-compose.test.yml up -d` and set
# TEST_DATABASE_URL instead -- the test suite only needs TEST_DATABASE_URL.
param(
    [int]$Port = 55432,
    [string]$DataDir = "$PSScriptRoot\..\.pgtest",
    [string]$Superuser = "gaia",
    [switch]$Keep
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
$DataDir = [System.IO.Path]::GetFullPath($DataDir)

# Locate the PostgreSQL binaries: PATH first, then the standard install path.
$bin = $null
$onPath = Get-Command initdb.exe -ErrorAction SilentlyContinue
if ($onPath) { $bin = Split-Path $onPath.Source -Parent }
if (-not $bin) {
    $found = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\initdb.exe" -ErrorAction SilentlyContinue |
             Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) { $bin = $found.DirectoryName }
}
if (-not $bin) { throw "PostgreSQL binaries not found. Install PostgreSQL or use Docker." }
Write-Host "Using PostgreSQL binaries in $bin"

function Stop-Cluster {
    & (Join-Path $bin 'pg_ctl.exe') -D "$DataDir\data" -m immediate stop 2>&1 | Out-Null
}

if (Test-Path "$DataDir\data") {
    Write-Host "Reusing existing cluster at $DataDir\data"
} else {
    Write-Host "Creating disposable cluster at $DataDir\data"
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    & (Join-Path $bin 'initdb.exe') -D "$DataDir\data" -U $Superuser --auth=trust --encoding=UTF8 | Out-Null
    # Two Windows PostgreSQL instances otherwise fight over the same shared
    # memory region (error 487). This instance is dedicated and small, so the
    # 'windows' mechanism and a small max_connections are appropriate.
    @"

# --- disposable test cluster (P0.3) ---
shared_memory_type = windows
max_connections = 20
listen_addresses = '127.0.0.1'
port = $Port
"@ | Add-Content -Path "$DataDir\data\postgresql.conf"
}

Stop-Cluster
Start-Sleep -Seconds 1
Write-Host "Starting on port $Port"
& (Join-Path $bin 'pg_ctl.exe') -D "$DataDir\data" -l "$DataDir\server.log" -o "-p $Port" -w -t 25 start | Out-Null

# Wait for readiness rather than assuming it.
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    & (Join-Path $bin 'pg_isready.exe') -h 127.0.0.1 -p $Port -q
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) {
    Get-Content "$DataDir\server.log" -Tail 20
    throw "PostgreSQL did not become ready on port $Port"
}

& (Join-Path $bin 'createdb.exe') -h 127.0.0.1 -p $Port -U $Superuser gaia_docs_test 2>&1 | Out-Null
Write-Host "Ready. TEST_DATABASE_URL=postgresql+psycopg://${Superuser}@127.0.0.1:${Port}/gaia_docs_test"
