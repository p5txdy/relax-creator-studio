@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install --disable-pip-version-check --upgrade -r requirements-windows.txt
if errorlevel 1 exit /b 1
if not exist ".\vendor\pymediainfo\__init__.py" (
  python -m pip install --disable-pip-version-check --upgrade --target ".\vendor" "pymediainfo==7.0.1"
  if errorlevel 1 exit /b 1
)
if not exist ".\vendor\pymediainfo\MediaInfo.dll" (
  echo 未找到 vendor\pymediainfo\MediaInfo.dll，请重新运行本脚本安装 Windows 媒体组件。
  exit /b 1
)
if not exist ".\vendor\pyJianYingDraft\__init__.py" (
  python -m pip install --disable-pip-version-check --upgrade --target ".\vendor" "pyJianYingDraft==0.3.0"
  if errorlevel 1 exit /b 1
)
python -m PyInstaller --noconfirm --clean --windowed --onefile --paths ".\vendor" --add-binary ".\vendor\pymediainfo\MediaInfo.dll;pymediainfo" --add-data ".\vendor\pyJianYingDraft\assets;pyJianYingDraft\assets" --exclude-module "uiautomation" --exclude-module "comtypes" --exclude-module "imageio" --exclude-module "numpy" --name "漫画推文-v1.1" --distpath "dist" --workpath "build-v1.1" --specpath "." "app.py"
if errorlevel 1 (
  echo.
  echo 打包失败，请检查上面的错误信息。
  if not defined CI pause
  exit /b 1
)
echo.
echo 打包完成：%CD%\dist\漫画推文-v1.1.exe
if not defined CI pause
