@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo WOWKIDS SAFE TEST - Making Skills accordion only
echo ============================================================
echo.
echo BEFORE CONTINUING:
echo   1. Open any student's rating form in WOWKIDS.
echo   2. Leave Making Skills COLLAPSED.
echo   3. Keep the WOWKIDS window visible.
echo.
echo This test is allowed to click ONLY the small Making Skills
echo accordion arrow. It will NOT select a rating and will NOT submit.
echo.
pause

call run_probe_first.bat
