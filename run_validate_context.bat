@echo off
setlocal enableextensions
cd /d "%~dp0"

rem Zero-click page classifier. Reads the window and classifies the page as
rem HOME / CLASS_ROSTER / RATING_FORM / STUDENT_PAGE / UNKNOWN. Never clicks.

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

echo Wowkids page context classifier - nothing will be clicked.
echo.
%PYEXE% validate_context.py %*
echo.
echo Report: reports\context_*.txt  (and .json, .png)
pause
exit /b 0

:fail
echo.
echo Library setup failed - see the messages above.
pause
exit /b 1