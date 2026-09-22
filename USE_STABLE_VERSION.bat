@echo off
setlocal enableextensions
title WOWKIDS - USE STABLE VERSION
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS - SWITCH TO KNOWN-GOOD STABLE VERSION
echo ============================================================
echo.
echo Stable snapshot: stable-working-2026-09-22
echo GitHub is NOT required on this PC.
echo Your saved pairing will NOT be changed.
echo.

set "PYEXE="
py -3 -c "import sys" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE python -c "import sys" >nul 2>nul && set "PYEXE=python"
if not defined PYEXE goto fail

echo 1/4 Stopping WOWKIDS background processes...
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$p=Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -and ($_.CommandLine -like '*wowkids_cloud_agent.py*' -or $_.CommandLine -like '*wowkids_agent_supervisor.py*') }; foreach($x in $p){ Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul

echo 2/4 Downloading the protected stable runtime through Feedback Assistant...
%PYEXE% vercel_update.py --ref stable-working-2026-09-22
if errorlevel 1 goto fail

echo 3/4 Checking stable Python files...
%PYEXE% -m py_compile wowkids_cloud_agent.py class_controller.py humanlike_engine.py home_navigator.py rating_engine.py
if errorlevel 1 goto fail

echo 4/4 Restarting with saved pairing...
%PYEXE% repair_windows_agent.py
if errorlevel 1 goto fail

echo.
echo ============================================================
echo STABLE VERSION ACTIVE
echo ============================================================
echo.
echo Post All remains outside the automation.
pause
exit /b 0

:fail
echo.
echo Could not activate the stable runtime.
echo Your saved pairing was not changed.
pause
exit /b 1
