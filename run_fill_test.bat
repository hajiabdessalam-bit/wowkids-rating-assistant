@echo off
setlocal enableextensions
cd /d "%~dp0"

rem Usage:
rem   run_fill_test.bat        -> real fill of the test student (never submits)
rem   run_fill_test.bat dry    -> build and print the plan, click nothing

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

set "MODE="
if /I "%~1"=="dry" set "MODE=--dry-run"

echo Stage 3 test fill - student DemoStudent, scores 4,4,4,3,5
echo ESC aborts at any time. Submit is never pressed.
echo.
%PYEXE% fill_one_student.py --student DemoStudent --scores 4,4,4,3,5 %MODE%
echo.
echo Report written to the "reports" folder, log to the "logs" folder.
pause
exit /b 0

:fail
echo.
echo Library setup failed - see the messages above.
pause
exit /b 1