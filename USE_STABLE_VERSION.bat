@echo off
setlocal enableextensions
title WOWKIDS - USE STABLE VERSION

cd /d "%~dp0"

echo ============================================================
echo WOWKIDS - SWITCH TO KNOWN-GOOD STABLE VERSION
echo ============================================================
echo.
echo Stable snapshot: 2026-09-22
echo This keeps your pairing and preserves local edits in a git stash.
echo.

echo 1/6 Stopping only WOWKIDS background Python processes...
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$p=Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -and ($_.CommandLine -like '*wowkids_cloud_agent.py*' -or $_.CommandLine -like '*wowkids_agent_supervisor.py*') }; foreach($x in $p){ Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul

echo 2/6 Preserving any local development changes...
git stash push -u -m "Auto backup before switching to stable WOWKIDS version" >nul 2>nul

echo 3/6 Fetching the stable fallback branch...
git fetch origin stable-current
if errorlevel 1 (
  git -c http.sslBackend=openssl -c http.version=HTTP/1.1 fetch origin stable-current
)
if errorlevel 1 goto fail

echo 4/6 Switching this folder to stable-current...
git checkout -B local-stable origin/stable-current
if errorlevel 1 goto fail
git branch --set-upstream-to=origin/stable-current local-stable >nul 2>nul

echo 5/6 Checking the stable Python files...
set "PYEXE="
py -3 -c "import sys" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  python -c "import sys" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE goto fail

%PYEXE% -m py_compile wowkids_cloud_agent.py wowkids_agent_supervisor.py repair_windows_agent.py pair_windows_agent.py class_controller.py humanlike_engine.py home_navigator.py rating_engine.py
if errorlevel 1 goto fail

echo 6/6 Reusing the saved pairing and restarting the stable agent...
%PYEXE% repair_windows_agent.py
if errorlevel 1 goto fail

echo.
echo ============================================================
echo STABLE VERSION ACTIVE
echo ============================================================
echo.
echo You are now on the known-good WOWKIDS fallback.
echo Post All remains outside the automation.
echo.
pause
exit /b 0

:fail
echo.
echo Could not switch fully to the stable version.
echo Your saved pairing was not intentionally changed.
echo.
pause
exit /b 1
