#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -d "dist-macos/漫画推文-v1.1.app" ]]; then
  open "dist-macos/漫画推文-v1.1.app"
  exit 0
fi

exec zsh "$SCRIPT_DIR/启动Mac开发版.command"
