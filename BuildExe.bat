@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
title Build Windows Hotspot Butler
setlocal

where python >nul 2>nul
if errorlevel 1 goto :nopy

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  python -m pip install pyinstaller || goto :failed
)

python -c "import webview" >nul 2>nul
if errorlevel 1 (
  python -m pip install pywebview || goto :failed
)

python -c "import pystray, PIL" >nul 2>nul
if errorlevel 1 (
  python -m pip install pystray Pillow || goto :failed
)

rem Desktop UI ships from source public\ (--add-data). Web preview dist\
rem is optional: drop stale dist\ so it never ends up inside the exe.
if exist dist rmdir /s /q dist

echo Building frontend ...
set "WHM_OPTIONAL_BUILD=1"
where node >nul 2>nul
if errorlevel 1 (
  echo frontend skipped; node not found, desktop UI from public\
) else (
  node rosezm-cli.mjs build .
  if exist dist\index.html (echo frontend OK) else (echo frontend skipped; desktop UI from public\)
)
set "WHM_OPTIONAL_BUILD="

python python-app\build_exe.py %*
if errorlevel 1 goto :failed

python python-app\smoke_exe.py
if errorlevel 1 (echo smoke FAILED) else (echo smoke OK)

echo python-app\dist\WiFiHotspotManager\WiFiHotspotManager.exe
pause
goto :end

:nopy
echo Python 3.10+ not found. Install from https://www.python.org/
pause
goto :end

:failed
pause
goto :end

:end
endlocal
