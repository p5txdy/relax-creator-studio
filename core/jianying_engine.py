from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .video_engine import ASPECT_SIZES, VideoClip, VideoProject, fit_clips_to_duration


# The checked-in vendor folder contains Windows native wheels.  A macOS build
# installs native dependencies into its own virtual environment instead.
VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
if os.name == "nt" and VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

try:
    import pyJianYingDraft as draft
except (ImportError, OSError) as exc:  # pragma: no cover - packaging failure path
    draft = None
    DRAFT_IMPORT_ERROR = exc
else:
    DRAFT_IMPORT_ERROR = None


class JianyingEngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class JianyingDraftResult:
    name: str
    path: str
    duration_seconds: float


INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SOURCE_VIDEO_VOLUME = 0.0


def sanitize_draft_name(value: str) -> str:
    cleaned = INVALID_NAME.sub("_", value).strip().rstrip(".")
    return cleaned[:80] or "解压视频混剪"


def unique_draft_name(root: str, requested: str) -> str:
    base = sanitize_draft_name(requested)
    candidate = base
    number = 2
    while (Path(root) / candidate).exists():
        candidate = f"{base}（{number}）"
        number += 1
    return candidate


def _is_launchable_application(path: Path) -> bool:
    return path.is_file() or (sys.platform == "darwin" and path.is_dir() and path.suffix.lower() == ".app")


def detect_jianying_executable(configured: str = "") -> str | None:
    if configured:
        configured_path = Path(configured.strip().strip('"')).expanduser()
        if _is_launchable_application(configured_path):
            return str(configured_path)
    if sys.platform == "darwin":
        candidates = [
            Path("/Applications/剪映专业版.app"),
            Path("/Applications/剪映.app"),
            Path("/Applications/JianyingPro.app"),
            Path("/Applications/JianyinPro.app"),
            Path.home() / "Applications" / "剪映专业版.app",
            Path.home() / "Applications" / "剪映.app",
            Path.home() / "Applications" / "JianyingPro.app",
            Path.home() / "Applications" / "JianyinPro.app",
        ]
        return str(next((path for path in candidates if _is_launchable_application(path)), "")) or None
    local = Path(os.getenv("LOCALAPPDATA", ""))
    candidates = [
        Path("E:/JianyingPro/JianyingPro.exe"),
        local / "JianyingPro" / "Apps" / "JianyingPro.exe",
        local / "JianyingPro" / "JianyingPro.exe",
        Path("C:/Program Files/JianyingPro/JianyingPro.exe"),
        Path("C:/Program Files (x86)/JianyingPro/JianyingPro.exe"),
    ]
    return str(next((path for path in candidates if path.is_file()), "")) or None


def _draft_meta_candidates() -> list[Path]:
    if sys.platform == "darwin":
        return [
            Path.home() / "Movies" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft" / "root_meta_info.json",
            Path.home() / "Movies" / "JianyinPro" / "User Data" / "Projects" / "com.lveditor.draft" / "root_meta_info.json",
            Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft" / "root_meta_info.json",
            Path.home() / "Library" / "Application Support" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft" / "root_meta_info.json",
        ]
    local = Path(os.getenv("LOCALAPPDATA", ""))
    return [local / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft" / "root_meta_info.json"]


def _configured_roots_from_meta() -> list[Path]:
    roots: list[Path] = []
    for meta_path in _draft_meta_candidates():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        for item in data.get("all_draft_store", []):
            if isinstance(item, dict) and item.get("draft_root_path"):
                path = Path(str(item["draft_root_path"])).expanduser()
                if path.is_dir():
                    roots.append(path)
        root_path = data.get("root_path")
        if root_path and Path(str(root_path)).expanduser().is_dir():
            roots.append(Path(str(root_path)).expanduser())
    return roots


def detect_jianying_drafts_path(configured: str = "") -> str | None:
    if configured and Path(configured.strip().strip('"')).expanduser().is_dir():
        return str(Path(configured.strip().strip('"')).expanduser())
    roots = _configured_roots_from_meta()
    if roots:
        most_common = Counter(str(path) for path in roots).most_common(1)[0][0]
        return most_common
    if sys.platform == "darwin":
        candidates = [
            Path.home() / "Movies" / "JianyingPro Drafts",
            Path.home() / "Movies" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
            Path.home() / "Movies" / "JianyinPro" / "User Data" / "Projects" / "com.lveditor.draft",
            Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft",
        ]
    else:
        local = Path(os.getenv("LOCALAPPDATA", ""))
        candidates = [
            Path("E:/JianyingPro Drafts"),
            local / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
            Path.home() / "Videos" / "JianyingPro Drafts",
        ]
    return str(next((path for path in candidates if path.is_dir()), "")) or None


def _transition_type(name: str):
    if draft is None or name == "none":
        return None
    mapping = {
        "fade": draft.TransitionType.叠化,
        "wipeleft": draft.TransitionType.向左擦除,
        "slideright": draft.TransitionType.右移,
        "circleopen": draft.TransitionType.圆形遮罩,
        "smoothleft": draft.TransitionType.左移,
    }
    return mapping.get(name, draft.TransitionType.叠化)


def probe_audio_duration(path: str) -> float | None:
    if draft is None or not Path(path).is_file():
        return None
    try:
        return draft.AudioMaterial(path).duration / draft.SEC
    except Exception:
        return None


def probe_video_duration(path: str) -> float | None:
    """Read the real material duration without requiring a separate FFprobe install."""
    if draft is None or not Path(path).is_file():
        return None
    try:
        duration = draft.VideoMaterial(path).duration / draft.SEC
        return float(duration) if duration > 0 else None
    except Exception:
        return None


SRT_BLOCK = re.compile(
    r"(?ms)^\s*\d+\s*\n"
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n"
    r"(.*?)(?=\n\s*\n\s*\d+\s*\n|\Z)"
)


def _srt_time_to_us(value: str) -> int:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return round((int(hours) * 3600 + int(minutes) * 60 + float(rest)) * 1_000_000)


def _us_to_srt_time(value: int) -> str:
    milliseconds = max(0, value // 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def clamp_srt_text(text: str, duration_us: int) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[str] = []
    for match in SRT_BLOCK.finditer(normalized):
        start = _srt_time_to_us(match.group(1))
        end = min(_srt_time_to_us(match.group(2)), duration_us)
        content = match.group(3).strip()
        if start >= duration_us or end <= start or not content:
            continue
        number = len(blocks) + 1
        blocks.append(f"{number}\n{_us_to_srt_time(start)} --> {_us_to_srt_time(end)}\n{content}")
    if not blocks:
        raise JianyingEngineError("字幕文件中没有位于主音频时长内的有效 SRT 字幕。")
    return "\n\n".join(blocks) + "\n"


def _update_draft_meta(draft_path: Path, name: str, draft_id: str, duration: int) -> None:
    meta_path = draft_path / "draft_meta_info.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return
    now = int(time.time() * 1_000_000)
    meta.update(
        {
            "draft_id": draft_id,
            "draft_name": name,
            "draft_fold_path": draft_path.as_posix(),
            "draft_root_path": draft_path.parent.as_posix(),
            "tm_duration": duration,
            "tm_draft_create": now,
            "tm_draft_modified": now,
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def create_jianying_draft(project: VideoProject, drafts_root: str, requested_name: str) -> JianyingDraftResult:
    if draft is None:
        raise JianyingEngineError(f"剪映草稿组件未能加载：{DRAFT_IMPORT_ERROR}")
    if not project.clips:
        raise JianyingEngineError("请至少添加一个视频素材。")
    root = Path(drafts_root)
    if not root.is_dir():
        raise JianyingEngineError("剪映草稿目录不存在，请在“模型与工具”中重新选择。")
    for clip in project.clips:
        if not Path(clip.path).is_file():
            raise JianyingEngineError(f"找不到视频素材：{Path(clip.path).name}")
    if project.music_path and not Path(project.music_path).is_file():
        raise JianyingEngineError("找不到所选背景音乐。")
    if project.voice_path and not Path(project.voice_path).is_file():
        raise JianyingEngineError("找不到所选主音频。")
    if project.subtitles_path and not Path(project.subtitles_path).is_file():
        raise JianyingEngineError("找不到所选字幕文件。")

    name = unique_draft_name(str(root), requested_name)
    width, height = ASPECT_SIZES.get(project.aspect, ASPECT_SIZES["9:16"])
    try:
        folder = draft.DraftFolder(str(root))
        script = folder.create_draft(name, width, height, project.fps)
        draft_id = str(uuid.uuid4()).upper()
        script.content["id"] = draft_id
        script.content["create_time"] = int(time.time())
        voice_material = draft.AudioMaterial(project.voice_path) if project.voice_path else None
        target_duration = voice_material.duration if voice_material else None
        script.append_track(draft.TrackSpec(draft.TrackType.video, "主视频"))

        cursor = 0
        transition = _transition_type(project.transition)
        prepared_segments: list[tuple[object, int]] = []
        material_cache: dict[str, object] = {}
        available_clips: list[VideoClip] = []
        for clip in project.clips:
            material = material_cache.get(clip.path)
            if material is None:
                material = draft.VideoMaterial(clip.path)
                material_cache[clip.path] = material
            start = max(0, round(clip.start * draft.SEC))
            available = max(0, material.duration - start)
            requested = min(max(round(clip.duration * draft.SEC), 200_000), available)
            if requested <= 0:
                raise JianyingEngineError(f"素材截取起点超过文件时长：{Path(clip.path).name}")
            available_clips.append(
                VideoClip(clip.path, clip.start, requested / draft.SEC, material.duration / draft.SEC)
            )

        timeline_clips = fit_clips_to_duration(
            available_clips,
            target_duration / draft.SEC if target_duration is not None else 0.0,
            overlap=0.0,
            strategy=project.mix_strategy,
        )
        for clip in timeline_clips:
            material = material_cache[clip.path]
            start = max(0, round(clip.start * draft.SEC))
            available = max(0, material.duration - start)
            requested = min(max(round(clip.duration * draft.SEC), 200_000), available)
            remaining = target_duration - cursor if target_duration is not None else requested
            duration = min(requested, available, remaining)
            if duration <= 0:
                raise JianyingEngineError(f"素材截取起点超过文件时长：{Path(clip.path).name}")
            segment = draft.VideoSegment(
                material,
                draft.Timerange(cursor, duration),
                source_timerange=draft.Timerange(start, duration),
                # Imported clips are visual material only. Their embedded audio
                # must never leak into the voice-over/background-music mix.
                volume=SOURCE_VIDEO_VOLUME,
            )
            segment.add_background_filling("blur", 0.375)
            prepared_segments.append((segment, duration))
            cursor += duration

        for index, (segment, duration) in enumerate(prepared_segments):
            if transition is not None and index < len(prepared_segments) - 1:
                next_duration = prepared_segments[index + 1][1]
                max_transition = min(project.transition_duration, duration / draft.SEC / 2, next_duration / draft.SEC / 2)
                segment.add_transition(transition, duration=round(max(0.1, max_transition) * draft.SEC))
            script.add_segment(segment, "主视频")

        if voice_material is not None:
            script.append_track(draft.TrackSpec(draft.TrackType.audio, "主音频"))
            voice_segment = draft.AudioSegment(
                voice_material,
                draft.Timerange(0, voice_material.duration),
                source_timerange=draft.Timerange(0, voice_material.duration),
                volume=1.0,
            )
            script.add_segment(voice_segment, "主音频")

        if project.music_path and cursor > 0:
            script.append_track(draft.TrackSpec(draft.TrackType.audio, "背景音乐"))
            material = draft.AudioMaterial(project.music_path)
            audio_cursor = 0
            while audio_cursor < cursor:
                duration = min(material.duration, cursor - audio_cursor)
                audio_segment = draft.AudioSegment(
                    material,
                    draft.Timerange(audio_cursor, duration),
                    source_timerange=draft.Timerange(0, duration),
                    volume=project.music_volume,
                )
                script.add_segment(audio_segment, "背景音乐")
                audio_cursor += duration

        if project.subtitles_path and cursor > 0:
            if Path(project.subtitles_path).suffix.lower() != ".srt":
                raise JianyingEngineError("当前支持导入 SRT 字幕文件。")
            subtitle_text = Path(project.subtitles_path).read_text(encoding="utf-8-sig")
            clamped = clamp_srt_text(subtitle_text, cursor)
            temp_path = ""
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".srt", encoding="utf-8-sig", delete=False) as temp:
                    temp.write(clamped)
                    temp_path = temp.name
                script.import_srt(temp_path, "字幕")
            finally:
                if temp_path:
                    try:
                        Path(temp_path).unlink()
                    except OSError:
                        pass

        script.save()
        draft_path = root / name
        _update_draft_meta(draft_path, name, draft_id, cursor)
    except JianyingEngineError:
        raise
    except Exception as exc:
        raise JianyingEngineError(f"生成剪映草稿失败：{exc}") from exc

    return JianyingDraftResult(name, str(draft_path), cursor / 1_000_000)


def open_jianying(executable: str) -> None:
    path = Path(executable).expanduser()
    if not _is_launchable_application(path):
        raise JianyingEngineError("找不到剪映专业版程序，请在“模型与工具”中重新选择。")
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", str(path)], close_fds=True)
        else:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
            subprocess.Popen([str(path)], close_fds=True, creationflags=flags)
    except OSError as exc:
        raise JianyingEngineError(f"无法启动剪映：{exc}") from exc
