@echo off
REM =============================================================================
REM Open Gaia Docs Architect in a desktop window (double-click this file).
REM
REM Runs the Tauri 2 app in src-tauri\, which starts the Python API as a child
REM process, waits for it to answer, and opens a native window on it. The API is
REM stopped when the window closes.
REM
REM Requires Rust (https://rustup.rs/), the MSVC build tools and the WebView2
REM runtime. A release installer is produced with:  npm run build
REM
REM Without Rust, use start.cmd for the browser version.
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

where cargo >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Rust is not installed, so the desktop app cannot be built.
    echo   Install it from https://rustup.rs/ then re-run this file.
    echo.
    echo   The app still runs without it:  start.cmd
    echo.
    pause
    endlocal
    exit /b 1
)

cd /d "%ROOT%"
call npm run dev
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo The desktop app exited with code %EXITCODE%.
    echo `npm run build` gives a fuller error, or use start.cmd for the browser
    echo version.
)

echo.
pause
endlocal
