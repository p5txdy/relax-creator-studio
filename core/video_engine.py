from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


ASPECT_SIZES = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}
DEFAULT_PLAYBACK_SPEED = 1.5


@dataclass
class VideoClip:
    path: str
    start: float = 0.0
    duration: float = 0.0
    source_duration: float = 0.0

    @property
    def name(self) -> str:
        return Path(self.path).name


@dataclass
class VideoProject:
    clips: list[VideoClip] = field(default_factory=list)
    aspect: str = "9:16"
    fps: int = 30
    transition: str = "fade"
    transition_duration: float = 0.35
    voice_path: str = ""
    subtitles_path: str = ""
    target_duration: float = 0.0
    mix_strategy: str = "balanced"
    playback_speed: float = DEFAULT_PLAYBACK_SPEED
    music_path: str = ""
    music_volume: float = 0.28

    @property
    def output_duration(self) -> float:
        if self.target_duration > 0:
            return self.target_duration
        speed = min(max(float(self.playback_speed), 0.25), 4.0)
        timeline_durations = [max(0.2, clip.duration / speed) for clip in self.clips]
        total = sum(timeline_durations)
        if len(self.clips) > 1 and self.transition != "none":
            shortest = min(timeline_durations)
            transition = min(max(self.transition_duration, 0.0), 2.0, shortest / 2)
            total -= transition * (len(self.clips) - 1)
        return max(0.0, total)


def _copy_clip(clip: VideoClip, duration: float | None = None) -> VideoClip:
    return VideoClip(
        clip.path,
        float(clip.start),
        max(0.2, float(clip.duration if duration is None else duration)),
        float(clip.source_duration),
    )


def _waterfill_durations(maximums: list[float], budget: float) -> list[float]:
    """Distribute a duration budget evenly without exceeding any source range."""
    allocations = [0.0] * len(maximums)
    active = set(range(len(maximums)))
    remaining = max(0.0, budget)
    while active and remaining > 0.000001:
        share = remaining / len(active)
        capped = [index for index in active if maximums[index] <= share + 0.000001]
        if not capped:
            for index in active:
                allocations[index] = share
            remaining = 0.0
            break
        for index in capped:
            allocations[index] = maximums[index]
            remaining -= maximums[index]
            active.remove(index)
    return allocations


def _evenly_spaced_clips(clips: list[VideoClip], count: int) -> list[VideoClip]:
    if count >= len(clips):
        return clips
    if count <= 1:
        return [clips[0]]
    indexes = [round(index * (len(clips) - 1) / (count - 1)) for index in range(count)]
    return [clips[index] for index in indexes]


def _balanced_partial_cycle(
    clips: list[VideoClip], effective_budget: float, overlap: float, leading_overlap: bool
) -> list[VideoClip]:
    """Use as many source clips as practical and share the remaining timeline evenly."""
    minimum_segment = max(0.5, overlap + 0.1)
    for count in range(len(clips), 0, -1):
        selected = _evenly_spaced_clips(clips, count)
        overlap_count = count - 1 + (1 if leading_overlap else 0)
        raw_budget = effective_budget + overlap * overlap_count
        maximums = [max(0.2, float(clip.duration)) for clip in selected]
        allocations = _waterfill_durations(maximums, raw_budget)
        if count == 1 or all(
            duration + 0.000001 >= min(minimum_segment, maximum)
            for duration, maximum in zip(allocations, maximums)
        ):
            return [_copy_clip(clip, duration) for clip, duration in zip(selected, allocations) if duration >= 0.2]
    return [_copy_clip(clips[0], effective_budget)]


def fit_clips_to_duration(
    clips: list[VideoClip],
    target_duration: float,
    overlap: float = 0.0,
    strategy: str = "balanced",
) -> list[VideoClip]:
    """Build an exact-length timeline using balanced or sequential source allocation."""
    if target_duration <= 0 or not clips:
        return [_copy_clip(clip) for clip in clips]
    normalized = [_copy_clip(clip) for clip in clips]
    overlap = min(max(0.0, float(overlap)), min(clip.duration for clip in normalized) / 2)
    if strategy == "sequential":
        result: list[VideoClip] = []
        effective = 0.0
        index = 0
        while effective < target_duration - 0.001:
            source = normalized[index % len(normalized)]
            segment_overlap = overlap if result else 0.0
            needed = target_duration - effective + segment_overlap
            duration = min(source.duration, needed)
            if duration <= segment_overlap and result:
                duration = min(source.duration, segment_overlap + 0.05)
            result.append(_copy_clip(source, duration))
            effective += duration - segment_overlap
            index += 1
            if index > 10000:
                raise ValueError("素材片段过短，无法安全铺满主音频时长。")
        return result

    result: list[VideoClip] = []
    effective = 0.0
    while effective < target_duration - 0.001:
        leading_overlap = bool(result)
        cycle_overlap_count = len(normalized) - 1 + (1 if leading_overlap else 0)
        cycle_overlap = overlap * cycle_overlap_count
        cycle_effective = sum(clip.duration for clip in normalized) - cycle_overlap
        remaining = target_duration - effective
        if cycle_effective > 0 and remaining >= cycle_effective - 0.001:
            result.extend(_copy_clip(clip) for clip in normalized)
            effective += cycle_effective
        else:
            partial = _balanced_partial_cycle(normalized, remaining, overlap, leading_overlap)
            result.extend(partial)
            partial_overlap_count = len(partial) - 1 + (1 if leading_overlap else 0)
            effective += sum(clip.duration for clip in partial) - overlap * max(0, partial_overlap_count)
        if len(result) > 10000:
            raise ValueError("素材片段过短，无法安全铺满主音频时长。")
    return result


def find_executable(configured: str, name: str) -> str | None:
    if configured:
        path = Path(configured.strip().strip('"'))
        if path.is_file():
            return str(path)
    located = shutil.which(name)
    if located:
        return located
    candidates = [
        Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe",
        Path("C:/ffmpeg/bin") / f"{name}.exe",
        Path("C:/Program Files/ffmpeg/bin") / f"{name}.exe",
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        Path("/opt/local/bin") / name,
    ]
    return str(next((path for path in candidates if path.is_file()), "")) or None


def probe_duration(path: str, ffprobe_path: str | None) -> float | None:
    if not ffprobe_path:
        return None
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
        data = json.loads(completed.stdout)
        return float(data["format"]["duration"])
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def build_export_command(project: VideoProject, ffmpeg_path: str, output_path: str) -> list[str]:
    if not project.clips:
        raise ValueError("请至少添加一个视频素材。")
    speed = min(max(float(project.playback_speed), 0.25), 4.0)
    timeline_sources = [
        VideoClip(
            clip.path,
            clip.start,
            max(0.2, float(clip.duration) / speed),
            clip.source_duration,
        )
        for clip in project.clips
    ]
    requested_transition = min(max(float(project.transition_duration), 0.1), 2.0)
    shortest_clip = min(clip.duration for clip in timeline_sources)
    overlap = min(requested_transition, shortest_clip / 2) if project.transition != "none" else 0.0
    clips = fit_clips_to_duration(timeline_sources, project.target_duration, overlap, project.mix_strategy)
    missing = [clip.path for clip in clips if not Path(clip.path).is_file()]
    if missing:
        raise ValueError(f"找不到素材：{Path(missing[0]).name}")
    if project.music_path and not Path(project.music_path).is_file():
        raise ValueError("找不到所选背景音乐。")
    if project.voice_path and not Path(project.voice_path).is_file():
        raise ValueError("找不到所选主音频。")

    width, height = ASPECT_SIZES.get(project.aspect, ASPECT_SIZES["9:16"])
    fps = min(max(int(project.fps), 15), 60)
    transition_duration = overlap
    command: list[str] = [ffmpeg_path, "-hide_banner", "-y"]
    for clip in clips:
        source_duration = max(0.2, clip.duration * speed)
        command.extend(["-ss", f"{max(0.0, clip.start):.3f}", "-t", f"{source_duration:.3f}", "-i", clip.path])
    voice_index = None
    if project.voice_path:
        voice_index = len(clips)
        command.extend(["-i", project.voice_path])
    music_index = None
    if project.music_path:
        music_index = len(clips) + (1 if voice_index is not None else 0)
        command.extend(["-stream_loop", "-1", "-i", project.music_path])

    filters: list[str] = []
    for index, _clip in enumerate(clips):
        filters.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p,"
            f"setpts=(PTS-STARTPTS)/{speed:.6f}[v{index}]"
        )

    if len(clips) == 1:
        video_map = "[v0]"
    elif project.transition == "none":
        sources = "".join(f"[v{i}]" for i in range(len(clips)))
        filters.append(f"{sources}concat=n={len(clips)}:v=1:a=0[vout]")
        video_map = "[vout]"
    else:
        transition = project.transition if project.transition in {"fade", "wipeleft", "slideright", "circleopen", "smoothleft"} else "fade"
        elapsed = clips[0].duration
        previous = "v0"
        for index in range(1, len(clips)):
            elapsed -= transition_duration
            output = "vout" if index == len(clips) - 1 else f"vx{index}"
            filters.append(
                f"[{previous}][v{index}]xfade=transition={transition}:duration={transition_duration:.3f}:"
                f"offset={max(0.0, elapsed):.3f}[{output}]"
            )
            previous = output
            elapsed += clips[index].duration
        video_map = f"[{previous}]"

    if project.target_duration > 0:
        source_label = video_map.strip("[]")
        filters.append(
            f"[{source_label}]tpad=stop_mode=clone:stop_duration={project.target_duration:.3f},"
            f"trim=duration={project.target_duration:.3f},setpts=PTS-STARTPTS[vfinal]"
        )
        video_map = "[vfinal]"

    audio_map: str | None = None
    if voice_index is not None:
        filters.append(
            f"[{voice_index}:a]volume=1.0,atrim=duration={project.output_duration:.3f},"
            "asetpts=PTS-STARTPTS[voice]"
        )
    if music_index is not None:
        filters.append(
            f"[{music_index}:a]volume={min(max(project.music_volume, 0.0), 1.0):.2f},"
            f"atrim=duration={project.output_duration:.3f},asetpts=PTS-STARTPTS[aout]"
        )
    if voice_index is not None and music_index is not None:
        filters.append("[voice][aout]amix=inputs=2:duration=first:dropout_transition=2[mixed]")
        audio_map = "[mixed]"
    elif voice_index is not None:
        audio_map = "[voice]"
    elif music_index is not None:
        audio_map = "[aout]"

    command.extend(["-filter_complex", ";".join(filters), "-map", video_map])
    if audio_map:
        command.extend(["-map", audio_map, "-c:a", "aac", "-b:a", "192k", "-shortest"])
    else:
        command.append("-an")
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            output_path,
        ]
    )
    return command


TIME_PATTERN = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def run_export(
    command: list[str],
    total_duration: float,
    on_progress: Callable[[float, str], None] | None = None,
) -> None:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )
    tail: list[str] = []
    assert process.stderr is not None
    for line in process.stderr:
        tail.append(line.rstrip())
        tail = tail[-16:]
        match = TIME_PATTERN.search(line)
        if match and on_progress:
            hours, minutes, seconds = match.groups()
            elapsed = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            on_progress(min(0.99, elapsed / max(total_duration, 0.1)), line.strip())
    code = process.wait()
    if code != 0:
        raise RuntimeError("FFmpeg 导出失败：\n" + "\n".join(tail[-8:]))
    if on_progress:
        on_progress(1.0, "导出完成")
