@echo off
REM =============================================================================
REM Start Apollo (double-click this file).
REM
REM Runs scripts\dev.ps1, which starts the backend, waits for it to answer
REM /api/health, starts the Vite dev server, and opens the browser.
REM
REM The window stays open afterwards so the servers keep running and any
REM message can be read. Use scripts\stop.ps1 (or stop.cmd) to shut down.
REM =============================================================================
setlocal

set "SCRIPT_DIR=%~dp0scripts"

REM Run the PowerShell script in the same window so its output is visible.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\dev.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo Setup or startup failed with exit code %EXITCODE%.
)

echo.
pause
endlocal
