@echo off
setlocal
title TenderPilot
cd /d "%~dp0"
cls
echo ============================================================
echo    TenderPilot - tender monitoring CRM
echo ============================================================
echo.

REM ---- 1. Find Python. Prefer 3.11-3.13: every library has prebuilt
REM         wheels for them, so nothing needs compiling.
set "PY="
for %%V in (3.12 3.13 3.11) do (
    if not defined PY (
        py -%%V --version >nul 2>&1
        if not errorlevel 1 set "PY=py -%%V"
    )
)
if not defined PY (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY goto nopython

echo Python version:
%PY% --version
echo.

REM ---- 2. Dependencies: binary wheels only, no compilation ----
%PY% -c "import fastapi, uvicorn, httpx, multipart, pypdf" >nul 2>&1
if not errorlevel 1 goto depsok

echo Installing libraries (no compilation, 1-3 minutes)...
echo.
%PY% -m pip install --upgrade pip >nul 2>&1
%PY% -m pip install --only-binary=:all: -r requirements.txt
if not errorlevel 1 goto depsok

echo.
echo First attempt failed. Trying a normal install...
%PY% -m pip install -r requirements.txt
if not errorlevel 1 goto depsok

goto piperror

:depsok
echo.
echo Libraries OK.
echo.

REM ---- 3. Database ----
if not exist "data\tenderpilot.db" (
    echo Creating database...
    %PY% -c "from app import db; db.init_db()"
)

REM ---- 4. Run ----
echo ============================================================
echo    Server: http://localhost:8000
echo    Login:  owner@zavod.ru
echo    Pass:   Tender2026
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
echo  1. Download Python 3.12: https://www.python.org/downloads/
echo  2. Run the installer.
echo  3. IMPORTANT: tick "Add python.exe to PATH" at the bottom
echo     of the first installer screen.
echo  4. Run start.bat again.
echo.
pause
exit /b 1

:piperror
echo.
echo ============================================================
echo  ERROR: libraries could not be installed.
echo ============================================================
echo.
echo Most likely your Python is newer than the libraries support
echo (for example Python 3.14), so pip tries to compile them.
echo Compiling needs Visual C++ Build Tools - not what we want.
echo.
echo Solution - install Python 3.12, it has ready-made packages:
echo   1. Go to https://www.python.org/downloads/release/python-3129/
echo   2. At the bottom of the page download
echo      "Windows installer (64-bit)"
echo   3. Run it, tick "Add python.exe to PATH", click Install
echo   4. Run start.bat again - it picks 3.12 automatically
echo.
pause
exit /b 1
