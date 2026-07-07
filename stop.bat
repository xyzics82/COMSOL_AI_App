@echo off
rem Emergency stop: kill ALL servers of this project (ASCII only)
echo Stopping all COMSOL AI app servers...
powershell -NoProfile -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*comsol_ai_app*' } | ForEach-Object { Write-Host ('  stopping project python PID ' + $_.Id); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }"
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8712 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ('  stopping port owner PID ' + $_.OwningProcess); Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8712 -State Listen -ErrorAction SilentlyContinue) { Write-Host 'RESULT: port 8712 STILL BUSY' } else { Write-Host 'RESULT: all clear - now run start.bat ONCE' }"
pause
