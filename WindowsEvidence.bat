@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
title Windows Hotspot Butler - Evidence Collector
setlocal

echo ==================================================================
echo  Windows Hotspot Butler - evidence collector
echo ==================================================================
net session >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Not Administrator. Right-click -^> Run as administrator.
  goto :end
)

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3.10+ not found.
  goto :end
)

if not exist evidence mkdir evidence

echo [1/6] Read-only self-test ...
python python-app\run.py --selftest --port 8787 > evidence\selftest.txt 2>&1
type evidence\selftest.txt
copy /Y python-app\windows_selftest_report.json evidence\selftest_report.json >nul

echo [2/6] Real hotspot toggle (idempotent restore) ...
python python-app\run.py --selftest --apply --port 8787 > evidence\selftest_apply.txt 2>&1
type evidence\selftest_apply.txt
copy /Y python-app\windows_selftest_report.json evidence\selftest_apply_report.json >nul

echo [3/6] UI parity ...
python python-app\test_original_ui.py > evidence\original_ui.txt 2>&1
type evidence\original_ui.txt

echo [4/6] desktop UI probe ...
python python-app\run.py --probe-ui > evidence\desktop_probe.txt 2>&1
type evidence\desktop_probe.txt
if exist python-app\desktop_probe_report.json (
  copy /Y python-app\desktop_probe_report.json evidence\desktop_probe_report.json >nul
)

set "HAVE_EXE="
if exist python-app\dist\WiFiHotspotManager\WiFiHotspotManager.exe set "HAVE_EXE=1"
if exist python-app\dist\WiFiHotspotManager.exe set "HAVE_EXE=1"
echo [5/6] exe smoke ...
if defined HAVE_EXE (
  python python-app\smoke_exe.py > evidence\exe_smoke.txt 2>&1
  type evidence\exe_smoke.txt
) else (
  echo       no built exe found
)

echo [6/6] Starting the app ...
call Run.bat --port 8788 > evidence\run_app.txt 2>&1

echo ==================================================================
echo  Verdict:
python python-app\verify_evidence.py evidence
echo  DONE.
python python-app\pack_evidence.py
echo ==================================================================
:end
pause
endlocal
