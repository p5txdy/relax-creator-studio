@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --windowed --onefile --paths ".\vendor" --add-data ".\vendor\pyJianYingDraft\assets;pyJianYingDraft\assets" --add-binary ".\vendor\pymediainfo\MediaInfo.dll;pymediainfo" --exclude-module "uiautomation" --exclude-module "comtypes" --exclude-module "imageio" --exclude-module "numpy" --name "解压创作工坊-v0.2.0" --distpath "dist" --workpath "build-v0.2.0" --specpath "." "app.py"
if errorlevel 1 (
  echo.
  echo 打包失败，请检查上面的错误信息。
  pause
  exit /b 1
)
echo.
echo 打包完成：%CD%\dist\解压创作工坊-v0.2.0.exe
pause
