@echo off
setlocal
title TenderPilot
cd /d "%~dp0"
cls
echo ============================================================
echo    TenderPilot - tender monitoring CRM
echo ============================================================
echo.

set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY goto nopython

echo Python:
%PY% --version
echo.

%PY% -c "import fastapi, uvicorn, httpx, multipart, pypdf" >nul 2>&1
if not errorlevel 1 goto depsok

echo Installing libraries. Please wait 1-3 minutes...
echo.
%PY% -m pip install --upgrade pip >nul 2>&1
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto piperror

:depsok
echo Libraries OK.
echo.

if not exist "data\tenderpilot.db" (
    echo Creating demo database...
    %PY% -c "from app import db; db.init_db()"
)

echo ============================================================
echo    Server: http://localhost:8000
echo    Login:  owner@zavod.ru
echo    Pass:   demo1234
echo.
echo    Browser opens automatically.
echo    Keep this window open. Close it to stop the server.
echo ============================================================
echo.

start "" http://localhost:8000
%PY% -m uvicorn app.main:app --host 0.0.0.0 --port 8000

echo.
echo Server stopped.
pause
exit /b 0

:nopython
echo.
echo ERROR: Python not found on this computer.
echo.
echo  1. Download Python: https://www.python.org/downloads/
echo  2. Run the installer.
echo  3. IMPORTANT: tick "Add python.exe to PATH" at the bottom
echo     of the first installer screen.
echo  4. Run start.bat again.
echo.
pause
exit /b 1

:piperror
echo.
echo ERROR: could not install libraries.
echo Check your internet connection and run start.bat again.
echo.
pause
exit /b 1
