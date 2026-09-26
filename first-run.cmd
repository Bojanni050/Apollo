@echo off
REM =============================================================================
REM First run of Apollo (double-click this file).
REM
REM Runs scripts\first-run.ps1: it installs anything missing (Python virtualenv,
REM backend packages, frontend packages, backend\.env) and then builds the
REM frontend bundle at frontend\dist, which the desktop app serves and cannot
REM start without.
REM
REM Afterwards use start.cmd (browser version) or desktop.cmd (desktop app).
REM Safe to re-run.
REM =============================================================================
setlocal
set "SCRIPT_DIR=%~dp0scripts"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\first-run.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo First run failed with exit code %EXITCODE%.
)
echo.
pause
endlocal
