@echo off
chcp 65001 >nul
title TenderPilot
cd /d "%~dp0"

echo ==========================================================
echo    TenderPilot - запуск приложения
echo ==========================================================
echo.

REM ---- 1. Ищем Python ----
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [ОШИБКА] Python не найден на этом компьютере.
    echo.
    echo   1. Скачайте Python: https://www.python.org/downloads/
    echo   2. Запустите установщик и ОБЯЗАТЕЛЬНО поставьте галочку
    echo      "Add python.exe to PATH" в самом низу первого экрана.
    echo   3. Перезапустите этот файл.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PY% --version 2^>^&1') do echo Python: %%v

REM ---- 2. Проверяем и ставим зависимости ----
%PY% -c "import fastapi, uvicorn, httpx, multipart, pypdf" >nul 2>&1
if errorlevel 1 (
    echo.
    echo Устанавливаю необходимые библиотеки, это займёт 1-2 минуты...
    echo.
    %PY% -m pip install --upgrade pip >nul 2>&1
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ОШИБКА] Не удалось установить библиотеки.
        echo Проверьте интернет и попробуйте запустить файл ещё раз.
        pause
        exit /b 1
    )
)
echo Библиотеки в порядке.
echo.

REM ---- 3. Проверяем демо-базу ----
if not exist "data\tenderpilot.db" (
    echo Создаю демо-базу...
    %PY% -c "from app import db; db.init_db()" >nul 2>&1
)

REM ---- 4. Запуск ----
echo ==========================================================
echo    Сервер запускается:  http://localhost:8000
echo    Вход:  owner@zavod.ru   пароль:  demo1234
echo.
echo    Браузер откроется автоматически.
echo    Чтобы остановить - закройте это окно или нажмите Ctrl+C
echo ==========================================================
echo.

start "" http://localhost:8000
%PY% -m uvicorn app.main:app --host 0.0.0.0 --port 8000

echo.
echo Сервер остановлен.
pause
