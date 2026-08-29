@echo off
REM ===================================================================
REM  Brickonomy - collect fresh prices from BrickLink / eBay / BrickOwl.
REM  Double-click, pick an option, leave it running.
REM ===================================================================
setlocal
cd /d "%~dp0"
title Brickonomy - price scan

set VPY=.venv\Scripts\python.exe
if not exist "%VPY%" (
    echo  [X] Brickonomy is not set up yet on this PC.
    echo      Run  run.bat  once first, then come back here.
    echo.
    pause
    exit /b 1
)

echo.
echo  ==========================================
echo    Brickonomy - price scan
echo  ==========================================
echo.
echo   Scanning opens a hidden Chrome window per source and waits a few
echo   seconds between requests on purpose, so the shops do not block us.
echo   Expect roughly half a minute per set. You can leave it running.
echo.
echo   1  My portfolio      - every set you own or want
echo   2  Stale only        - just the sets whose prices are past the TTL
echo   3  One set           - type a set or minifig number
echo   4  Everything        - the whole catalog (very slow, hours)
echo   5  Full LEGO catalog - re-download set/minifig names from Rebrickable
echo   6  Rebuild the published site (docs\)
echo   7  Sign in to eBay  - unlocks SOLD prices (one time, opens a browser)
echo   0  Quit
echo.

set CHOICE=
set /p CHOICE=  Choose [1]:
if not defined CHOICE set CHOICE=1

if "%CHOICE%"=="0" exit /b 0
if "%CHOICE%"=="1" goto :portfolio
if "%CHOICE%"=="2" goto :stale
if "%CHOICE%"=="3" goto :one
if "%CHOICE%"=="4" goto :all
if "%CHOICE%"=="5" goto :catalog
if "%CHOICE%"=="6" goto :export
if "%CHOICE%"=="7" goto :ebaylogin
echo  Not a valid choice.
pause
exit /b 1

:portfolio
echo.
echo  Scanning your portfolio...
"%VPY%" -m brickonomy.refresh --scope portfolio
goto :done

:stale
echo.
echo  Scanning stale sets...
"%VPY%" -m brickonomy.refresh --scope stale
goto :done

:one
echo.
set ITEM=
set /p ITEM=  Set or minifig number (e.g. 75192 or sw0879):
if not defined ITEM (
    echo  Nothing entered.
    goto :done
)
"%VPY%" -m brickonomy.refresh --item %ITEM% --force
goto :done

:all
echo.
echo  This scans EVERY item in the database and can run for hours.
set SURE=
set /p SURE=  Continue? [y/N]:
if /i not "%SURE%"=="y" goto :done
"%VPY%" -m brickonomy.refresh --scope all
goto :done

:catalog
echo.
echo  Downloading the full LEGO catalog from Rebrickable...
"%VPY%" -m brickonomy.rebrickable --minifigs
goto :done

:export
echo.
echo  Rebuilding the static site into docs\ ...
"%VPY%" -m brickonomy.export
echo.
echo  Done. To publish it:  git add docs ^&^& git commit -m "Update site" ^&^& git push
goto :done

:ebaylogin
echo.
echo  eBay only shows SOLD prices to a signed-in browser.
echo  A Chrome window will open - sign in there, then come back here.
echo  Your password goes to eBay's own page; only the browser session is kept.
"%VPY%" -m brickonomy.ebay_login
goto :done

:done
echo.
echo  Finished. Start the website with  run.bat  to see the new numbers.
pause
