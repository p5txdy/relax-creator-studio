from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Sequence

from .video_engine import ASPECT_SIZES


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    text: str


def probe_audio_duration(path: str | Path) -> float | None:
    source = Path(path)
    if not source.is_file():
        return None
    try:
        from pymediainfo import MediaInfo

        media = MediaInfo.parse(str(source))
        for track in media.tracks:
            if str(getattr(track, "track_type", "")).lower() == "audio":
                duration_ms = float(getattr(track, "duration", 0.0) or 0.0)
                if duration_ms > 0:
                    return duration_ms / 1000.0
    except (ImportError, OSError, TypeError, ValueError):
        return None
    return None


_SRT_TIME = re.compile(
    r"(?P<sh>\d{1,2}):(?P<sm>\d{2}):(?P<ss>\d{2})[,.](?P<sms>\d{3})\s*-->\s*"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2}):(?P<es>\d{2})[,.](?P<ems>\d{3})"
)


def _seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def parse_srt_text(value: str) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    for block in re.split(r"\n\s*\n", value.replace("\r\n", "\n").replace("\r", "\n").strip()):
        match = _SRT_TIME.search(block)
        if not match:
            continue
        groups = match.groupdict()
        start = _seconds(groups["sh"], groups["sm"], groups["ss"], groups["sms"])
        end = _seconds(groups["eh"], groups["em"], groups["es"], groups["ems"])
        text = block[match.end() :].strip()
        if end > start and text:
            cues.append(SubtitleCue(start, end, text))
    return sorted(cues, key=lambda item: item.start)


def load_srt(path: str | Path) -> list[SubtitleCue]:
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return parse_srt_text(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return parse_srt_text(data.decode("utf-8", errors="replace"))


def _normalize_alignment_text(value: str) -> str:
    """Keep spoken characters only so punctuation does not disturb alignment."""
    return "".join(character.lower() for character in str(value) if character.isalnum())


def _map_text_offset(matcher: SequenceMatcher, source_offset: int) -> float:
    """Map a character offset from storyboard text into normalized subtitle text."""
    for _tag, source_start, source_end, cue_start, cue_end in matcher.get_opcodes():
        if source_start <= source_offset <= source_end:
            if source_end == source_start:
                return float(cue_end)
            fraction = (source_offset - source_start) / (source_end - source_start)
            return cue_start + fraction * (cue_end - cue_start)
    return float(len(matcher.b))


def _cue_text_offset_to_time(cues: Sequence[SubtitleCue], cue_offset: float, total_duration: float) -> float:
    normalized = [(cue, _normalize_alignment_text(cue.text)) for cue in cues]
    normalized = [(cue, text) for cue, text in normalized if text]
    cursor = 0
    for index, (cue, text) in enumerate(normalized):
        next_cursor = cursor + len(text)
        if cue_offset < next_cursor:
            fraction = min(max((cue_offset - cursor) / len(text), 0.0), 1.0)
            return min(max(cue.start + (cue.end - cue.start) * fraction, 0.0), total_duration)
        if cue_offset == next_cursor:
            if index + 1 < len(normalized):
                return min(max(normalized[index + 1][0].start, 0.0), total_duration)
            return min(max(cue.end, 0.0), total_duration)
        cursor = next_cursor
    return total_duration


def _content_aligned_durations(
    total_duration: float,
    cues: Sequence[SubtitleCue],
    shot_texts: Sequence[str],
) -> list[float] | None:
    normalized_shots = [_normalize_alignment_text(text) for text in shot_texts]
    normalized_cues = [_normalize_alignment_text(cue.text) for cue in cues]
    if not cues or not normalized_shots or any(not text for text in normalized_shots):
        return None
    storyboard_text = "".join(normalized_shots)
    subtitle_text = "".join(normalized_cues)
    if not storyboard_text or not subtitle_text:
        return None

    matcher = SequenceMatcher(None, storyboard_text, subtitle_text, autojunk=False)
    # A low score usually means the imported subtitle belongs to another audio
    # or script.  In that case a forced semantic mapping would be less reliable
    # than the legacy timing fallback.
    if matcher.ratio() < 0.68:
        return None

    boundaries = [0.0]
    source_cursor = 0
    for text in normalized_shots[:-1]:
        source_cursor += len(text)
        cue_offset = float(source_cursor) if storyboard_text == subtitle_text else _map_text_offset(matcher, source_cursor)
        boundary = _cue_text_offset_to_time(cues, cue_offset, total_duration)
        if boundary <= boundaries[-1]:
            return None
        boundaries.append(boundary)
    boundaries.append(total_duration)
    durations = [right - left for left, right in zip(boundaries, boundaries[1:])]
    if any(duration <= 0 for duration in durations):
        return None
    durations[-1] += total_duration - sum(durations)
    return durations


def allocate_shot_durations(
    total_duration: float,
    shot_count: int,
    cues: Sequence[SubtitleCue] = (),
    shot_texts: Sequence[str] = (),
) -> list[float]:
    if total_duration <= 0:
        raise ValueError("音频时长必须大于 0。")
    if shot_count <= 0:
        raise ValueError("至少需要一张分镜图片。")
    if shot_count == 1:
        return [total_duration]

    if len(shot_texts) == shot_count:
        aligned = _content_aligned_durations(total_duration, cues, shot_texts)
        if aligned is not None:
            return aligned

    boundaries = [0.0]
    if cues:
        for index in range(1, shot_count):
            cue_index = min(len(cues) - 1, int(index * len(cues) / shot_count))
            boundaries.append(min(max(cues[cue_index].start, 0.0), total_duration))
    elif len(shot_texts) == shot_count:
        weights = [max(len(_normalize_alignment_text(text)), 1) for text in shot_texts]
        total_weight = sum(weights)
        boundaries.extend(total_duration * sum(weights[:index]) / total_weight for index in range(1, shot_count))
    else:
        boundaries.extend(total_duration * index / shot_count for index in range(1, shot_count))
    boundaries.append(total_duration)

    if any(right - left < 0.35 for left, right in zip(boundaries, boundaries[1:])):
        return [total_duration / shot_count] * shot_count
    durations = [right - left for left, right in zip(boundaries, boundaries[1:])]
    durations[-1] += total_duration - sum(durations)
    return durations


def _subtitle_filter_path(path: str | Path) -> str:
    value = str(Path(path).resolve()).replace("\\", "/")
    for source, escaped in ((":", r"\:"), (",", r"\,"), ("[", r"\["), ("]", r"\]"), (";", r"\;"), ("'", r"\'")):
        value = value.replace(source, escaped)
    return value


def _motion_filter(index: int, frames: int, width: int, height: int, mode: str, fps: int) -> str:
    source_width = int(width * 1.10) // 2 * 2
    source_height = int(height * 1.10) // 2 * 2
    base = (
        f"scale={source_width}:{source_height}:force_original_aspect_ratio=increase,"
        f"crop={source_width}:{source_height}"
    )
    denominator = max(frames - 1, 1)
    x = "iw/2-(iw/zoom/2)"
    if mode == "无关键帧":
        zoom = "1.0"
        x = "0"
        y = "0"
    else:
        zoom = "1.08"
        move_up = mode == "向上移动关键帧" or (mode == "上下交替关键帧" and index % 2 == 0)
        if mode == "向下移动关键帧":
            move_up = False
        y = f"(ih-ih/zoom)*on/{denominator}" if move_up else f"(ih-ih/zoom)*(1-on/{denominator})"
    return (
        f"{base},zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},"
        f"trim=duration={frames / fps:.3f},setpts=PTS-STARTPTS,format=yuv420p"
    )


def build_comic_video_command(
    image_paths: Sequence[str],
    durations: Sequence[float],
    *,
    audio_path: str,
    subtitles_path: str = "",
    aspect: str = "9:16",
    motion_mode: str = "上下交替关键帧",
    ffmpeg_path: str = "ffmpeg",
    output_path: str,
    fps: int = 30,
) -> list[str]:
    if not image_paths or len(image_paths) != len(durations):
        raise ValueError("分镜图片与时长数量不一致。")
    if not Path(audio_path).is_file():
        raise ValueError("找不到漫画配音文件。")
    missing = [path for path in image_paths if not Path(path).is_file()]
    if missing:
        raise ValueError(f"找不到分镜图片：{Path(missing[0]).name}")
    if subtitles_path and not Path(subtitles_path).is_file():
        raise ValueError("找不到字幕文件。")

    total_duration = sum(float(item) for item in durations)
    width, height = ASPECT_SIZES.get(aspect, ASPECT_SIZES["9:16"])
    fps = min(max(int(fps), 15), 60)
    command: list[str] = [ffmpeg_path, "-hide_banner", "-y"]
    for path in image_paths:
        command.extend(["-i", path])
    audio_index = len(image_paths)
    command.extend(["-i", audio_path])

    filters: list[str] = []
    for index, duration in enumerate(durations):
        frames = max(2, round(max(float(duration), 0.35) * fps))
        motion = _motion_filter(index, frames, width, height, motion_mode, fps)
        filters.append(f"[{index}:v]{motion}[v{index}]")
    if len(image_paths) == 1:
        filters.append("[v0]null[vcat]")
    else:
        inputs = "".join(f"[v{index}]" for index in range(len(image_paths)))
        filters.append(f"{inputs}concat=n={len(image_paths)}:v=1:a=0[vcat]")
    video_map = "[vcat]"
    if subtitles_path:
        subtitle_path = _subtitle_filter_path(subtitles_path)
        style = "FontName=Microsoft YaHei,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H90000000,Outline=2,Shadow=0,Alignment=2,MarginV=90"
        filters.append(f"[vcat]subtitles=filename='{subtitle_path}':force_style='{style}'[vsub]")
        video_map = "[vsub]"
    filters.append(f"[{audio_index}:a]atrim=duration={total_duration:.3f},asetpts=PTS-STARTPTS[voice]")

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            video_map,
            "-map",
            "[voice]",
            "-t",
            f"{total_duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            output_path,
        ]
    )
    return command
