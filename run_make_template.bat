@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found. Run setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" make_template.py %*
echo.
pause
