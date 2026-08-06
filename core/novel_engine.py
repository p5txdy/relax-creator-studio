from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    content: str


CHAPTER_PATTERN = re.compile(
    r"(?im)^(\s*(?:第[零〇一二两三四五六七八九十百千万\d]+[章节卷回]|chapter\s+\d+)[^\n]*)$"
)


def split_chapters(text: str, fallback_size: int = 5000) -> list[Chapter]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    matches = list(CHAPTER_PATTERN.finditer(normalized))
    chapters: list[Chapter] = []
    if matches:
        for number, match in enumerate(matches, start=1):
            end = matches[number].start() if number < len(matches) else len(normalized)
            title = match.group(1).strip()
            content = normalized[match.end() : end].strip()
            if content:
                chapters.append(Chapter(number, title, content))
        prefix = normalized[: matches[0].start()].strip()
        if prefix:
            chapters.insert(0, Chapter(0, "序章", prefix))
        return [Chapter(i + 1, item.title, item.content) for i, item in enumerate(chapters)]

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    current: list[str] = []
    current_size = 0
    for paragraph in paragraphs:
        if current and current_size + len(paragraph) > fallback_size:
            index = len(chapters) + 1
            chapters.append(Chapter(index, f"片段 {index}", "\n\n".join(current)))
            current, current_size = [], 0
        current.append(paragraph)
        current_size += len(paragraph)
    if current:
        index = len(chapters) + 1
        chapters.append(Chapter(index, f"片段 {index}", "\n\n".join(current)))
    return chapters


def chapter_records(text: str, fallback_size: int = 5000) -> list[dict[str, str]]:
    """Convert imported or directly pasted text into the app's editable chapter records."""
    return [
        {"title": chapter.title, "content": chapter.content}
        for chapter in split_chapters(text, fallback_size=fallback_size)
    ]


def build_rewrite_prompt(
    chapter_title: str,
    content: str,
    *,
    mode: str,
    style: str,
    perspective: str,
    target_length: str,
    custom_rules: str,
    story_bible: str,
) -> tuple[str, str]:
    system = (
        "你是一名严谨的中文小说编辑。只处理用户有权编辑的文本。"
        "你的任务是提高原创表达、可读性和叙事质量，而不是模仿在世作家的独特文风。"
        "必须维持人物动机、事件因果、称谓和时空连续性。只输出改写后的章节正文，不要解释。"
    )
    bible = story_bible.strip() or "（暂未填写；请仅依据本章保持自洽。）"
    rules = custom_rules.strip() or "保留核心剧情，不改变关键因果。"
    user = f"""请改写下面的小说章节。

【章节】{chapter_title}
【模式】{mode}
【目标文风】{style}
【叙事视角】{perspective}
【篇幅要求】{target_length}
【自定义规则】{rules}

【人物与世界观设定】
{bible}

【必须遵守】
1. 保留核心剧情、关键冲突和有效信息。
2. 修正语病、重复、跳跃和不自然对白。
3. 不擅自添加破坏后续剧情的新设定。
4. 使用自然的中文段落格式。
5. 不输出分析、标题说明或 Markdown 代码块。

【待改写正文】
{content.strip()}
"""
    return system, user


def build_post_prompt(
    project_name: str,
    mood: str,
    platform: str,
    clip_names: list[str],
    duration_seconds: float,
) -> tuple[str, str]:
    system = "你是短视频文案编辑。输出自然、有画面感、不夸大、不使用虚假承诺的中文发布文案。"
    material = "、".join(clip_names[:12]) or "用户导入的解压视频素材"
    user = f"""为一个解压视频生成发布文案。
项目：{project_name}
平台：{platform}
氛围：{mood}
成片时长：约 {duration_seconds:.0f} 秒
素材线索：{material}

请输出：
第一行：15字以内的标题
第二部分：60—120字正文，口吻自然
最后一行：3—6个相关话题标签
不要使用“保证治愈”等绝对化表达。"""
    return system, user
