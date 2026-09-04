@echo off
cd /d "%~dp0"
title WiFi Hotspot Manager

set "PY=%~dp0tools\python\python.exe"
if exist "%PY%" goto :run

where python >nul 2>nul
if errorlevel 1 goto :nopy
python -c "import tkinter" >nul 2>nul
if errorlevel 1 goto :notk
python "%~dp0main.py" %*
if errorlevel 1 pause
goto :end

:run
"%PY%" "%~dp0main.py" %*
if errorlevel 1 pause
goto :end

:nopy
echo Python not found. Please install Python 3.10+ (check "Add to PATH" and "tcl/tk").
pause
goto :end

:notk
echo tkinter is missing. Reinstall official Python and check "tcl/tk and IDLE".
pause
goto :end

:end
