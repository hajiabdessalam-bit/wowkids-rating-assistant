@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS RATING ASSISTANT - HUMAN DEMO PHASE
echo ============================================================
echo.
echo We changed strategy: YOU demonstrate the task, the recorder observes.
echo The recorder itself never clicks, scrolls, rates, Submit, or Post All.
echo.
echo Updating the project first...
git pull --ff-only
if errorlevel 1 goto fail

echo.
call RECORD_WOWKIDS_DEMO.bat
exit /b %ERRORLEVEL%

:fail
echo.
echo Update failed. Nothing in WOWKIDS was changed.
pause
exit /b 1
