@echo off
cd /d "%~dp0"
title WiFi Hotspot Manager

set "PY=%~dp0tools\python\python.exe"
if exist "%PY%" goto :run

where python >nul 2>nul
if errorlevel 1 goto :nopy
python -c "import webview" >nul 2>nul
if errorlevel 1 goto :noweb
python "%~dp0main.py" %*
if errorlevel 1 pause
goto :end

:run
"%PY%" "%~dp0main.py" %*
if errorlevel 1 pause
goto :end

:nopy
echo Python not found. Please install Python 3.10+ (check "Add to PATH").
pause
goto :end

:noweb
echo pywebview is missing. Run: pip install pywebview
echo Or use the legacy Tkinter UI: python main.py --tk
pause
goto :end

:end
