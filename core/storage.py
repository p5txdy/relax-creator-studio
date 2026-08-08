from __future__ import annotations

import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_STATE: dict[str, Any] = {
    "settings": {
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "remember_api_key": True,
        "ffmpeg_path": "",
        "ffprobe_path": "",
        "jianying_exe": "",
        "jianying_drafts_path": "",
    },
    "video": {
        "project_name": "我的解压视频",
        "clips": [],
        "aspect": "9:16",
        "fps": 30,
        "transition": "fade",
        "transition_duration": 0.35,
        "mix_strategy": "balanced",
        "voice_path": "",
        "voice_duration": 0.0,
        "subtitles_path": "",
        "music_path": "",
        "music_volume": 0.28,
        "output_path": "",
        "mood": "治愈",
        "platform": "小红书",
        "post_copy": "",
    },
    "novel": {
        "project_name": "我的小说改文",
        "source_path": "",
        "source_text": "",
        "chapters": [],
        "results": {},
        "mode": "深度改写",
        "style": "节奏紧凑、画面感强",
        "perspective": "保持原视角",
        "target_length": "与原文接近",
        "custom_rules": "保留核心剧情和人物关系，不新增与主线冲突的设定。",
        "story_bible": "",
    },
}


def _merge(default: Any, saved: Any) -> Any:
    if isinstance(default, dict) and isinstance(saved, dict):
        merged = deepcopy(default)
        for key, value in saved.items():
            merged[key] = _merge(default[key], value) if key in default else value
        return merged
    return saved


class StateStore:
    """Small JSON store. API keys are deliberately never persisted."""

    def __init__(self, base_dir: Path | None = None) -> None:
        explicit_base = base_dir is not None
        if base_dir is None:
            if sys.platform == "darwin":
                base_dir = Path.home() / "Library" / "Application Support" / "RelaxCreatorStudio"
            else:
                app_data = os.getenv("APPDATA")
                base_dir = Path(app_data) / "RelaxCreatorStudio" if app_data else Path.home() / ".relax_creator_studio"
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "state.json"
        if not explicit_base:
            try:
                self.base_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.base_dir = Path(tempfile.gettempdir()) / "RelaxCreatorStudio"
                self.path = self.base_dir / "state.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return deepcopy(DEFAULT_STATE)
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                saved.get("settings", {}).pop("api_key", None)
                return _merge(DEFAULT_STATE, saved)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return deepcopy(DEFAULT_STATE)

    def save(self, state: dict[str, Any]) -> None:
        payload = deepcopy(state)
        payload.get("settings", {}).pop("api_key", None)
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.path)
        except OSError:
            # A read-only corporate profile should not make the creative UI unusable.
            return
