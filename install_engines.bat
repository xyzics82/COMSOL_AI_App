@echo off
rem Optional engine dependencies (Solcore, ASE for QE CIF, scipy/h5py for .mat)
rem Run AFTER the app has been started once (so .venv exists).
cd /d "%~dp0"
if not exist .venv\Scripts\pip.exe (
  echo [ERROR] .venv not found - run start.bat once first.
  pause
  exit /b 1
)
echo Installing optional engine packages into .venv ...
.venv\Scripts\pip install solcore ase scipy h5py
echo.
echo Done. Restart the app (start.bat) and check the engine tabs.
pause
