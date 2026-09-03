@echo off
setlocal
cd /d "%~dp0"
title audio2sub (Tkinter GUI)
echo Starting audio2sub ...
echo.

set "PY_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PY_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PY_EXE if exist "D:\ASMR\openlrc\.venv\Scripts\python.exe" set "PY_EXE=D:\ASMR\openlrc\.venv\Scripts\python.exe"
if not defined PY_EXE set "PY_EXE=python"

"%PY_EXE%" "%~dp0app.py"
if errorlevel 1 (
  echo.
  echo Failed to start. Check the following:
  echo   1. Python environment (.venv or D:\ASMR\openlrc\.venv) exists.
  echo   2. Required dependencies are installed: pip install -r requirements.txt
  echo.
  pause
)
endlocal
