@echo off
rem Starts Ramble. The first run (and any run after the libraries change) installs them, about a minute or two.
rem The environment lives in a short path because Windows can't load libraries from very long ones.
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.ramble\venv"
set "DONE=%VENV%\requirements.installed.txt"

rem Already set up with these exact libraries?
fc /b requirements.txt "%DONE%" >nul 2>&1
if not errorlevel 1 goto run

echo Setting up Ramble. This is a one-time download of about 30 MB.
if not exist "%VENV%\Scripts\python.exe" (
    echo.
    echo [1/2] Creating Ramble's Python environment...
    py -3.13 -m venv "%VENV%" 2>nul || py -3 -m venv "%VENV%" 2>nul
)
if not exist "%VENV%\Scripts\python.exe" (
    echo.
    echo Python was not found. Install Python 3.13 from python.org, tick "Add python.exe to PATH" and "py launcher", then run this again.
    pause
    exit /b 1
)
echo.
echo [2/2] Downloading libraries...
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo Setup failed. See the messages above, then run this again.
    pause
    exit /b 1
)
copy /y requirements.txt "%DONE%" >nul
echo.
echo Setup done. Starting Ramble...

:run
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0ramble.py"
