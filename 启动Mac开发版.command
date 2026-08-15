#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -x ".venv-macos/bin/python" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 Python 3，请先安装 Python 3.12：https://www.python.org/downloads/macos/"
    read -k 1 "?按任意键退出..."
    exit 1
  fi
  echo "首次运行，正在准备 macOS 环境..."
  python3 -m venv .venv-macos
  source .venv-macos/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements-macos.txt
else
  source .venv-macos/bin/activate
fi

python app.py
