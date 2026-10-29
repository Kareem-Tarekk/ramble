@echo off
rem Starts Ramble. The first run sets up its Python environment, which takes a few minutes.
rem The environment lives in a short path because Windows can't load libraries from very long ones.
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.ramble\venv"
if not exist "%VENV%\Scripts\pythonw.exe" (
    echo Setting up Ramble - first run only, this takes a few minutes...
    py -3.13 -m venv "%VENV%" 2>nul || py -3 -m venv "%VENV%"
    "%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Setup failed. See the messages above.
        pause
        exit /b 1
    )
)
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0ramble.py"
