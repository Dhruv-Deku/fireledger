@echo off
rem Downloads this year's hotspots from NASA FIRMS and swaps them into the API.
rem Needs a free FIRMS MAP_KEY (https://firms.modaps.eosdis.nasa.gov/api/map_key/), kept in .env.
cd /d "%~dp0"
title FireLedger update
if exist .env goto havekey
set /p KEY=Paste your FIRMS MAP_KEY and press Enter:
>.env echo FIRMS_MAP_KEY=%KEY%
:havekey

echo [1/5] Downloading from NASA FIRMS (the API keeps running meanwhile)...
python pipeline\fetch_recent.py --download-only || goto fail

echo [2/5] Stopping the API and converting the download...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
python pipeline\fetch_recent.py --convert-only || goto fail

echo [3/5] Which satellites observed each day...
python pipeline\calibrate.py --avail-only || goto fail

echo [4/5] Grid for the new days...
python pipeline\build_grid.py --recent || goto fail

echo [5/5] This month vs the same days of past years...
python pipeline\build_grid.py --mtd || goto fail

echo.
echo Done. Starting the API again.
start "" "%~dp0run_api.bat"
pause
exit /b 0

:fail
echo.
echo A step failed (message above). Starting the API with the data it already has.
start "" "%~dp0run_api.bat"
pause
exit /b 1
