@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS CLASS RATING ASSISTANT
echo ============================================================
echo.
echo Updating the project...
git pull --ff-only
if errorlevel 1 goto fail

call RUN_CLASS.bat
exit /b %ERRORLEVEL%

:fail
echo.
echo Update failed. Nothing in WOWKIDS was changed.
pause
exit /b 1
