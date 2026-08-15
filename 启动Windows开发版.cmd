@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv-windows\Scripts\pythonw.exe" (
  echo 首次运行，正在准备 Windows 环境...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.12 -m venv .venv-windows
  ) else (
    python -m venv .venv-windows
  )
  if errorlevel 1 goto :python_error
  ".venv-windows\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
  if errorlevel 1 goto :install_error
  ".venv-windows\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-windows.txt
  if errorlevel 1 goto :install_error
)

start "" ".venv-windows\Scripts\pythonw.exe" app.py
exit /b 0

:python_error
echo.
echo 未找到可用的 Python 3.12，请先安装 64 位 Python 3.12 后重试。
pause
exit /b 1

:install_error
echo.
echo 依赖安装失败，请检查网络后重新双击本文件。
pause
exit /b 1
