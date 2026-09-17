@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS NEXT GUARDED TEST - resume after Problem Solving
echo ============================================================
echo.
echo Making Skills and Problem Solving are already done.
echo This stage will continue with:
echo   Theory ^& Application = 4
echo   Creative Thinking = 3
echo   Interpersonal Skills = 5
echo.
echo It can scroll the rating form automatically when a category is too low.
echo It will NOT click Submit or Post All.
echo If a category succeeds and a later one fails, progress is saved so the
 echo next run resumes instead of repeating completed categories.
echo.
echo Keep WOWKIDS on the SAME student's rating form and visible.
echo Press ESC at any time to abort the Python stage.
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
%PYEXE% test_after_problem.py
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
