@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
title Windows Hotspot Butler
setlocal

where python >nul 2>nul
if errorlevel 1 goto :nopy
python -V >nul 2>nul
if errorlevel 1 goto :nopy

python python-app\run.py %*
if errorlevel 1 goto :failed
goto :end

:nopy
echo Python 3.10+ not found. Install from https://www.python.org/
pause
goto :end

:failed
echo Startup failed. See output above.
pause
goto :end

:end
endlocal
