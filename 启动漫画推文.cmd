@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\漫画推文-v1.1.exe" (
  start "" "dist\漫画推文-v1.1.exe"
  exit /b 0
)
call "%~dp0启动Windows开发版.cmd"
