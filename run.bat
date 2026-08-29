@echo off
REM ===================================================================
REM  Brickonomy - start the website on this PC.
REM  Just double-click this file. Safe to run again any time.
REM ===================================================================
setlocal
cd /d "%~dp0"
title Brickonomy

if "%PORT%"=="" set PORT=8000

echo.
echo  ==========================================
echo    Brickonomy - LEGO price tracker
echo  ==========================================
echo.

REM --- 1. Find Python -------------------------------------------------
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY (
    python --version >nul 2>&1 && set PY=python
)
if not defined PY (
    echo  [X] Python is not installed.
    echo.
    echo      1. Download it from  https://www.python.org/downloads/
    echo      2. IMPORTANT: tick "Add python.exe to PATH" in the installer
    echo      3. Run this file again
    echo.
    pause
    exit /b 1
)

REM --- 2. Check the version is new enough -----------------------------
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 (
    echo  [X] Your Python is too old. Version 3.9 or newer is needed.
    %PY% --version
    echo      Get a current version from https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM --- 3. Private environment for this app -----------------------------
if not exist ".venv\Scripts\python.exe" (
    echo  [1/4] Creating a private Python environment ^(one time^)...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo  [X] Could not create the environment. See the message above.
        pause
        exit /b 1
    )
) else (
    echo  [1/4] Environment ready.
)
set VPY=.venv\Scripts\python.exe
if not exist "%VPY%" (
    echo  [X] The environment looks broken. Delete the .venv folder and run this again.
    pause
    exit /b 1
)

REM --- 4. Dependencies -------------------------------------------------
echo  [2/4] Checking dependencies ^(quiet, may take a minute the first time^)...
"%VPY%" -m pip install --quiet --upgrade pip
"%VPY%" -m pip install --quiet -r brickonomy\requirements.txt
if errorlevel 1 (
    echo  [X] Could not install the dependencies ^(no internet?^).
    pause
    exit /b 1
)

REM --- 5. First run: build the database --------------------------------
REM  Written with labels rather than an if^(...^) block on purpose: %VAR%
REM  inside a parenthesised block is expanded when the block is parsed, so
REM  the answer to the prompt below would always read back as empty.
if exist "brickonomy.db" goto :have_db

echo  [3/4] First run - preparing your data...
"%VPY%" -m brickonomy.importer
echo.
echo      The full LEGO catalog is every set ever released ^(~34,000 items^),
echo      so search works like BrickLink. It needs internet and ~1 minute.
set /p GETCAT=      Download it now? [Y/n]:
if /i "%GETCAT%"=="n" goto :skip_catalog
"%VPY%" -m brickonomy.rebrickable --minifigs
goto :have_db

:skip_catalog
echo      Skipped. You can add it later: run scan.bat and pick option 5.
goto :have_db

:have_db
if exist "brickonomy.db" echo  [3/4] Database ready.

REM --- 6. Serve --------------------------------------------------------
echo  [4/4] Starting the website...
echo.
echo      Open:  http://127.0.0.1:%PORT%
echo      Stop:  press Ctrl+C, or just close this window
echo.

REM Open the browser a few seconds later, once uvicorn is actually listening.
start "" /b "%VPY%" -c "import time,webbrowser; time.sleep(4); webbrowser.open('http://127.0.0.1:%PORT%')"

"%VPY%" -m uvicorn brickonomy.web.app:app --host 127.0.0.1 --port %PORT%

echo.
echo  The website stopped.
echo  If it stopped immediately, port %PORT% may already be in use - run:
echo      set PORT=8001  ^&^&  run.bat
pause
