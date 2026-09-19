@echo off
setlocal enableextensions
cd /d "%~dp0"

echo Updating WOWKIDS Rating Assistant...
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

if not exist "libs\pywinauto" (
  echo Fetching required local libraries...
  %PYEXE% _fetch_libs.py
  if errorlevel 1 goto fail
)

echo Checking the new cloud agent...
%PYEXE% -m py_compile wowkids_cloud_agent.py wowkids_agent_supervisor.py repair_windows_agent.py pair_windows_agent.py class_controller.py humanlike_engine.py rating_engine.py
if errorlevel 1 goto fail

%PYEXE% pair_windows_agent.py
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

:fail
echo.
echo Setup/update failed.
pause
exit /b 1
