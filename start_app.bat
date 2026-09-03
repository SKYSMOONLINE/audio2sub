@echo off
setlocal
cd /d "%~dp0"

set "PYW_EXE="
if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYW_EXE=%~dp0.venv\Scripts\pythonw.exe"
if not defined PYW_EXE if exist "D:\ASMR\openlrc\.venv\Scripts\pythonw.exe" set "PYW_EXE=D:\ASMR\openlrc\.venv\Scripts\pythonw.exe"
if not defined PYW_EXE set "PYW_EXE=pythonw"

start "" "%PYW_EXE%" desktop.py
endlocal
