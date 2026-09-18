@echo off
setlocal
cd /d "%~dp0"

if not exist "ratings.csv" (
  copy /Y "ratings.example.csv" "ratings.csv" >nul
)

echo Opening your PRIVATE local ratings.csv...
echo.
echo Replace the demo rows with real students.
echo Scores are 1-5.
echo A score may be left blank only when that ability is NOT used in the lesson.
echo.
start "" "ratings.csv"
exit /b 0
