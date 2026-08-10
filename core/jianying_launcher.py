from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


class JianyingLaunchError(RuntimeError):
    pass


def _is_launchable(path: Path) -> bool:
    return path.is_file() or (sys.platform == "darwin" and path.is_dir() and path.suffix.lower() == ".app")


def detect_jianying_executable(configured: str = "") -> str | None:
    if configured:
        path = Path(configured.strip().strip('"')).expanduser()
        if _is_launchable(path):
            return str(path)
    if sys.platform == "darwin":
        candidates = [
            Path("/Applications/剪映专业版.app"),
            Path("/Applications/剪映.app"),
            Path("/Applications/JianyingPro.app"),
            Path.home() / "Applications" / "剪映专业版.app",
            Path.home() / "Applications" / "剪映.app",
        ]
    else:
        local = Path(os.getenv("LOCALAPPDATA", ""))
        candidates = [
            Path("E:/JianyingPro/JianyingPro.exe"),
            local / "JianyingPro" / "Apps" / "JianyingPro.exe",
            local / "JianyingPro" / "JianyingPro.exe",
            Path("C:/Program Files/JianyingPro/JianyingPro.exe"),
            Path("C:/Program Files (x86)/JianyingPro/JianyingPro.exe"),
        ]
    return str(next((path for path in candidates if _is_launchable(path)), "")) or None


def open_jianying(executable: str, media_path: str = "") -> None:
    app = Path(executable).expanduser()
    if not _is_launchable(app):
        raise JianyingLaunchError("找不到剪映专业版程序，请在“模型与工具”中选择剪映程序。")
    media = Path(media_path).expanduser() if media_path else None
    if media is not None and not media.is_file():
        raise JianyingLaunchError("找不到要交给剪映的视频文件。")
    try:
        if sys.platform == "darwin":
            command = ["/usr/bin/open", "-a", str(app)]
            if media is not None:
                command.append(str(media))
            subprocess.Popen(command, close_fds=True)
        else:
            command = [str(app)]
            if media is not None:
                command.append(str(media))
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
            subprocess.Popen(command, close_fds=True, creationflags=flags)
    except OSError as exc:
        raise JianyingLaunchError(f"无法启动剪映：{exc}") from exc
