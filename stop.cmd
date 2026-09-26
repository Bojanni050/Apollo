@echo off
REM =============================================================================
REM Stop Gaia Docs Architect (double-click this file).
REM
REM Stops only the processes that scripts\dev.ps1 started, using the pid files
REM in .dev\. Other programs listening on ports 8000 or 5173 are reported and
REM left running.
REM =============================================================================
setlocal

set "SCRIPT_DIR=%~dp0scripts"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\stop.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

echo.
pause
endlocal
