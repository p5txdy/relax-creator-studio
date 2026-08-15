from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .comic_presentation import DOUYIN_COMIC_MOTION, normalize_motion_mode
from .novel_engine import NOVEL_COMMENTARY_MODE, NOVEL_COMMENTARY_STYLE
from .seedream_client import LEGACY_SEEDREAM_PRO_MODEL, SEEDREAM_LITE_MODEL, SEEDREAM_PRO_MODEL, SEEDREAM_SIZES


COMIC_PROJECT_DEFAULT: dict[str, Any] = {
    "project_id": "",
    "project_name": "未命名漫画推文",
    "created_at": "",
    "updated_at": "",
    "asset_library_id": "",
    "source_path": "",
    "source_text": "",
    "art_style": "国风 3D 动漫，电影级光影，高细节",
    "aspect": "9:16",
    "analysis_chunk_chars": 3500,
    "resolution": "2K",
    "optimize_mode": "standard",
    "shot_image_model": "doubao-seedream-5-0-lite-260128",
    "workspace_step": 0,
    "output_dir": "",
    "audio_path": "",
    "audio_duration": 0.0,
    "subtitles_path": "",
    "motion_mode": DOUYIN_COMIC_MOTION,
    "video_output_path": "",
    "jianying_draft_path": "",
    "jianying_draft_name": "",
    "cover": {
        "title": "",
        "prompt": "",
        "character": "",
        "scene": "",
        "task_id": "",
        "status": "未生成",
        "progress": "0%",
        "image_url": "",
        "local_path": "",
        "error": "",
        "final_prompt": "",
        "image_model": "",
        "images": [],
    },
    "characters": [],
    "scenes": [],
    "shots": [],
}


NOVEL_DEFAULT: dict[str, Any] = {
    "project_name": "未命名小说",
    "source_path": "",
    "source_text": "",
    "mode": NOVEL_COMMENTARY_MODE,
    "style": NOVEL_COMMENTARY_STYLE,
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


def new_asset_library(name: str = "默认人物场景项") -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "library_id": uuid.uuid4().hex,
        "name": name.strip() or "未命名人物场景项",
        "created_at": now,
        "updated_at": now,
        "characters": [],
        "scenes": [],
    }


DEFAULT_STATE: dict[str, Any] = {
    "schema_version": 6,
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
        "ark_model": SEEDREAM_PRO_MODEL,
        "remember_ark_api_key": True,
    },
    "projects": [],
    "active_project_id": "",
    "asset_libraries": [],
    # Compatibility aliases for the asset library linked to the active project.
    "shared_characters": [],
    "shared_scenes": [],
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


def _merge_named_asset(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge duplicate shared assets without discarding an existing local reference."""
    merged = dict(current)
    for key, value in incoming.items():
        if key not in merged or merged.get(key) in (None, "", []):
            merged[key] = deepcopy(value)
    for path_key in ("local_path", "candidate_path"):
        current_path = Path(str(merged.get(path_key, "")).strip())
        incoming_path = Path(str(incoming.get(path_key, "")).strip())
        if incoming_path.is_file() and not current_path.is_file():
            merged[path_key] = str(incoming_path)
            if path_key == "local_path":
                for key in ("task_id", "image_url", "status"):
                    if incoming.get(key) not in (None, ""):
                        merged[key] = incoming[key]
            else:
                for key in ("task_id", "candidate_image_url", "status"):
                    if incoming.get(key) not in (None, ""):
                        merged[key] = incoming[key]
    return merged


def _consolidate_named_assets(primary: object, collections: list[object]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    sources = [primary, *collections]
    for source in sources:
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            record = dict(item)
            if name in positions:
                index = positions[name]
                result[index] = _merge_named_asset(result[index], record)
            else:
                positions[name] = len(result)
                result.append(record)
    return result


class StateStore:
    """Small JSON store. API keys are deliberately never persisted."""

    BACKUP_INTERVAL_SECONDS = 30.0

    def __init__(self, base_dir: Path | None = None) -> None:
        explicit_base = base_dir is not None
        self.legacy_base_dir: Path | None = None
        self._migration_source: Path | None = None
        self._lock_handle = None
        self._last_backup_at = 0.0
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
                    if str(settings.get("ark_model", "")).strip() == LEGACY_SEEDREAM_PRO_MODEL:
                        settings["ark_model"] = SEEDREAM_PRO_MODEL
                comic = saved.get("comic", {})
                if isinstance(comic, dict):
                    comic.pop("bot_type", None)
                    comic.pop("upscale_index", None)
                    comic.pop("segment_chars", None)
                novel = saved.get("novel", {})
                if isinstance(novel, dict) and int(saved.get("schema_version", 0) or 0) < 4:
                    if str(novel.get("mode", "")).strip() == "深度改写":
                        novel["mode"] = NOVEL_COMMENTARY_MODE
                    if str(novel.get("style", "")).strip() == "节奏紧凑、画面感强":
                        novel["style"] = NOVEL_COMMENTARY_STYLE
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
                    shot_model = str(project.get("shot_image_model", "")).strip()
                    if shot_model == LEGACY_SEEDREAM_PRO_MODEL:
                        shot_model = SEEDREAM_PRO_MODEL
                    if shot_model not in {SEEDREAM_LITE_MODEL, SEEDREAM_PRO_MODEL}:
                        shot_model = SEEDREAM_LITE_MODEL
                    project["shot_image_model"] = shot_model
                    if str(project.get("resolution", "")).strip().upper() not in SEEDREAM_SIZES:
                        project["resolution"] = "2K"
                    project["motion_mode"] = normalize_motion_mode(project.get("motion_mode"))
                    project["project_id"] = str(project.get("project_id", "")).strip() or uuid.uuid4().hex
                    now = datetime.now().isoformat(timespec="seconds")
                    project["created_at"] = str(project.get("created_at", "")).strip() or now
                    project["updated_at"] = str(project.get("updated_at", "")).strip() or project["created_at"]
                    normalized_projects.append(project)
                raw_libraries = state.get("asset_libraries", [])
                libraries: list[dict[str, Any]] = []
                if isinstance(raw_libraries, list):
                    for index, item in enumerate(raw_libraries, start=1):
                        if not isinstance(item, dict):
                            continue
                        library_id = str(item.get("library_id", "")).strip() or uuid.uuid4().hex
                        libraries.append(
                            {
                                "library_id": library_id,
                                "name": str(item.get("name", "")).strip() or f"人物场景项 {index}",
                                "created_at": str(item.get("created_at", "")).strip() or datetime.now().isoformat(timespec="seconds"),
                                "updated_at": str(item.get("updated_at", "")).strip() or datetime.now().isoformat(timespec="seconds"),
                                "characters": _consolidate_named_assets(item.get("characters", []), []),
                                "scenes": _consolidate_named_assets(item.get("scenes", []), []),
                            }
                        )
                old_characters = _consolidate_named_assets(
                    state.get("shared_characters", []),
                    [project.get("characters", []) for project in normalized_projects],
                )
                old_scenes = _consolidate_named_assets(
                    state.get("shared_scenes", []),
                    [project.get("scenes", []) for project in normalized_projects],
                )
                if not libraries:
                    default_library = new_asset_library("默认人物场景项")
                    default_library["characters"] = old_characters
                    default_library["scenes"] = old_scenes
                    libraries.append(default_library)
                elif old_characters or old_scenes:
                    libraries[0]["characters"] = _consolidate_named_assets(libraries[0].get("characters", []), [old_characters])
                    libraries[0]["scenes"] = _consolidate_named_assets(libraries[0].get("scenes", []), [old_scenes])
                libraries_by_id = {str(item["library_id"]): item for item in libraries}
                fallback_library = libraries[0]
                active_id = str(state.get("active_project_id", "")).strip()
                if active_id not in {str(item["project_id"]) for item in normalized_projects}:
                    active_id = str(normalized_projects[0]["project_id"]) if normalized_projects else ""
                active = next((item for item in normalized_projects if str(item["project_id"]) == active_id), None)
                for project in normalized_projects:
                    library = libraries_by_id.get(str(project.get("asset_library_id", "")).strip(), fallback_library)
                    project["asset_library_id"] = str(library["library_id"])
                    project["characters"] = library["characters"]
                    project["scenes"] = library["scenes"]
                active_library = libraries_by_id.get(str(active.get("asset_library_id", "")).strip(), fallback_library) if active else fallback_library
                state["projects"] = normalized_projects
                state["active_project_id"] = active_id
                state["asset_libraries"] = libraries
                state["shared_characters"] = active_library["characters"]
                state["shared_scenes"] = active_library["scenes"]
                state["comic"] = active if active is not None else _merge(
                    COMIC_PROJECT_DEFAULT,
                    {"asset_library_id": active_library["library_id"], "characters": active_library["characters"], "scenes": active_library["scenes"]},
                )
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
            match = re.fullmatch(r"(.+?)_(reference|candidate|imported_reference(?:_\d+)?)", stem)
            if match:
                found.append((match.group(1), "candidate" if match.group(2) == "candidate" else "reference", path))
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

    @staticmethod
    def _copy_asset_to_shared(path: Path, destination_dir: Path) -> Path:
        """Move the live reference to a project-independent folder by copying it."""
        if not path.is_file():
            return path
        try:
            if path.resolve().parent == destination_dir.resolve():
                return path
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / path.name
            if not destination.exists() or path.stat().st_mtime > destination.stat().st_mtime:
                shutil.copy2(path, destination)
            return destination
        except OSError:
            return path

    def _recover_local_assets(self, state: dict[str, Any]) -> dict[str, Any]:
        libraries = [item for item in state.get("asset_libraries", []) if isinstance(item, dict)]
        if not libraries:
            library = new_asset_library("默认人物场景项")
            library["characters"] = state.get("shared_characters", []) if isinstance(state.get("shared_characters"), list) else []
            library["scenes"] = state.get("shared_scenes", []) if isinstance(state.get("shared_scenes"), list) else []
            libraries = [library]
        libraries_by_id = {str(item.get("library_id", "")): item for item in libraries}
        projects = [item for item in state.get("projects", []) if isinstance(item, dict)]
        fallback_library = libraries[0]

        def recover_collection(library: dict[str, Any], kind: str, directories: list[Path]) -> list[dict[str, Any]]:
            records = library.get(kind, [])
            if not isinstance(records, list):
                records = []
            record_kind = "character" if kind == "characters" else "scene"
            destination = self.base_dir / "shared_assets" / "libraries" / str(library["library_id"]) / kind
            by_name = {
                str(item.get("name", "")).strip(): item
                for item in records
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            }
            by_path: dict[Path, dict[str, Any]] = {}
            for record in records:
                if not isinstance(record, dict):
                    continue
                for key, asset_type in (("local_path", "reference"), ("candidate_path", "candidate")):
                    path = Path(str(record.get(key, "")).strip())
                    if not path.is_file():
                        continue
                    path = self._copy_asset_to_shared(path, destination)
                    record[key] = str(path)
                    self._merge_recovered_file(record, asset_type, path, record_kind)
                    try:
                        by_path[path.resolve()] = record
                    except OSError:
                        by_path[path.absolute()] = record
            seen_dirs: set[Path] = set()
            for directory in [destination, *directories]:
                try:
                    resolved_dir = directory.resolve()
                except OSError:
                    resolved_dir = directory.absolute()
                if resolved_dir in seen_dirs:
                    continue
                seen_dirs.add(resolved_dir)
                for name, asset_type, path in self._asset_files(directory):
                    if not name:
                        continue
                    path = self._copy_asset_to_shared(path, destination)
                    try:
                        resolved_path = path.resolve()
                    except OSError:
                        resolved_path = path.absolute()
                    record = by_path.get(resolved_path) or by_name.get(name)
                    if record is None:
                        record = self._recovered_asset_record(name, record_kind)
                        records.append(record)
                        by_name[name] = record
                    by_path[resolved_path] = record
                    self._merge_recovered_file(record, asset_type, path, record_kind)
            library[kind] = records
            return records

        for project in projects:
            output_dir = str(project.get("output_dir", "")).strip()
            if output_dir and self.legacy_base_dir is not None:
                output_path = Path(output_dir)
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
                    except OSError:
                        pass

        for library_index, library in enumerate(libraries):
            library_id = str(library.get("library_id", ""))
            linked_projects = [item for item in projects if str(item.get("asset_library_id", "")) == library_id]
            character_dirs: list[Path] = []
            scene_dirs: list[Path] = []
            if library_index == 0:
                for root in self._asset_roots():
                    character_dirs.append(root / "shared_assets" / "characters")
                    scene_dirs.append(root / "shared_assets" / "scenes")
            for project in linked_projects:
                output_dir = str(project.get("output_dir", "")).strip()
                if output_dir:
                    character_dirs.append(Path(output_dir) / "characters")
                    scene_dirs.append(Path(output_dir) / "scenes")
            recover_collection(library, "characters", character_dirs)
            recover_collection(library, "scenes", scene_dirs)

        for project in projects:
            library = libraries_by_id.get(str(project.get("asset_library_id", "")), fallback_library)
            project["asset_library_id"] = str(library["library_id"])
            project["characters"] = library["characters"]
            project["scenes"] = library["scenes"]

        state["asset_libraries"] = libraries
        state["projects"] = projects
        active_id = str(state.get("active_project_id", "")).strip()
        active = next(
            (item for item in projects if isinstance(item, dict) and str(item.get("project_id", "")) == active_id),
            projects[0] if projects else None,
        )
        active_library = libraries_by_id.get(str(active.get("asset_library_id", "")), fallback_library) if active else fallback_library
        state["shared_characters"] = active_library["characters"]
        state["shared_scenes"] = active_library["scenes"]
        if active is not None:
            state["active_project_id"] = str(active.get("project_id", ""))
            state["comic"] = active
        elif isinstance(state.get("comic"), dict):
            state["comic"]["asset_library_id"] = str(active_library["library_id"])
            state["comic"]["characters"] = active_library["characters"]
            state["comic"]["scenes"] = active_library["scenes"]
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

    def _backup_current_state(self) -> bool:
        source = self.path if self.path.exists() else self._migration_source
        if source is None or not source.exists():
            return False
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
        return True

    def save(self, state: dict[str, Any]) -> None:
        payload = deepcopy(state)
        payload["schema_version"] = 6
        settings = payload.get("settings", {})
        if isinstance(settings, dict):
            for key in list(settings):
                if key == "api_key" or key.endswith("_api_key"):
                    settings.pop(key, None)
        projects = payload.get("projects", [])
        project_records = projects if isinstance(projects, list) else []
        comic = payload.get("comic", {})
        comic_record = comic if isinstance(comic, dict) else {}
        libraries = [item for item in payload.get("asset_libraries", []) if isinstance(item, dict)]
        if not libraries:
            library = new_asset_library("默认人物场景项")
            library["characters"] = _consolidate_named_assets(payload.get("shared_characters", []), [])
            library["scenes"] = _consolidate_named_assets(payload.get("shared_scenes", []), [])
            libraries = [library]
        libraries_by_id = {str(item.get("library_id", "")): item for item in libraries}
        fallback_library = libraries[0]
        for project in project_records:
            if not isinstance(project, dict):
                continue
            library = libraries_by_id.get(str(project.get("asset_library_id", "")), fallback_library)
            project["asset_library_id"] = str(library["library_id"])
            library["characters"] = _consolidate_named_assets(library.get("characters", []), [project.get("characters", [])])
            library["scenes"] = _consolidate_named_assets(library.get("scenes", []), [project.get("scenes", [])])
        active_library = libraries_by_id.get(str(comic_record.get("asset_library_id", "")), fallback_library)
        active_library["characters"] = _consolidate_named_assets(active_library.get("characters", []), [comic_record.get("characters", [])])
        active_library["scenes"] = _consolidate_named_assets(active_library.get("scenes", []), [comic_record.get("scenes", [])])
        payload["asset_libraries"] = libraries
        payload["shared_characters"] = []
        payload["shared_scenes"] = []
        if isinstance(projects, list):
            for project in projects:
                if isinstance(project, dict):
                    project["characters"] = []
                    project["scenes"] = []
        if isinstance(comic, dict):
            comic["characters"] = []
            comic["scenes"] = []
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            now = time.monotonic()
            migration_pending = self._migration_source is not None
            if migration_pending or now - self._last_backup_at >= self.BACKUP_INTERVAL_SECONDS:
                if self._backup_current_state():
                    self._last_backup_at = now
            temp_path = self.path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temp_path.replace(self.path)
        except OSError:
            # A read-only corporate profile should not make the creative UI unusable.
            return
