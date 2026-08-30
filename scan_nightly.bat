@echo off
REM Nightly unattended price scan.
REM
REM Walks the catalog in priority order - the portfolio first, then the
REM wishlist, then the figures inside owned sets, then the rest of the themes
REM already collected, then everything else. Never-scanned items come before
REM merely stale ones, so an interrupted night still made progress where it
REM counted.
REM
REM Install (one time, from an ordinary Command Prompt in this folder):
REM
REM   schtasks /create /tn "Brickonomy nightly scan" /tr "\"%CD%\scan_nightly.bat\"" /sc daily /st 02:00
REM
REM Check on it:      schtasks /query /tn "Brickonomy nightly scan"
REM Run it now:       schtasks /run   /tn "Brickonomy nightly scan"
REM Remove it:        schtasks /delete /tn "Brickonomy nightly scan" /f
REM
REM At ~150 items and roughly a minute each, a night's run takes about 2.5
REM hours. Raise SCAN_LIMIT once you have seen a few clean nights.

setlocal
cd /d "%~dp0"

set SCAN_LIMIT=150
set SCAN_SCOPE=priority

if not exist "logs" mkdir "logs"
for /f "tokens=2 delims==" %%d in ('wmic os get localdatetime /value') do set LDT=%%d
set STAMP=%LDT:~0,4%-%LDT:~4,2%-%LDT:~6,2%
set LOGFILE=logs\scan-%STAMP%.log

REM Emoji in the scan log would die on this machine's default codepage.
set PYTHONIOENCODING=utf-8

echo ============================================================ >> "%LOGFILE%"
echo Scan started %DATE% %TIME%  (scope=%SCAN_SCOPE% limit=%SCAN_LIMIT%) >> "%LOGFILE%"

".venv\Scripts\python.exe" -m brickonomy.refresh --scope %SCAN_SCOPE% --limit %SCAN_LIMIT% >> "%LOGFILE%" 2>&1
set RC=%ERRORLEVEL%

if %RC%==3 (
  echo Skipped: another scan already holds the lock. >> "%LOGFILE%"
) else (
  echo Finished %DATE% %TIME% with exit code %RC% >> "%LOGFILE%"
)

REM Keep a fortnight of logs, drop the rest.
forfiles /p logs /m scan-*.log /d -14 /c "cmd /c del @path" 2>nul

endlocal
exit /b %RC%
