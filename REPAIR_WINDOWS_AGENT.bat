@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS WINDOWS AGENT - NO-KEY REPAIR
echo ============================================================
echo.
echo This keeps your existing pairing. You will NOT paste a new key.
echo.

echo Updating the local assistant...
git pull --ff-only
if errorlevel 1 (
  echo.
  echo Normal Git TLS failed. Retrying with Git's OpenSSL backend...
  git -c http.sslBackend=openssl -c http.version=HTTP/1.1 pull --ff-only
)
if errorlevel 1 (
  echo.
  echo GitHub update is temporarily unavailable.
  echo Continuing with the local installed files instead of failing the repair.
  echo.
)

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

echo Checking the agent files...
%PYEXE% -m py_compile wowkids_cloud_agent.py wowkids_agent_supervisor.py repair_windows_agent.py pair_windows_agent.py class_controller.py humanlike_engine.py home_navigator.py performance_log.py rating_engine.py background_workspace.py
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
