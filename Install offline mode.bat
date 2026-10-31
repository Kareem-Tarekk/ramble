@echo off
rem Optional: lets Ramble turn speech into text on this PC when Groq can't be reached.
rem Downloads about 45 MB of libraries plus the Whisper "small" speech model (about 460 MB). Needs internet, once.
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.ramble\venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo Run "Start Ramble.bat" first to set Ramble up, then run this again.
    pause
    exit /b 1
)
echo Installing offline speech libraries...
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-offline.txt
if errorlevel 1 goto failed
echo.
echo Downloading the Whisper speech model...
"%VENV%\Scripts\python.exe" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
if errorlevel 1 goto failed
echo.
echo Done. Ramble now works offline. Restart it if it is running (tray icon > Quit, then Start Ramble.bat).
pause
exit /b 0
:failed
echo.
echo Setup failed. See the messages above.
pause
exit /b 1
