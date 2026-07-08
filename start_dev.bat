@echo off
rem COMSOL AI App DEV launcher (ASCII only) - auto-reload on code change
rem Use for development sessions with Claude. For normal use run start.bat
rem IMPORTANT: run only ONE instance of this script at a time.
cd /d "%~dp0"

set PYCMD=
py -3.12 -c "pass" >nul 2>nul
if not errorlevel 1 set PYCMD=py -3.12
if defined PYCMD goto found
py -3.13 -c "pass" >nul 2>nul
if not errorlevel 1 set PYCMD=py -3.13
if defined PYCMD goto found
py -3.11 -c "pass" >nul 2>nul
if not errorlevel 1 set PYCMD=py -3.11
if defined PYCMD goto found
py -3.10 -c "pass" >nul 2>nul
if not errorlevel 1 set PYCMD=py -3.10
if defined PYCMD goto found
echo.
echo [ERROR] Python 3.10 - 3.13 not found on this PC.
echo Install Python 3.12 from https://www.python.org/downloads/ then run again.
echo.
pause
exit /b 1

:found
echo Using Python: %PYCMD%

if not exist .venv\Scripts\python.exe goto mkvenv
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info < (3,14) else 1)" >nul 2>nul
if errorlevel 1 goto rmvenv
goto deps

:rmvenv
echo Removing old virtual environment (wrong Python version)...
rmdir /s /q .venv

:mkvenv
echo Creating virtual environment...
%PYCMD% -m venv .venv
if errorlevel 1 goto fail

:deps
echo Installing packages (first run may take several minutes)...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto fail

rem Kill ALL previous servers of this project (by path), then any port owner
echo Stopping any previous server of this project...
powershell -NoProfile -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*comsol_ai_app*' } | ForEach-Object { Write-Host ('  stopping project python PID ' + $_.Id); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }"
powershell -NoProfile -Command "for($i=0;$i -lt 10;$i++){ $c=Get-NetTCPConnection -LocalPort 8712 -State Listen -ErrorAction SilentlyContinue; if(-not $c){ Write-Host '  port 8712 is free'; exit 0 }; foreach($x in $c){ Write-Host ('  stopping port owner PID tree ' + $x.OwningProcess); taskkill /PID $x.OwningProcess /T /F | Out-Null }; Start-Sleep -Milliseconds 700 }; Write-Host '  WARNING: port still busy - run stop.bat, close ALL black windows, then start.bat once'"

start "" http://127.0.0.1:8712
echo.
echo DEV server running at http://127.0.0.1:8712  (auto-reload ON; closing this window stops the app)
echo.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8712 --reload
pause
exit /b 0

:fail
echo.
echo [ERROR] Setup failed - copy ALL text in this window and paste it to Claude.
echo.
pause
exit /b 1
