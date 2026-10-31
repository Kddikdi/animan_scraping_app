@echo off
rem Start the app so that a smartphone on the same Wi-Fi can open it
cd /d "%~dp0"
set APP_HOST=0.0.0.0
set APP_PORT=5000
call ..\myenv\Scripts\activate.bat
python run.py
pause
