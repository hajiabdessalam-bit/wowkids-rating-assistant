@echo off
setlocal enableextensions
cd /d "%~dp0"

set "PYEXE="
py -3 -c "import sys" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  python -c "import sys" >nul 2>nul && set "PYEXE=python"
)

if not defined PYEXE (
  echo Python 3 was not found.
  pause
  exit /b 1
)

%PYEXE% restore_wowkids_window.py
echo.
echo WOWKIDS window recovery finished.
pause
