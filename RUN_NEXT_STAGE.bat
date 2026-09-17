@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS NEXT SAFE TEST - select Making Skills score 4 only
echo ============================================================
echo.
echo This test WILL select score 4 in Making Skills on the CURRENT student.
echo It will NOT submit and will NOT touch the other four categories.
echo.
echo Keep WOWKIDS on the rating form and visible.
echo Making Skills may be collapsed or already expanded.
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
