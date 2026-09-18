@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS HUMAN-LIKE ENGINE - GUARDED CURRENT STUDENT TEST
echo ============================================================
echo.
echo What this test does:
echo   1. Scans the current lesson to discover which abilities ACTUALLY exist.
echo   2. Asks you for those scores in one line.
echo   3. Fills them using the workflow learned from your demonstration.
echo.
echo It does NOT click Submit or Post All.
echo ESC aborts the Python stage.
echo.
echo Open ONE student's In-Class Assessment page first.
echo.
pause

echo Updating the project first...
git pull --ff-only
if errorlevel 1 goto fail

set "PYEXE="
py -3 -c "import sys" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  python -c "import sys" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo Could not find Python 3.
  goto fail
)

if exist "repair_wkcommon.py" (
  %PYEXE% repair_wkcommon.py
  if errorlevel 1 goto fail
)

if not exist "libs\pywinauto" (
  echo Fetching required local libraries...
  %PYEXE% _fetch_libs.py
  if errorlevel 1 goto fail
)

%PYEXE% test_humanlike_current.py
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

:fail
echo.
echo Setup/update failed. Nothing in WOWKIDS was changed.
pause
exit /b 1
