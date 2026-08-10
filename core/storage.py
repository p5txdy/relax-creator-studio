from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


COMIC_PROJECT_DEFAULT: dict[str, Any] = {
    "project_id": "",
    "project_name": "未命名漫画推文",
    "created_at": "",
    "updated_at": "",
    "source_path": "",
    "source_text": "",
    "art_style": "国风 3D 动漫，电影级光影，高细节",
    "aspect": "9:16",
    "analysis_chunk_chars": 3500,
    "resolution": "2K",
    "optimize_mode": "standard",
    "workspace_step": 0,
    "output_dir": "",
    "audio_path": "",
    "audio_duration": 0.0,
    "subtitles_path": "",
    "motion_mode": "上下交替关键帧",
    "video_output_path": "",
    "jianying_draft_path": "",
    "jianying_draft_name": "",
    "characters": [],
    "scenes": [],
    "shots": [],
}


NOVEL_DEFAULT: dict[str, Any] = {
    "project_name": "未命名小说",
    "source_path": "",
    "source_text": "",
    "mode": "深度改写",
    "style": "节奏紧凑、画面感强",
    "perspective": "保持原视角",
    "target_length": "与原文接近",
    "custom_rules": "保留核心剧情，不改变关键因果。",
    "story_bible": "",
    "chapters": [],
    "results": {},
}


def new_comic_project(name: str = "未命名漫画推文") -> dict[str, Any]:
    project = deepcopy(COMIC_PROJECT_DEFAULT)
    now = datetime.now().isoformat(timespec="seconds")
    project.update(
        {
            "project_id": uuid.uuid4().hex,
            "project_name": name.strip() or "未命名漫画推文",
            "created_at": now,
            "updated_at": now,
        }
    )
    return project


DEFAULT_STATE: dict[str, Any] = {
    "schema_version": 3,
    "settings": {
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "remember_api_key": True,
        "ffmpeg_path": "",
        "ffprobe_path": "",
        "jianying_exe": "",
        "jianying_drafts_path": "",
        "ark_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "ark_model": "doubao-seedream-5-0-pro-260628",
        "remember_ark_api_key": True,
    },
    "projects": [],
    "active_project_id": "",
    "shared_characters": [],
    "comic": deepcopy(COMIC_PROJECT_DEFAULT),
    "novel": deepcopy(NOVEL_DEFAULT),
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
        self.legacy_base_dir: Path | None = None
        self._migration_source: Path | None = None
        self._lock_handle = None
        if base_dir is None:
            if sys.platform == "darwin":
                app_support = Path.home() / "Library" / "Application Support"
                base_dir = app_support / "ComicPostStudio"
                self.legacy_base_dir = app_support / "RelaxCreatorStudio"
            else:
                app_data = os.getenv("APPDATA")
                if app_data:
                    base_dir = Path(app_data) / "ComicPostStudio"
                    self.legacy_base_dir = Path(app_data) / "RelaxCreatorStudio"
                else:
                    base_dir = Path.home() / ".comic_post_studio"
                    self.legacy_base_dir = Path.home() / ".relax_creator_studio"
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "state.json"
        self.lock_path = self.base_dir / "app.lock"
        if not explicit_base:
            try:
                self.base_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.base_dir = Path(tempfile.gettempdir()) / "RelaxCreatorStudio"
                self.path = self.base_dir / "state.json"

    def load(self) -> dict[str, Any]:
        source_path = self.path
        legacy_path = self.legacy_base_dir / "state.json" if self.legacy_base_dir is not None else None
        if not source_path.exists() and legacy_path is not None and legacy_path.exists():
            source_path = legacy_path
            self._migration_source = legacy_path
        if not source_path.exists():
            return self._recover_local_assets(deepcopy(DEFAULT_STATE))
        try:
            saved = json.loads(source_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                saved.pop("video", None)
                settings = saved.get("settings", {})
                if isinstance(settings, dict):
                    for key in list(settings):
                        if key == "api_key" or key.endswith("_api_key"):
                            settings.pop(key, None)
                    settings.pop("yunwu_base_url", None)
                comic = saved.get("comic", {})
                if isinstance(comic, dict):
                    comic.pop("bot_type", None)
                    comic.pop("upscale_index", None)
                    comic.pop("segment_chars", None)
                state = _merge(DEFAULT_STATE, saved)
                projects = state.get("projects", [])
                if not isinstance(projects, list):
                    projects = []
                if "projects" not in saved and isinstance(comic, dict):
                    legacy = _merge(COMIC_PROJECT_DEFAULT, comic)
                    legacy["project_id"] = str(legacy.get("project_id", "")).strip() or uuid.uuid4().hex
                    now = datetime.now().isoformat(timespec="seconds")
                    legacy["created_at"] = str(legacy.get("created_at", "")).strip() or now
                    legacy["updated_at"] = str(legacy.get("updated_at", "")).strip() or now
                    projects = [legacy]
                normalized_projects: list[dict[str, Any]] = []
                for item in projects:
                    if not isinstance(item, dict):
                        continue
                    project = _merge(COMIC_PROJECT_DEFAULT, item)
                    project.pop("bot_type", None)
                    project.pop("upscale_index", None)
                    project.pop("segment_chars", None)
                    if str(project.get("motion_mode", "")) not in {"上下交替关键帧", "向上移动关键帧", "向下移动关键帧", "无关键帧"}:
                        project["motion_mode"] = "上下交替关键帧"
                    project["project_id"] = str(project.get("project_id", "")).strip() or uuid.uuid4().hex
                    now = datetime.now().isoformat(timespec="seconds")
                    project["created_at"] = str(project.get("created_at", "")).strip() or now
                    project["updated_at"] = str(project.get("updated_at", "")).strip() or project["created_at"]
                    normalized_projects.append(project)
                shared = state.get("shared_characters", [])
                if not isinstance(shared, list):
                    shared = []
                by_name = {
                    str(item.get("name", "")).strip(): dict(item)
                    for item in shared
                    if isinstance(item, dict) and str(item.get("name", "")).strip()
                }
                for project in normalized_projects:
                    for character in project.get("characters", []):
                        if not isinstance(character, dict):
                            continue
                        name = str(character.get("name", "")).strip()
                        if name and name not in by_name:
                            by_name[name] = dict(character)
                shared = list(by_name.values())
                active_id = str(state.get("active_project_id", "")).strip()
                if active_id not in {str(item["project_id"]) for item in normalized_projects}:
                    active_id = str(normalized_projects[0]["project_id"]) if normalized_projects else ""
                active = next((item for item in normalized_projects if str(item["project_id"]) == active_id), None)
                for project in normalized_projects:
                    project["characters"] = shared
                state["projects"] = normalized_projects
                state["active_project_id"] = active_id
                state["shared_characters"] = shared
                state["comic"] = active if active is not None else _merge(COMIC_PROJECT_DEFAULT, {"characters": shared})
                return self._recover_local_assets(state)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return self._recover_local_assets(deepcopy(DEFAULT_STATE))

    @staticmethod
    def _asset_files(directory: Path) -> list[tuple[str, str, Path]]:
        if not directory.is_dir():
            return []
        found: list[tuple[str, str, Path]] = []
        for path in directory.iterdir():
            if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            stem = path.stem
            if stem.endswith("_reference"):
                found.append((stem[: -len("_reference")], "reference", path))
            elif stem.endswith("_candidate"):
                found.append((stem[: -len("_candidate")], "candidate", path))
        return found

    @staticmethod
    def _recovered_asset_record(name: str, kind: str) -> dict[str, Any]:
        status = "未生成"
        if kind == "character":
            status = "候选待确认"
        elif kind == "scene":
            status = "候选待确认"
        return {
            "name": name or ("恢复的角色" if kind == "character" else "恢复的场景"),
            "description": "",
            "prompt": "",
            "task_id": "",
            "image_url": "",
            "local_path": "",
            "candidate_path": "",
            "candidate_image_url": "",
            "status": status,
        }

    def _merge_recovered_file(self, record: dict[str, Any], asset_type: str, path: Path, kind: str) -> None:
        def is_inside(candidate: Path, directory: Path | None) -> bool:
            if directory is None:
                return False
            try:
                candidate.resolve().relative_to(directory.resolve())
                return True
            except (OSError, ValueError):
                return False

        if asset_type == "reference":
            current = Path(str(record.get("local_path", "")))
            prefer_new_copy = is_inside(path, self.base_dir) and is_inside(current, self.legacy_base_dir)
            if prefer_new_copy or not current.is_file() or path.stat().st_mtime > current.stat().st_mtime:
                record["local_path"] = str(path)
            record["status"] = "定妆已确认" if kind == "character" else "定景已确认"
        else:
            current = Path(str(record.get("candidate_path", "")))
            prefer_new_copy = is_inside(path, self.base_dir) and is_inside(current, self.legacy_base_dir)
            if prefer_new_copy or not current.is_file() or path.stat().st_mtime > current.stat().st_mtime:
                record["candidate_path"] = str(path)
            if not Path(str(record.get("local_path", ""))).is_file():
                record["status"] = "候选待确认"

    def _asset_roots(self) -> list[Path]:
        roots = [self.base_dir]
        if self.legacy_base_dir is not None and self.legacy_base_dir != self.base_dir:
            roots.append(self.legacy_base_dir)
        return roots

    def _localize_legacy_asset(self, path: Path, destination_dir: Path) -> Path:
        if self.legacy_base_dir is None:
            return path
        try:
            path.resolve().relative_to(self.legacy_base_dir.resolve())
        except (OSError, ValueError):
            return path
        destination = destination_dir / path.name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or path.stat().st_mtime > destination.stat().st_mtime:
                shutil.copy2(path, destination)
            return destination
        except OSError:
            return path

    def _recover_local_assets(self, state: dict[str, Any]) -> dict[str, Any]:
        shared = state.get("shared_characters", [])
        if not isinstance(shared, list):
            shared = []
        by_name = {
            str(item.get("name", "")).strip(): item
            for item in shared
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        }
        character_dirs: list[Path] = []
        for root in self._asset_roots():
            character_dirs.append(root / "shared_assets" / "characters")
            projects_root = root / "comic_projects"
            if projects_root.is_dir():
                character_dirs.extend(path for path in projects_root.glob("*/characters") if path.is_dir())
        recovered_characters = 0
        for directory in character_dirs:
            for name, asset_type, path in self._asset_files(directory):
                if not name:
                    continue
                path = self._localize_legacy_asset(path, self.base_dir / "shared_assets" / "characters")
                record = by_name.get(name)
                if record is None:
                    record = self._recovered_asset_record(name, "character")
                    shared.append(record)
                    by_name[name] = record
                    recovered_characters += 1
                self._merge_recovered_file(record, asset_type, path, "character")

        projects = state.get("projects", [])
        if not isinstance(projects, list):
            projects = []
        for project_index, project in enumerate(projects):
            if not isinstance(project, dict):
                continue
            scenes = project.get("scenes", [])
            if not isinstance(scenes, list):
                scenes = []
            for index, scene in enumerate(scenes, start=1):
                if isinstance(scene, dict) and not str(scene.get("name", "")).strip():
                    scene["name"] = f"场景 {index}"
            scene_dirs: list[Path] = []
            output_dir = str(project.get("output_dir", "")).strip()
            if output_dir:
                output_path = Path(output_dir)
                scene_dirs.append(output_path / "scenes")
                if self.legacy_base_dir is not None:
                    try:
                        relative_output = output_path.resolve().relative_to(self.legacy_base_dir.resolve())
                    except (OSError, ValueError):
                        pass
                    else:
                        localized_output = self.base_dir / relative_output
                        try:
                            if output_path.is_dir():
                                shutil.copytree(output_path, localized_output, dirs_exist_ok=True)
                            localized_output.mkdir(parents=True, exist_ok=True)
                            project["output_dir"] = str(localized_output)
                            scene_dirs.insert(0, localized_output / "scenes")
                        except OSError:
                            pass
            if len(projects) == 1:
                for root in self._asset_roots():
                    projects_root = root / "comic_projects"
                    if projects_root.is_dir():
                        scene_dirs.extend(path for path in projects_root.glob("*/scenes") if path.is_dir())
            scene_by_name = {
                str(item.get("name", "")).strip(): item
                for item in scenes
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            }
            for directory in scene_dirs:
                for name, asset_type, path in self._asset_files(directory):
                    if not name:
                        continue
                    path = self._localize_legacy_asset(
                        path,
                        self.base_dir / "comic_projects" / str(project.get("project_id", project_index + 1)) / "scenes",
                    )
                    record = scene_by_name.get(name)
                    if record is None:
                        record = self._recovered_asset_record(name, "scene")
                        scenes.append(record)
                        scene_by_name[name] = record
                    self._merge_recovered_file(record, asset_type, path, "scene")
            project["scenes"] = scenes
            project["characters"] = shared

        state["shared_characters"] = shared
        state["projects"] = projects
        active_id = str(state.get("active_project_id", "")).strip()
        active = next(
            (item for item in projects if isinstance(item, dict) and str(item.get("project_id", "")) == active_id),
            projects[0] if projects else None,
        )
        if active is not None:
            active["characters"] = shared
            state["active_project_id"] = str(active.get("project_id", ""))
            state["comic"] = active
        elif isinstance(state.get("comic"), dict):
            state["comic"]["characters"] = shared
        return state

    def acquire_instance_lock(self) -> bool:
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            handle = self.lock_path.open("a+b")
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_handle = handle
            return True
        except (OSError, ImportError):
            try:
                handle.close()
            except (OSError, UnboundLocalError):
                pass
            return False

    def release_instance_lock(self) -> None:
        handle = self._lock_handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ImportError):
            pass
        try:
            handle.close()
        except OSError:
            pass
        self._lock_handle = None

    def _backup_current_state(self) -> None:
        source = self.path if self.path.exists() else self._migration_source
        if source is None or not source.exists():
            return
        backups = self.base_dir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        prefix = "legacy-import" if source != self.path else "state"
        shutil.copy2(source, backups / f"{prefix}-{timestamp}.json")
        existing = sorted(backups.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in existing[20:]:
            try:
                old.unlink()
            except OSError:
                pass
        self._migration_source = None

    def save(self, state: dict[str, Any]) -> None:
        payload = deepcopy(state)
        payload["schema_version"] = 3
        settings = payload.get("settings", {})
        if isinstance(settings, dict):
            for key in list(settings):
                if key == "api_key" or key.endswith("_api_key"):
                    settings.pop(key, None)
        shared = payload.get("shared_characters", [])
        if not isinstance(shared, list):
            shared = []
        payload["shared_characters"] = shared
        projects = payload.get("projects", [])
        if isinstance(projects, list):
            for project in projects:
                if isinstance(project, dict):
                    project["characters"] = []
        comic = payload.get("comic", {})
        if isinstance(comic, dict):
            comic["characters"] = []
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self._backup_current_state()
            temp_path = self.path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.path)
        except OSError:
            # A read-only corporate profile should not make the creative UI unusable.
            return
