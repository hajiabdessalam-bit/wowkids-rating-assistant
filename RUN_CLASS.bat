@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS CLASS RATING ASSISTANT
echo ============================================================
echo.
echo Open the correct class roster in WOWKIDS before continuing.
echo You will choose ALL, FIRST N, or specific students for this run.
echo.
echo Submit is automatic after each student's ratings are verified.
echo POST ALL IS NEVER CLICKED.
echo ESC or F10 stops the run.
echo.
pause

set "PYEXE="
py -3 -c "import sys" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  python -c "import sys" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo Could not find Python 3.
  pause
  exit /b 1
)

if exist "repair_wkcommon.py" (
  %PYEXE% repair_wkcommon.py
  if errorlevel 1 goto fail
)

if not exist "libs\pywinauto" (
  echo Fetching required local libraries...
  %PYEXE% _fetch_libs.py
  if errorlevel 1 goto fail
)

%PYEXE% class_controller.py
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

:fail
echo.
echo Setup failed. Nothing in WOWKIDS was changed.
pause
exit /b 1
