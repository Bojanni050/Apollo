@echo off
REM =============================================================================
REM Open Apollo in a desktop window (double-click this file).
REM
REM Delegates to scripts\desktop.ps1: the Tauri 2 app in src-tauri\ starts the
REM Python API as a child process, waits for it to answer, and opens a native
REM window on it. The API is stopped when the window closes.
REM
REM Expects first-run to have been done (apollo.cmd desktop does that
REM automatically, including on a fresh machine).
REM
REM The one entry point for everything is apollo.cmd: menu, start, desktop,
REM release, release-start, first-run, stop.
REM =============================================================================
setlocal
set "SCRIPT_DIR=%~dp0scripts"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\desktop.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo The desktop app exited with code %EXITCODE%.
    echo Use apollo.cmd for the menu, or apollo.cmd start for the browser version.
)
echo.
pause
endlocal
