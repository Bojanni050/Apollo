@echo off
REM =============================================================================
REM First run of Apollo -- install and build (double-click this file).
REM
REM This is a thin alias for first-run.cmd, which does the real work by
REM calling scripts\first-run.ps1. That script installs anything missing
REM (Python virtualenv, backend packages, frontend packages, backend\.env)
REM and builds the frontend bundle at frontend\dist that the desktop app
REM serves.
REM
REM Afterwards use start.cmd / stop.cmd for the browser version.
REM =============================================================================
call "%~dp0first-run.cmd" %*
