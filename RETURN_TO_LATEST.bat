@echo off
setlocal enableextensions
title WOWKIDS - RETURN TO LATEST
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS - RETURN TO LATEST MAIN VERSION
echo ============================================================
echo.
echo GitHub is NOT required on this PC.
echo.

set "PYEXE="
py -3 -c "import sys" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE python -c "import sys" >nul 2>nul && set "PYEXE=python"
if not defined PYEXE goto fail

echo 1/3 Stopping WOWKIDS background processes...
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$p=Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -and ($_.CommandLine -like '*wowkids_cloud_agent.py*' -or $_.CommandLine -like '*wowkids_agent_supervisor.py*') }; foreach($x in $p){ Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul

echo 2/3 Downloading latest runtime through Feedback Assistant...
%PYEXE% vercel_update.py --ref main
if errorlevel 1 goto fail

echo 3/3 Restarting latest agent with saved pairing...
%PYEXE% repair_windows_agent.py
if errorlevel 1 goto fail

echo.
echo LATEST VERSION ACTIVE
pause
exit /b 0

:fail
echo.
echo Could not return to latest.
echo Your saved pairing was not changed.
pause
exit /b 1
