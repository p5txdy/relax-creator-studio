@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\解压创作工坊-v0.2.4.exe" (
  start "" "dist\解压创作工坊-v0.2.4.exe"
  exit /b 0
)
pythonw app.py
if errorlevel 1 python app.py
