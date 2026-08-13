#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

pause_if_interactive() {
  if [[ -t 0 ]]; then
    read -k 1 "?按任意键继续..."
  fi
}

echo "[1/7] 检查 Mac 打包环境"
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3，请先从 https://www.python.org/downloads/macos/ 安装 Python 3.12。"
  pause_if_interactive
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "未找到 Homebrew。请先安装 Homebrew，再运行：brew install libmediainfo ffmpeg"
  pause_if_interactive
  exit 1
fi

if ! brew --prefix libmediainfo >/dev/null 2>&1; then
  echo "正在安装音频时长读取所需的 MediaInfo..."
  brew install libmediainfo
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "正在安装静态漫视频导出所需的 FFmpeg..."
  brew install ffmpeg
fi

echo "[2/7] 创建独立打包环境"
python3 -m venv .venv-macos
source .venv-macos/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-macos.txt

echo "[3/7] 查找 MediaInfo 动态库"
MEDIAINFO_PREFIX="$(brew --prefix libmediainfo)"
MEDIAINFO_LIB=""
for candidate in "$MEDIAINFO_PREFIX/lib/libmediainfo.0.dylib" "$MEDIAINFO_PREFIX/lib/libmediainfo.dylib"; do
  if [[ -f "$candidate" ]]; then
    MEDIAINFO_LIB="$candidate"
    break
  fi
done
if [[ -z "$MEDIAINFO_LIB" ]]; then
  echo "未找到 libmediainfo.dylib，请运行 brew reinstall libmediainfo 后重试。"
  pause_if_interactive
  exit 1
fi

echo "[4/7] 生成原生 .app"
export CREATOR_MEDIAINFO_LIB="$MEDIAINFO_LIB"
python -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "dist-macos" \
  --workpath "build-macos" \
  "漫画推文-mac.spec"

APP_PATH="$SCRIPT_DIR/dist-macos/漫画推文-v1.1.app"
echo "[5/7] 验证应用资源"
"$APP_PATH/Contents/MacOS/漫画推文" --self-test

echo "[6/7] 为应用添加本机签名"
codesign --force --deep --sign - "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"

echo "[7/7] 生成 DMG 安装包"
DMG_STAGE="$(mktemp -d)"
trap 'rm -rf "$DMG_STAGE"' EXIT
cp -R "$APP_PATH" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
hdiutil create \
  -volname "漫画推文 v1.1" \
  -srcfolder "$DMG_STAGE" \
  -ov \
  -format UDZO \
  "$SCRIPT_DIR/dist-macos/漫画推文-v1.1-macOS.dmg"

echo ""
echo "打包完成："
echo "  $APP_PATH"
echo "  $SCRIPT_DIR/dist-macos/漫画推文-v1.1-macOS.dmg"
echo "首次打开若被系统拦截，请在 Finder 中右键应用并选择“打开”。"
pause_if_interactive
