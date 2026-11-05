@echo off
chcp 65001 >nul
REM Restart backend: stop the old app.main process on port 8080, then start it again.
REM Usage: double-click this file, or run .\restart.bat in a terminal.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue; foreach ($x in $c) { $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $x.OwningProcess); if ($p.CommandLine -match 'app\.main') { Stop-Process -Id $x.OwningProcess -Force } }; Start-Sleep -Milliseconds 800; Set-Location backend; if (Test-Path 'venv\Scripts\python.exe') { & 'venv\Scripts\python.exe' -m app.main } else { python -m app.main }"
if errorlevel 1 pause
