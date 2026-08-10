from __future__ import annotations


DOUYIN_COMIC_MOTION = "抖音漫画推文效果"
LEGACY_ALTERNATING_MOTION = "上下交替关键帧"
UPWARD_MOTION = "向上移动关键帧"
DOWNWARD_MOTION = "向下移动关键帧"
STATIC_MOTION = "无关键帧"

MOTION_MODE_OPTIONS = (
    DOUYIN_COMIC_MOTION,
    UPWARD_MOTION,
    DOWNWARD_MOTION,
    STATIC_MOTION,
)


def normalize_motion_mode(value: object) -> str:
    """Return the persisted presentation mode, migrating the old default."""
    mode = str(value or "").strip()
    if mode == LEGACY_ALTERNATING_MOTION:
        return DOUYIN_COMIC_MOTION
    if mode in MOTION_MODE_OPTIONS:
        return mode
    return DOUYIN_COMIC_MOTION


def moves_up(mode: object, index: int) -> bool:
    """Choose the vertical pan direction without coupling it to the art style."""
    normalized = normalize_motion_mode(mode)
    if normalized == UPWARD_MOTION:
        return True
    if normalized == DOWNWARD_MOTION:
        return False
    return index % 2 == 0
