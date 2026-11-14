@echo off
rem Starts the FireLedger API on port 8000. Keep this window open while the app is in use.
cd /d "%~dp0"
title FireLedger API
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=1" %%b in ("%%a") do echo Partner URL: http://%%b:8000
echo Local URL:   http://localhost:8000/api/health
echo.
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
echo.
echo The API stopped. Press any key to close this window.
pause >nul
