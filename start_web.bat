@echo off
setlocal
cd /d "%~dp0"

set "PY_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PY_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PY_EXE if exist "D:\ASMR\openlrc\.venv\Scripts\python.exe" set "PY_EXE=D:\ASMR\openlrc\.venv\Scripts\python.exe"
if not defined PY_EXE set "PY_EXE=python"

start "audio2sub server" /min "%PY_EXE%" -m web.server
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8710/api/health -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>nul
  if not errorlevel 1 goto ready
  timeout /t 1 /nobreak >nul
)
:ready
start "" http://127.0.0.1:8710/
endlocal
