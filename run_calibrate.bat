@echo off
setlocal enableextensions
cd /d "%~dp0"

rem Zero-click calibration: measures the window and draws every candidate
rem rectangle interpretation onto a screenshot for visual confirmation.

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

rem Usage:
rem   run_calibrate.bat                 measure and draw overlays, click nothing
rem   run_calibrate.bat confirm H1      activate H1/H2/H3 after checking the PNG

echo Coordinate calibration - nothing will be clicked.
echo.
set "ARGS="
if /I "%~1"=="confirm" set "ARGS=--confirm %~2"
%PYEXE% calibrate_coordinates.py %ARGS%
echo.
echo Look at reports\coordinate_debug_*.png to confirm the boxes line up.
pause
exit /b 0

:fail
echo.
echo Library setup failed - see the messages above.
pause
exit /b 1