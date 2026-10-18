@echo off
cd /d "%~dp0"

echo Checking Python...
set "PY_CMD="
where py >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD (
    where python >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo.
    echo [ERROR] Python not found.
    echo Install Python 3.10+ and check "Add python.exe to PATH".
    echo https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

echo Using: %PY_CMD%
if exist ".venv" (
    echo Recreating .venv ...
    rmdir /s /q ".venv"
)
%PY_CMD% -m venv .venv
if errorlevel 1 (
    echo [ERROR] Failed to create .venv
    pause
    exit /b 1
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies. Check network.
    pause
    exit /b 1
)

echo.
echo Done. Run run.bat to start.
pause
