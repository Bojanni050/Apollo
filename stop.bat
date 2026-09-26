@echo off
REM =============================================================================
REM Stop Gaia Docs Architect -- backend AND frontend (double-click this file).
REM
REM A thin alias for stop.cmd. Stops only the processes these scripts started,
REM using the pid files in .dev\, and leaves anything else on ports 8000/5173
REM alone.
REM =============================================================================
call "%~dp0stop.cmd" %*
