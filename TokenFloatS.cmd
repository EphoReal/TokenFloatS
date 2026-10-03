@echo off
rem TokenFloatS - start the tray panel without a console window.
rem Prefer a local venv; fall back to whatever python is on PATH.
setlocal
cd /d "%~dp0"

if exist "%~dp0venv\Scripts\pythonw.exe" (
    start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0tokenfloats.py"
) else (
    where pythonw >nul 2>&1
    if errorlevel 1 (
        echo Python was not found. Create a venv first:
        echo     python -m venv venv
        echo     venv\Scripts\pip install -r requirements.txt
        pause
    ) else (
        start "" pythonw "%~dp0tokenfloats.py"
    )
)
endlocal
