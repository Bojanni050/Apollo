@echo off
REM =============================================================================
REM Apollo -- one script for everything.
REM
REM Double-click for a menu, or run with an argument:
REM
REM   apollo.cmd                  menu
REM   apollo.cmd start            start the browser version (backend + Vite + browser)
REM   apollo.cmd desktop          start the desktop app (installs what is missing first)
REM   apollo.cmd release          build a release: the exe and the installers
REM   apollo.cmd release-start    build the release, then start the built exe
REM   apollo.cmd first-run        install everything + build the frontend bundle
REM   apollo.cmd stop             stop everything this repo started
REM
REM "desktop" folds first-run.cmd and desktop.cmd into one: it installs what
REM is missing (venv, packages, Tauri CLI, frontend bundle) and then starts
REM the app, so on a fresh machine it is genuinely one command.
REM =============================================================================

setlocal
set "ROOT=%~dp0"
set "SCRIPTS=%ROOT%scripts"

REM --- the first argument is the action; everything after it is passed to
REM --- the PowerShell script unchanged.
set "ACTION=%~1"
shift
set "PSARGS="
:collect
if "%~1"=="" goto :collected
set "PSARGS=%PSARGS% %~1"
shift
goto :collect
:collected

if "%ACTION%"=="" goto :menu
if /i "%ACTION%"=="start" goto :start
if /i "%ACTION%"=="dev" goto :start
if /i "%ACTION%"=="desktop" goto :desktop
if /i "%ACTION%"=="release" goto :release
if /i "%ACTION%"=="build" goto :release
if /i "%ACTION%"=="release-start" goto :release_start
if /i "%ACTION%"=="first-run" goto :first_run
if /i "%ACTION%"=="firstrun" goto :first_run
if /i "%ACTION%"=="setup" goto :first_run
if /i "%ACTION%"=="stop" goto :stop
REM Numeric aliases, so "apollo.cmd 4" works like choosing 4 in the menu.
if "%ACTION%"=="1" goto :start
if "%ACTION%"=="2" goto :desktop
if "%ACTION%"=="3" goto :release
if "%ACTION%"=="4" goto :release_start
if "%ACTION%"=="5" goto :first_run
if "%ACTION%"=="6" goto :stop
echo.
echo   Unknown action: %ACTION%
echo.
goto :menu

REM --- actions ------------------------------------------------------------------

:start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS%\dev.ps1"%PSARGS%
goto :end

:desktop
call :maybe_first_run
if not "%ERRORLEVEL%"=="0" goto :failed
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS%\desktop.ps1"%PSARGS%
goto :end

:release
call :maybe_first_run
if not "%ERRORLEVEL%"=="0" goto :failed
set "APOLLO_SKIP_FIRST_RUN=1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS%\release.ps1"%PSARGS%
goto :end

:release_start
call :maybe_first_run
if not "%ERRORLEVEL%"=="0" goto :failed
set "APOLLO_SKIP_FIRST_RUN=1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS%\release.ps1" -Start%PSARGS%
goto :end

:first_run
call :run_first_run
if not "%ERRORLEVEL%"=="0" goto :failed
echo.
echo   Start the browser version:  apollo.cmd start
echo   Start the desktop app:       apollo.cmd desktop
echo   Build a release:             apollo.cmd release
echo   Stop everything:            apollo.cmd stop
goto :end

:stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS%\stop.ps1"%PSARGS%
goto :end

REM --- the menu (no argument, or double-click) ----------------------------------

:menu
echo.
echo   ================= Apollo =================
echo.
echo   1  start            browser version (backend + frontend + browser)
echo   2  desktop           desktop app (installs what is missing first)
echo   3  release           build a release (exe + installers)
echo   4  release-start     build the release, then start it
echo   5  first-run         (re)install dependencies and rebuild the bundle
echo   6  stop              stop everything Apollo started
echo.
set /p CHOICE="  Choose 1-6: "
if "%CHOICE%"=="1" goto :start
if "%CHOICE%"=="2" goto :desktop
if "%CHOICE%"=="3" goto :release
if "%CHOICE%"=="4" goto :release_start
if "%CHOICE%"=="5" goto :first_run
if "%CHOICE%"=="6" goto :stop
goto :menu

REM --- helpers ------------------------------------------------------------------

:maybe_first_run
REM Only prepare when something is actually missing; otherwise a desktop start
REM would rebuild the frontend bundle every single time. first-run.ps1 is
REM idempotent, so running it when in doubt is always safe.
if not exist "%ROOT%.venv\Scripts\python.exe" goto :run_first_run
if not exist "%ROOT%node_modules\.bin\tauri.cmd" goto :run_first_run
if not exist "%ROOT%frontend\node_modules\vite\package.json" goto :run_first_run
echo.
echo   [ok] Everything is installed; skipping first-run.
goto :eof

:run_first_run
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS%\first-run.ps1"
goto :eof

:failed
echo.
echo   Preparing Apollo failed. Fix the message above and run this again.
echo.
pause
endlocal
exit /b 1

:end
set "EXITCODE=%ERRORLEVEL%"

REM A successful launch leaves nothing to say and nothing to wait for: the app
REM has its own window, and anything this console was going to show afterwards
REM is either in that window or in a log. So it closes itself instead of waiting
REM for a keypress that has no purpose.
REM
REM A failure is the exception. A message the reader never gets to read is worse
REM than a window they have to close, so the window stays and says why.
if not "%EXITCODE%"=="0" (
    echo.
    echo   Apollo exited with code %EXITCODE%.
    echo.
    pause
)

REM endlocal comes last, and the code is read out first: endlocal discards
REM everything set inside the block, so testing EXITCODE after it would always
REM find it empty and report success.
endlocal & exit /b %EXITCODE%
