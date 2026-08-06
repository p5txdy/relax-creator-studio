#!/bin/zsh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -d "dist-macos/解压创作工坊-v0.2.1.app" ]]; then
  open "dist-macos/解压创作工坊-v0.2.1.app"
  exit 0
fi

if [[ ! -x ".venv-macos/bin/python" ]]; then
  echo "尚未准备 Mac 运行环境，请先双击“打包Mac版.command”。"
  read -k 1 "?按任意键退出..."
  exit 1
fi

source .venv-macos/bin/activate
python app.py
