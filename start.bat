@echo off
REM =============================================================================
REM Start Gaia Docs Architect -- backend AND frontend (double-click this file).
REM
REM This is a thin alias for start.cmd, which does the real work by calling
REM scripts\dev.ps1. That script installs anything missing, starts the backend,
REM waits for /api/health to answer, starts the Vite dev server, and opens
REM http://127.0.0.1:5173.
REM
REM Use stop.bat (or stop.cmd) to shut both down.
REM =============================================================================
call "%~dp0start.cmd" %*
