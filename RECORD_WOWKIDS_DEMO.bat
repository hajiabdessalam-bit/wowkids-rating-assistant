@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS HUMAN DEMO RECORDER
echo ============================================================
echo.
echo This records YOUR clicks and scrolling while you rate normally.
echo It NEVER clicks or submits anything by itself.
echo Your screenshots stay local in the demos folder.
echo.
echo Stop recording at any time with F10.
echo.

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

%PYEXE% record_human_demo.py
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

:fail
echo.
echo Setup failed. Nothing in WOWKIDS was changed.
pause
exit /b 1
