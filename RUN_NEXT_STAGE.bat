@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS DEVELOPMENT TEST - current guarded stage
echo ============================================================
echo.
echo This launcher updates itself from GitHub before every run.
echo Current stage may change ratings on the student currently open.
echo It will NOT click Submit or Post All.
echo Press ESC at any time to abort the Python stage.
echo.
echo Keep WOWKIDS on the rating form and visible.
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
  echo Fetching required libraries...
  %PYEXE% _fetch_libs.py
  if errorlevel 1 goto fail
)

echo.
%PYEXE% select_making_score_test.py
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo Test finished successfully.
) else (
  echo Test stopped safely with code %RC%.
)
echo Reports are in "reports" and screenshots in "reports\shots".
pause
exit /b %RC%

:fail
echo.
echo Setup/update failed. Nothing else was run.
pause
exit /b 1
