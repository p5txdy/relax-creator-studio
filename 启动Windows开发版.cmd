@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\漫画推文-v1.1.exe" (
  start "" "dist\漫画推文-v1.1.exe"
  exit /b 0
)
if exist ".venv-windows\Scripts\pythonw.exe" (
  start "" ".venv-windows\Scripts\pythonw.exe" app.py
  exit /b 0
)
pythonw app.py
if errorlevel 1 (
  echo 未找到可用的 Python 运行环境，请按照 README 的“Windows 运行”安装依赖。
  python app.py
)
