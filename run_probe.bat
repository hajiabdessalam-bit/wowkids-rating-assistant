@echo off
setlocal enableextensions
cd /d "%~dp0"

rem Usage:
rem   run_probe.bat             -> probe all five categories (expands them, selects nothing)
rem   run_probe.bat first       -> probe only Making Skills
rem   run_probe.bat 1,2         -> probe categories 1 and 2

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

set "ARG=all"
if not "%~1"=="" set "ARG=%~1"

echo Section expansion probe - expands headers only, never selects a rating.
echo.
%PYEXE% probe_sections.py --sections %ARG%
echo.
echo Report in "reports", screenshots in "reports\shots".
pause
exit /b 0

:fail
echo.
echo Library setup failed - see the messages above.
pause
exit /b 1