@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS WINDOWS AGENT - NO-KEY REPAIR
echo ============================================================
echo.
echo No GitHub download is required.
echo Updates arrive automatically through the normal Feedback Assistant connection.
echo Your saved pairing will NOT be changed.
echo.

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

echo Checking the installed agent files...
%PYEXE% -m py_compile wowkids_cloud_agent.py wowkids_agent_supervisor.py repair_windows_agent.py pair_windows_agent.py class_controller.py humanlike_engine.py home_navigator.py performance_log.py rating_engine.py
if errorlevel 1 goto fail

%PYEXE% repair_windows_agent.py
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

:fail
echo.
echo Repair could not finish. Your existing pairing was not changed.
pause
exit /b 1
