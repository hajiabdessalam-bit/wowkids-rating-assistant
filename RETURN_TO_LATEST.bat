@echo off
setlocal enableextensions
title WOWKIDS - RETURN TO LATEST

cd /d "%~dp0"

echo ============================================================
echo WOWKIDS - RETURN TO LATEST MAIN VERSION
echo ============================================================
echo.

echo 1/5 Stopping only WOWKIDS background Python processes...
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$p=Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -and ($_.CommandLine -like '*wowkids_cloud_agent.py*' -or $_.CommandLine -like '*wowkids_agent_supervisor.py*') }; foreach($x in $p){ Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul

echo 2/5 Preserving any local changes...
git stash push -u -m "Auto backup before returning WOWKIDS to main" >nul 2>nul

echo 3/5 Switching to main...
git checkout main
if errorlevel 1 goto fail

echo 4/5 Updating main...
git pull --ff-only
if errorlevel 1 (
  git -c http.sslBackend=openssl -c http.version=HTTP/1.1 pull --ff-only
)
if errorlevel 1 goto fail

echo 5/5 Restarting the latest agent with the saved pairing...
call REPAIR_WINDOWS_AGENT.bat
exit /b %ERRORLEVEL%

:fail
echo.
echo Could not return to latest main automatically.
echo Your saved pairing was not intentionally changed.
echo.
pause
exit /b 1
