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
REM That registers the task "Interactive only": it runs inside the logged-on
REM desktop session and shares its console, so a Ctrl+C anywhere in that
REM session kills the scan mid-run. To make it independent of the session,
REM re-create it to run whether or not you are logged on -- schtasks will
REM prompt for your password, which it stores in the Credential Manager:
REM
REM   schtasks /delete /tn "Brickonomy nightly scan" /f
REM   schtasks /create /tn "Brickonomy nightly scan" /tr "\"%CD%\scan_nightly.bat\"" /sc daily /st 02:00 /ru %USERNAME% /rp *
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

REM -u matters more than it looks. Python buffers stdout when it is redirected
REM to a file, so a run that dies mid-scan takes its last few KB of output --
REM including whatever explains the death -- to the grave. The night of
REM 2026-08-31 died two items in and the log held nothing but the start line.
".venv\Scripts\python.exe" -u -m brickonomy.refresh --scope %SCAN_SCOPE% --limit %SCAN_LIMIT% >> "%LOGFILE%" 2>&1
set RC=%ERRORLEVEL%

if %RC%==3 (
  echo Skipped: another scan already holds the lock. >> "%LOGFILE%"
) else (
  echo Finished %DATE% %TIME% with exit code %RC% >> "%LOGFILE%"
)
REM No "Finished" line at all means this script was killed rather than that
REM the scan failed. Exit code -1073741510 (0xC000013A) on the task is
REM STATUS_CONTROL_C_EXIT: a Ctrl+C reached the console this task shares.
REM A task registered "Interactive only" runs inside the logged-on session
REM and dies with it; see the install note above for the fix.

REM Keep a fortnight of logs, drop the rest.
".venv\Scripts\python.exe" -c "import pathlib,time; [p.unlink() for p in pathlib.Path('logs').glob('scan-*.log') if time.time()-p.stat().st_mtime > 14*86400]" 2>nul

endlocal
exit /b %RC%
