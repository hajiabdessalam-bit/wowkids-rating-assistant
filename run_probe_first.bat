@echo off
setlocal enableextensions
cd /d "%~dp0"

rem Always probes ONLY Making Skills. No arguments, no way to widen it here.

set "PYEXE="
py -3 -c "import sys" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  python -c "import sys" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo Could not find Python 3. Install it from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if exist "repair_wkcommon.py" (
  %PYEXE% repair_wkcommon.py
  if errorlevel 1 goto fail
)

if not exist "libs\pywinauto" (
  echo First run - fetching the required libraries...
  %PYEXE% _fetch_libs.py
  if errorlevel 1 goto fail
)

echo Making Skills VISUAL probe - expands at most one header, selects nothing.
echo It ignores stale UIA roster/calendar rows and verifies the real 1..5 stars from pixels.
echo.
%PYEXE% probe_visual_first.py
echo.
echo Report in "reports", screenshots in "reports\shots".
pause
exit /b 0

:fail
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
