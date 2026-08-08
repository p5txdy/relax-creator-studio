#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[1/6] 检查 Mac 打包环境"
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3，请先从 https://www.python.org/downloads/macos/ 安装 Python 3.11。"
  read -k 1 "?按任意键退出..."
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "未找到 Homebrew。请先安装 Homebrew，再运行：brew install mediainfo ffmpeg"
  read -k 1 "?按任意键退出..."
  exit 1
fi

if ! brew --prefix mediainfo >/dev/null 2>&1; then
  echo "正在安装草稿读取所需的 MediaInfo..."
  brew install mediainfo
fi

echo "[2/6] 创建独立打包环境"
python3 -m venv .venv-macos
source .venv-macos/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-macos.txt

echo "[3/6] 查找 MediaInfo 动态库"
MEDIAINFO_PREFIX="$(brew --prefix mediainfo)"
MEDIAINFO_LIB=""
for candidate in "$MEDIAINFO_PREFIX/lib/libmediainfo.0.dylib" "$MEDIAINFO_PREFIX/lib/libmediainfo.dylib"; do
  if [[ -f "$candidate" ]]; then
    MEDIAINFO_LIB="$candidate"
    break
  fi
done
if [[ -z "$MEDIAINFO_LIB" ]]; then
  echo "未找到 libmediainfo.dylib，请运行 brew reinstall mediainfo 后重试。"
  read -k 1 "?按任意键退出..."
  exit 1
fi

echo "[4/6] 生成原生 .app"
export CREATOR_MEDIAINFO_LIB="$MEDIAINFO_LIB"
python -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "dist-macos" \
  --workpath "build-macos" \
  "解压创作工坊-mac.spec"

APP_PATH="$SCRIPT_DIR/dist-macos/解压创作工坊-v0.2.3.app"
echo "[5/6] 验证应用资源"
"$APP_PATH/Contents/MacOS/解压创作工坊" --self-test
codesign --force --deep --sign - "$APP_PATH"

echo "[6/6] 生成 DMG 安装包"
DMG_STAGE="$(mktemp -d)"
trap 'rm -rf "$DMG_STAGE"' EXIT
cp -R "$APP_PATH" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
hdiutil create \
  -volname "解压创作工坊 v0.2.3" \
  -srcfolder "$DMG_STAGE" \
  -ov \
  -format UDZO \
  "$SCRIPT_DIR/dist-macos/解压创作工坊-v0.2.3-macOS.dmg"

echo ""
echo "打包完成："
echo "  $APP_PATH"
echo "  $SCRIPT_DIR/dist-macos/解压创作工坊-v0.2.3-macOS.dmg"
echo "首次打开若被系统拦截，请在 Finder 中右键应用并选择“打开”。"
read -k 1 "?按任意键结束..."
