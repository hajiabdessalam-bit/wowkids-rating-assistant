@echo off
setlocal enableextensions
cd /d "%~dp0"

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

if not exist "libs\pywinauto" (
  echo First run - fetching the required libraries...
  %PYEXE% _fetch_libs.py
  if errorlevel 1 goto fail
)

%PYEXE% inspect_uia.py %*
echo.
echo Report written to the "reports" folder next to this script.
pause
exit /b 0

:fail
echo.
echo Library setup failed - see the messages above.
pause
exit /b 1