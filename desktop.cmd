@echo off
REM =============================================================================
REM Open Apollo in a desktop window (double-click this file).
REM
REM Runs scripts\desktop.py, which starts the API in a background thread, builds
REM the frontend if needed, and opens a native window. No browser, no second
REM terminal.
REM
REM First run only: if pywebview is not installed, run
REM   .venv\Scripts\python -m pip install -e .\backend[desktop]
REM The window is closed by closing it; the server stops with it.
REM =============================================================================
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo.
    echo   No virtualenv found at %PYTHON%
    echo   Run setup first:  scripts\setup.ps1
    echo.
    pause
    endlocal
    exit /b 1
)

REM --browser opens in your normal browser instead, which needs no GUI extras.
"%PYTHON%" "%ROOT%scripts\desktop.py" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo The desktop app exited with code %EXITCODE%.
    echo The window is closed by closing it. If no window appeared, try:
    echo   %PYTHON% scripts\desktop.py --browser
)

echo.
pause
endlocal
