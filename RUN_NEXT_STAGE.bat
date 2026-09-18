@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS HUMAN-LIKE ENGINE - CURRENT STUDENT TEST
echo ============================================================
echo.
echo This version was rebuilt from your recorded human demonstration.
echo It discovers the abilities that ACTUALLY exist on the lesson instead
echo of assuming every class has all five abilities.
echo.
echo It will fill the current student but WILL NOT click Submit or Post All.
echo ESC aborts the Python stage.
echo.
echo Open ONE student's In-Class Assessment page first.
echo.
pause

echo Updating the project first...
git pull --ff-only
if errorlevel 1 goto fail

call RUN_HUMANLIKE_TEST.bat
exit /b %ERRORLEVEL%

:fail
echo.
echo Update failed. Nothing in WOWKIDS was changed.
pause
exit /b 1
