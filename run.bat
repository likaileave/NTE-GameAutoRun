@echo off
cd /d "%~dp0"

fltmc >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    if errorlevel 1 (
        echo.
        echo Failed to elevate. Right-click this file and choose "Run as administrator".
        pause
    )
    exit /b
)

if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] .venv\Scripts\pythonw.exe not found. Run setup.bat first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" gui.py
exit /b
