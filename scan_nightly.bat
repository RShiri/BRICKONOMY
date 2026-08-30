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

REM Date for the log filename. Not via wmic: it was removed in Windows 11
REM 24H2/26200, and its absence left the name containing a colon, which is
REM illegal in a filename, so the redirect failed and nothing was ever logged.
REM Python is already required for the scan itself, so it does the formatting.
set STAMP=
for /f "usebackq delims=" %%d in (`.venv\Scripts\python.exe -c "import datetime;print(datetime.date.today().isoformat())"`) do set STAMP=%%d
if "%STAMP%"=="" set STAMP=undated
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
".venv\Scripts\python.exe" -c "import pathlib,time; [p.unlink() for p in pathlib.Path('logs').glob('scan-*.log') if time.time()-p.stat().st_mtime > 14*86400]" 2>nul

endlocal
exit /b %RC%
