from __future__ import annotations

import base64
import json
import re
import shutil
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Iterable, Mapping, Sequence


class ComicEngineError(RuntimeError):
    pass


COMIC_ASSET_PACK_FORMAT = "relax-creator-studio/comic-assets"
COMIC_ASSET_PACK_VERSION = 1
COMIC_ASSET_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def safe_filename(value: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", value.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return (cleaned or fallback)[:80]


def image_data_url(path: str | Path) -> str:
    source = Path(path)
    data = source.read_bytes()
    if data.startswith(b"\x89PNG"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def has_local_reference(item: Mapping[str, object]) -> bool:
    path = str(item.get("local_path", "")).strip()
    return bool(path and Path(path).is_file())


def _portable_asset_record(item: Mapping[str, object]) -> dict[str, str]:
    return {
        "name": str(item.get("name", "")).strip(),
        "description": str(item.get("description", "")).strip(),
        "prompt": str(item.get("prompt", "")).strip(),
        "base_character": str(item.get("base_character", "")).strip(),
        "reference": "",
    }


def export_comic_asset_pack(
    destination: str | Path,
    *,
    characters: Sequence[Mapping[str, object]],
    scenes: Sequence[Mapping[str, object]],
    metadata: Mapping[str, object] | None = None,
) -> dict[str, int]:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "format": COMIC_ASSET_PACK_FORMAT,
        "version": COMIC_ASSET_PACK_VERSION,
        "metadata": {str(key): str(value) for key, value in (metadata or {}).items()},
        "characters": [],
        "scenes": [],
    }
    files: list[tuple[Path, str]] = []
    reference_count = 0
    for kind, items in (("characters", characters), ("scenes", scenes)):
        records: list[dict[str, str]] = []
        for index, item in enumerate(items, start=1):
            record = _portable_asset_record(item)
            if not record["name"]:
                continue
            local_path = Path(str(item.get("local_path", "")).strip())
            if local_path.is_file():
                suffix = local_path.suffix.lower()
                if suffix in COMIC_ASSET_IMAGE_SUFFIXES:
                    archive_path = f"{kind}/{index:03d}_{safe_filename(record['name'])}{suffix}"
                    record["reference"] = archive_path
                    files.append((local_path, archive_path))
                    reference_count += 1
            records.append(record)
        manifest[kind] = records
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            for source, archive_path in files:
                archive.write(source, archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ComicEngineError(f"无法导出漫画资产包：{exc}") from exc
    return {
        "characters": len(manifest["characters"]),
        "scenes": len(manifest["scenes"]),
        "references": reference_count,
    }


def _validated_pack_records(payload: object, kind: str) -> list[dict[str, str]]:
    if not isinstance(payload, list):
        raise ComicEngineError(f"资产包中的 {kind} 不是数组。")
    if len(payload) > 500:
        raise ComicEngineError(f"资产包中的 {kind} 数量超过 500，已拒绝导入。")
    records: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise ComicEngineError(f"资产包中的 {kind} 记录格式无效。")
        name = str(item.get("name", "")).strip()[:120]
        if not name:
            raise ComicEngineError(f"资产包中的 {kind} 存在空名称。")
        reference = str(item.get("reference", "")).replace("\\", "/").strip()
        if reference:
            portable = PurePosixPath(reference)
            if portable.is_absolute() or ".." in portable.parts or not portable.parts or portable.parts[0] != kind:
                raise ComicEngineError("资产包包含不安全的参考图路径，已拒绝导入。")
            if portable.suffix.lower() not in COMIC_ASSET_IMAGE_SUFFIXES:
                raise ComicEngineError("资产包包含不支持的参考图格式。")
        records.append(
            {
                "name": name,
                "description": str(item.get("description", "")).strip()[:8000],
                "prompt": str(item.get("prompt", "")).strip()[:16000],
                "base_character": str(item.get("base_character", "")).strip()[:120],
                "reference": reference,
            }
        )
    return records


def _unique_import_path(folder: Path, name: str, suffix: str, reserved: set[Path] | None = None) -> Path:
    reserved = reserved if reserved is not None else set()
    base = folder / f"{safe_filename(name)}_imported_reference{suffix}"
    if not base.exists() and base not in reserved:
        return base
    for index in range(2, 10000):
        candidate = folder / f"{safe_filename(name)}_imported_reference_{index}{suffix}"
        if not candidate.exists() and candidate not in reserved:
            return candidate
    raise ComicEngineError(f"无法为“{name}”分配本地参考图文件名。")


def import_comic_asset_pack(source: str | Path, destination_root: str | Path) -> dict[str, object]:
    package = Path(source)
    destination = Path(destination_root)
    try:
        with zipfile.ZipFile(package, "r") as archive:
            try:
                manifest_info = archive.getinfo("manifest.json")
            except KeyError as exc:
                raise ComicEngineError("所选文件不是有效的漫画资产包：缺少 manifest.json。") from exc
            if manifest_info.file_size > 2 * 1024 * 1024:
                raise ComicEngineError("漫画资产包清单过大，已拒绝导入。")
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            if not isinstance(manifest, Mapping) or manifest.get("format") != COMIC_ASSET_PACK_FORMAT:
                raise ComicEngineError("所选文件不是解压创作工坊漫画资产包。")
            if int(manifest.get("version", 0)) != COMIC_ASSET_PACK_VERSION:
                raise ComicEngineError("漫画资产包版本不受支持。")
            characters = _validated_pack_records(manifest.get("characters", []), "characters")
            scenes = _validated_pack_records(manifest.get("scenes", []), "scenes")
            prepared: list[tuple[dict[str, str], zipfile.ZipInfo, Path]] = []
            reserved_paths: set[Path] = set()
            total_size = 0
            for kind, records in (("characters", characters), ("scenes", scenes)):
                folder = destination / kind
                for record in records:
                    reference = record["reference"]
                    if not reference:
                        continue
                    try:
                        info = archive.getinfo(reference)
                    except KeyError as exc:
                        raise ComicEngineError(f"资产包缺少参考图：{reference}") from exc
                    if info.file_size > 100 * 1024 * 1024:
                        raise ComicEngineError(f"参考图过大，已拒绝导入：{reference}")
                    total_size += info.file_size
                    if total_size > 1024 * 1024 * 1024:
                        raise ComicEngineError("资产包解压后的参考图总大小超过 1GB，已拒绝导入。")
                    target = _unique_import_path(folder, record["name"], PurePosixPath(reference).suffix.lower(), reserved_paths)
                    reserved_paths.add(target)
                    prepared.append((record, info, target))
            for record, info, target in prepared:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as input_file, target.open("xb") as output_file:
                    shutil.copyfileobj(input_file, output_file)
                record["local_path"] = str(target)
    except ComicEngineError:
        raise
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise ComicEngineError(f"无法导入漫画资产包：{exc}") from exc

    def records_for_app(records: list[dict[str, str]], factory) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for item in records:
            record = factory(item["name"])
            record.update(
                {
                    "description": item["description"],
                    "prompt": item["prompt"],
                    "base_character": item.get("base_character", "") if factory is default_character else "",
                }
            )
            local_path = item.get("local_path", "")
            if local_path:
                record.update({"local_path": local_path, "status": "定妆已确认" if factory is default_character else "定景已确认"})
            result.append(record)
        return result

    metadata = manifest.get("metadata", {}) if isinstance(manifest.get("metadata"), Mapping) else {}
    return {
        "characters": records_for_app(characters, default_character),
        "scenes": records_for_app(scenes, default_scene),
        "metadata": dict(metadata),
    }


def split_story_segments(text: str, target_chars: int = 180) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    target = min(max(int(target_chars), 20), 600)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]
    if len(paragraphs) == 1:
        paragraphs = [part.strip() for part in re.split(r"(?<=[。！？!?])", normalized) if part.strip()]
    segments: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = [paragraph]
        if len(paragraph) > target * 2:
            pieces = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", paragraph) if part.strip()]
        expanded: list[str] = []
        for piece in pieces:
            if len(piece) > target * 2:
                expanded.extend(piece[start : start + target] for start in range(0, len(piece), target))
            else:
                expanded.append(piece)
        for piece in expanded:
            candidate = f"{current}\n{piece}".strip() if current else piece
            if current and len(candidate) > target:
                segments.append(current)
                current = piece
            else:
                current = candidate
    if current:
        segments.append(current)
    return segments


def numbered_story_segments(text: str, target_chars: int = 180) -> list[dict[str, str]]:
    return [
        {"segment_id": f"S{index:05d}", "source": source}
        for index, source in enumerate(split_story_segments(text, target_chars), start=1)
    ]


def split_story_source_chunks(text: str, max_chars: int = 3500) -> list[str]:
    """Split long source text into transport chunks, not storyboard segments.

    The size limit exists only to keep each model request manageable. Storyboard
    boundaries are deliberately left to the AI.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    limit = min(max(int(max_chars), 300), 8000)
    if len(normalized) <= limit:
        return [normalized]

    units = [part for part in re.split(r"(?<=[。！？!?；;])|\n+", normalized) if part and part.strip()]
    chunks: list[str] = []
    current = ""
    for unit in units:
        unit = unit.strip()
        pieces = [unit[start : start + limit] for start in range(0, len(unit), limit)] if len(unit) > limit else [unit]
        for piece in pieces:
            candidate = f"{current}\n{piece}".strip() if current else piece
            if current and len(candidate) > limit:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def batch_story_segments(
    segments: Sequence[Mapping[str, object]], max_chars: int = 3500
) -> list[list[dict[str, str]]]:
    limit = min(max(int(max_chars), 800), 8000)
    batches: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_chars = 0
    for item in segments:
        record = {
            "segment_id": str(item.get("segment_id", "")).strip(),
            "source": str(item.get("source", "")).strip(),
        }
        if not record["segment_id"] or not record["source"]:
            continue
        cost = len(record["source"]) + len(record["segment_id"]) + 12
        if current and current_chars + cost > limit:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(record)
        current_chars += cost
    if current:
        batches.append(current)
    return batches


def default_character(name: str = "新角色") -> dict[str, object]:
    return {
        "name": name,
        "base_character": "",
        "description": "",
        "prompt": "",
        "task_id": "",
        "image_url": "",
        "local_path": "",
        "candidate_path": "",
        "candidate_image_url": "",
        "status": "未生成",
    }


def default_scene(name: str = "新场景") -> dict[str, object]:
    return {
        "name": name,
        "description": "",
        "prompt": "",
        "task_id": "",
        "image_url": "",
        "local_path": "",
        "candidate_path": "",
        "candidate_image_url": "",
        "status": "未生成",
    }


KOREAN_WEBTOON_STYLE_CONSTRAINT = (
    "明确采用二维韩系网络漫画（Korean webtoon）插画表现，清晰精致线稿，平滑赛璐璐与柔和渐变上色，"
    "高颜值但保持漫画化五官，修长自然人体比例，干净的漫画肤色块和层次分明的漫画阴影，竖屏条漫质感；"
    "禁止真人照片、写实摄影、影视剧照、真人皮肤毛孔、照片级皮肤纹理、3D人物渲染和超写实风格"
)


def expand_art_style(art_style: str) -> str:
    """Turn a short style preset into a concrete image-model instruction."""
    style = art_style.strip() or "项目统一漫画画风"
    folded = style.casefold()
    if any(keyword in folded for keyword in ("韩漫", "韩系", "webtoon")):
        if KOREAN_WEBTOON_STYLE_CONSTRAINT not in style:
            return f"{style}；{KOREAN_WEBTOON_STYLE_CONSTRAINT}"
    return style


def _character_reference_constraint(art_style: str = "") -> str:
    style_rule = f"统一采用{expand_art_style(art_style)}；" if art_style.strip() else ""
    return (
        f"人物参考图硬性要求：{style_rule}画面中只出现该角色一人，独立正面全身定妆，纯白或浅灰无缝背景，"
        "不得出现任何室内外场景、建筑、房间、街道、自然环境、家具、环境道具、其他人物、文字、水印或标志"
    )


CHARACTER_REFERENCE_CONSTRAINT = _character_reference_constraint()

_CHARACTER_REFERENCE_CONSTRAINT_PATTERN = re.compile(
    r"[，。；;\s]*人物参考图硬性要求：.*$",
    flags=re.DOTALL,
)

_CHARACTER_VARIANT_CONSTRAINT_PATTERN = re.compile(
    r"[，。；;\s]*换装关联硬性要求：.*$",
    flags=re.DOTALL,
)

_SCENE_REFERENCE_CONSTRAINT_PATTERN = re.compile(
    r"[，。；;\s]*场景参考图硬性要求：.*$",
    flags=re.DOTALL,
)

_DYNAMIC_CAMERA_PATTERN = re.compile(
    r"镜头(?:缓慢)?(?:向前|向后|向左|向右)?(?:推进|推近|拉远|拉近|摇移|移动|跟拍|环绕|变焦)|"
    r"(?:推镜头|拉镜头|跟拍镜头|环绕镜头|变焦镜头|画面转场|慢动作镜头|动态运镜)"
)


def _character_prompt_base(prompt: str) -> str:
    base = _CHARACTER_REFERENCE_CONSTRAINT_PATTERN.sub("", prompt.strip()).rstrip("，。；; ")
    return _CHARACTER_VARIANT_CONSTRAINT_PATTERN.sub("", base).rstrip("，。；; ")


def enforce_character_reference_prompt(prompt: str, art_style: str = "") -> str:
    base = _character_prompt_base(prompt)
    constraint = _character_reference_constraint(art_style)
    if not base:
        return constraint
    return f"{base}，{constraint}"


def enforce_character_variant_prompt(
    prompt: str,
    base_character_name: str,
    art_style: str = "",
    clothing_request: str = "",
) -> str:
    """Lock identity to a confirmed base reference and allow clothing changes only."""
    base_name = base_character_name.strip()
    if not base_name:
        return enforce_character_reference_prompt(prompt, art_style)
    base = _character_prompt_base(prompt)
    request = clothing_request.strip()
    request_rule = f"本次只按以下要求换装：{request}；" if request else "本次只按当前提示中的服装要求换装；"
    variant_constraint = (
        f"换装关联硬性要求：{request_rule}以输入参考图中的“{base_name}”为唯一人物本体，必须保持同一人的脸型、五官、"
        "眼睛、发型、发色、肤色、年龄、性别、体态和身体比例完全一致；只允许按照当前描述改变服装、"
        "鞋袜和可穿戴服饰配件，不得重新设计人物，不得改变身份，不得改变面部与发型"
    )
    combined = f"{base}，{variant_constraint}" if base else variant_constraint
    character_constraint = _character_reference_constraint(art_style)
    return f"{combined}，{character_constraint}"


def enforce_scene_reference_prompt(
    prompt: str,
    art_style: str,
    aspect: str = "9:16",
) -> str:
    """Keep every scene reference in the same visual language as character designs."""
    base = _SCENE_REFERENCE_CONSTRAINT_PATTERN.sub("", prompt.strip()).rstrip("，。；; ")
    style = expand_art_style(art_style)
    ratio = aspect.strip() if re.fullmatch(r"\d{1,2}:\d{1,2}", aspect.strip()) else "9:16"
    constraint = (
        f"场景参考图硬性要求：画面风格必须与角色定妆保持一致，统一采用{style}；"
        "只表现环境，无人物、无动物；固定建筑结构、空间布局、主要家具、门窗位置、"
        f"标志物、色彩和基础光线；画面宽高比 {ratio}；无文字、无水印、无标志"
    )
    return f"{base}，{constraint}" if base else constraint


def build_character_prompt(
    name: str,
    description: str,
    art_style: str,
    base_character_name: str = "",
) -> str:
    if base_character_name.strip():
        details = description.strip() or "仅更换服装，保持人物本体完全一致"
        prompt = f"{name}，换装服饰要求：{details}，单一角色换装定妆照，正面全身，高细节，竖版 2:3 构图"
        return enforce_character_variant_prompt(prompt, base_character_name, art_style, details)
    details = description.strip() or "请根据人物身份设计鲜明、易辨认且可重复使用的外貌与服装"
    prompt = (
        f"{name}，{details}，单一角色定妆照，正面全身，清晰五官，固定发型与服装，"
        "角色设计稿，高细节，竖版 2:3 构图"
    )
    return enforce_character_reference_prompt(prompt, art_style)


def build_scene_prompt(name: str, description: str, art_style: str, aspect: str = "9:16") -> str:
    details = description.strip() or "请设计可在连续镜头中重复使用、空间关系明确且容易识别的环境"
    return enforce_scene_reference_prompt(
        f"{name}，{details}，漫画场景定景参考图，清晰环境全景，高细节",
        art_style,
        aspect,
    )


def build_storyboard_prompt(
    source_text: str,
    *,
    art_style: str,
    target_chars: int,
    existing_characters: Sequence[Mapping[str, object]],
    existing_scenes: Sequence[Mapping[str, object]] = (),
) -> tuple[str, str]:
    segments = numbered_story_segments(source_text, target_chars)
    return build_storyboard_batch_prompt(
        segments,
        art_style=art_style,
        existing_characters=existing_characters,
        existing_scenes=existing_scenes,
        generation_mode="all",
        batch_index=1,
        batch_total=1,
    )


def build_storyboard_batch_prompt(
    segments: Sequence[Mapping[str, object]],
    *,
    art_style: str,
    existing_characters: Sequence[Mapping[str, object]],
    generation_mode: str,
    batch_index: int,
    batch_total: int,
    existing_scenes: Sequence[Mapping[str, object]] = (),
) -> tuple[str, str]:
    art_style = expand_art_style(art_style)
    if generation_mode == "both":
        generation_mode = "all"
    if generation_mode not in {"characters", "scenes", "shots", "all"}:
        raise ValueError("不支持的漫画 AI 生成模式。")
    character_context = "\n".join(
        f"- {item.get('name', '')}：{item.get('description', '')}"
        for item in existing_characters
        if str(item.get("name", "")).strip()
    ) or "（暂无，请从正文提取主要角色）"
    if generation_mode == "shots" and not existing_characters:
        character_context = "（暂无已有角色；characters 与分镜 characters 均返回空数组）"
    scene_context = "\n".join(
        f"- {item.get('name', '')}：{item.get('description', '')}"
        for item in existing_scenes
        if str(item.get("name", "")).strip()
    ) or "（暂无，请从正文提取会在多个镜头复用的主要场景）"
    if generation_mode == "shots" and not existing_scenes:
        scene_context = "（暂无已有场景；scenes 返回空数组，分镜 scene 返回空字符串）"
    wants_characters = generation_mode in {"characters", "all"}
    wants_scenes = generation_mode in {"scenes", "all"}
    wants_shots = generation_mode in {"shots", "all"}
    task_text = {
        "characters": "只识别并完善角色设定，不生成场景或分镜",
        "scenes": "只识别并完善固定场景，不生成角色或分镜",
        "shots": "只生成分镜，不新增或改写角色库和场景库",
        "all": "同时识别角色、固定场景并生成分镜",
    }[generation_mode]
    system = (
        "你是中文漫画推文的角色设计师、场景美术师与静态漫画分镜导演。只输出一个合法 JSON 对象，不要输出 Markdown。"
        "不得省略、合并或重复输入片段，不得添加原文没有的关键事实。"
        "所有分镜最终都只生成一张静止图片，不生成视频动作或镜头运动。"
    )
    segment_text = "\n\n".join(
        f"[{str(item.get('segment_id', '')).strip()}]\n{str(item.get('source', '')).strip()}"
        for item in segments
        if str(item.get("segment_id", "")).strip() and str(item.get("source", "")).strip()
    )
    expected_ids = "、".join(str(item.get("segment_id", "")).strip() for item in segments)
    shot_rule = (
        f"本批共有 {len(segments)} 个已编号片段：{expected_ids}。shots 必须恰好返回 {len(segments)} 项，"
        "每项严格对应一个片段；segment_id 必须原样返回且不得重复；source 必须完整复制该片段原文。"
        if wants_shots
        else "shots 必须返回空数组。"
    )
    character_rule = (
        "characters 只返回本批出现的主要角色；外貌必须具体、稳定、可复用，同名角色不得改名。"
        "人物 prompt 只能描述角色本身与纯色背景，必须明确无场景、无建筑、无家具、无环境道具、无其他人物。"
        if wants_characters
        else "characters 必须返回空数组；分镜中的角色名只能从已有角色设定中选择。"
    )
    scene_rule = (
        "scenes 只返回本批新出现或需要补全的主要场景；每个场景必须有稳定名称，并明确固定空间布局、建筑、家具、标志物、色彩与基础光线；"
        "场景 description 与 prompt 必须采用上方指定的画面风格，与角色定妆保持完全一致；同名场景不得改名。"
        if wants_scenes
        else "scenes 必须返回空数组；分镜中的 scene 只能从已有场景设定中选择，无法确定时返回空字符串。"
    )
    user = f"""任务：{task_text}
当前批次：{batch_index}/{batch_total}
画面风格：{art_style}

已有角色设定（同名时保留并补充，不要改名）：
{character_context}

已有固定场景（同名时保留并补充，不要改名）：
{scene_context}

硬性规则：
1. {character_rule}
2. {scene_rule}
3. {shot_rule}
4. 每个分镜只绑定一个固定场景。scene 必须使用场景库中的精确名称。
5. 每项 prompt 只写“对应人物 + 一个表情或一个动作”，控制在 8～30 个汉字，例如“苏晚皱眉回头”或“林川握紧手机，神情紧张”。
6. prompt 不写人物外貌、服装、场景陈设、光线、画风、景别或构图；禁止推拉摇移、跟拍、环绕、变焦、转场、慢动作等动态描述；不带任何 -- 参数。
7. 分镜构图以中近景和近景为绝对主力；有人物时优先胸像、半身或腰部以上，让主要人物占画面高度约 55%～80%，面部和表情必须清楚。只有剧情核心就是环境且不这样拍无法讲清时才允许中景；不采用远景、大全景、超远景，也不要让人物在画面中过小。

请严格输出：
{{
  "characters": [{{"name": "姓名", "description": "年龄、五官、发型、服装、体态、标志物", "prompt": "仅人物、纯色背景、无任何场景的定妆图提示词"}}],
  "scenes": [{{"name": "固定场景名", "description": "空间布局、建筑、家具、门窗、标志物、色彩与基础光线", "prompt": "无人物的场景定景图提示词"}}],
  "shots": [{{"segment_id": "S00001", "title": "镜头标题", "source": "完整复制对应片段原文", "narration": "适合推文配音的简短旁白", "characters": ["角色名"], "scene": "固定场景名", "prompt": "人物的一个表情或动作短句"}}]
}}

本批已编号片段：
{segment_text}
"""
    return system, user


def build_ai_split_storyboard_prompt(
    source_text: str,
    *,
    art_style: str,
    existing_characters: Sequence[Mapping[str, object]],
    generation_mode: str,
    batch_index: int,
    batch_total: int,
    existing_scenes: Sequence[Mapping[str, object]] = (),
) -> tuple[str, str]:
    """Build a prompt where the model decides storyboard boundaries itself."""
    art_style = expand_art_style(art_style)
    if generation_mode == "both":
        generation_mode = "all"
    if generation_mode not in {"characters", "scenes", "shots", "all"}:
        raise ValueError("不支持的漫画 AI 生成模式。")
    character_context = "\n".join(
        f"- {item.get('name', '')}：{item.get('description', '')}"
        for item in existing_characters
        if str(item.get("name", "")).strip()
    ) or "（暂无，请从正文提取主要角色）"
    if generation_mode == "shots" and not existing_characters:
        character_context = "（暂无已有角色；characters 与分镜 characters 均返回空数组）"
    scene_context = "\n".join(
        f"- {item.get('name', '')}：{item.get('description', '')}"
        for item in existing_scenes
        if str(item.get("name", "")).strip()
    ) or "（暂无，请从正文提取会在多个镜头复用的主要场景）"
    if generation_mode == "shots" and not existing_scenes:
        scene_context = "（暂无已有场景；scenes 返回空数组，分镜 scene 返回空字符串）"
    wants_characters = generation_mode in {"characters", "all"}
    wants_scenes = generation_mode in {"scenes", "all"}
    wants_shots = generation_mode in {"shots", "all"}
    task_text = {
        "characters": "只识别并完善角色设定，不生成场景或分镜",
        "scenes": "只识别并完善固定场景，不生成角色或分镜",
        "shots": "由 AI 按剧情节奏拆分正文并生成分镜，不新增或改写角色库和场景库",
        "all": "识别角色和固定场景，并由 AI 按剧情节奏拆分正文、生成分镜",
    }[generation_mode]
    system = (
        "你是中文漫画推文的角色设计师、场景美术师与静态漫画分镜导演。只输出一个合法 JSON 对象，不要输出 Markdown。"
        "所有 shots 最终各自只生成一张静止图片，不生成角色动画、连续动作或镜头运动。"
        "分镜边界必须由你根据可独立绘制的静态画面变化判断，不按固定字数切分。"
    )
    shot_rule = (
        "由你决定 shots 数量。一个 shot 只表达一个能够被瞬间定格的关键画面、一个主要动作状态和一个明确情绪。"
        "只要场景、人物站位或姿态、说话人、受话人的反应、动作结果、情绪、时间或关键信息发生变化，就应新建静态分镜；"
        "不得把需要连续播放才能理解的动作过程压缩成一张图。每项 source 必须是输入正文中连续且未经改写的原文。"
        "所有 source 按返回顺序拼接后必须完整覆盖本批正文，不得漏字、改写、重叠、调换顺序或添加内容。"
        "segment_id 可留空，程序会在完整性校验后统一编号。"
        if wants_shots
        else "shots 必须返回空数组。"
    )
    character_rule = (
        "characters 只返回本批出现的主要角色；外貌必须具体、稳定、可复用，同名角色不得改名。"
        "人物 prompt 只能描述角色本身与纯色背景，必须明确无场景、无建筑、无家具、无环境道具、无其他人物。"
        if wants_characters
        else "characters 必须返回空数组；分镜中的角色名只能从已有角色设定中选择。"
    )
    scene_rule = (
        "scenes 只返回本批新出现或需要补全的主要场景；场景名称与空间布局必须稳定；"
        "场景 description 与 prompt 必须采用上方指定的画面风格，与角色定妆保持完全一致；同名场景不得改名。"
        if wants_scenes
        else "scenes 必须返回空数组；分镜中的 scene 只能从已有场景设定中选择，无法确定时返回空字符串。"
    )
    user = f"""任务：{task_text}
当前文本批次：{batch_index}/{batch_total}
画面风格：{art_style}

已有角色设定（同名时保留并补充，不要改名）：
{character_context}

已有固定场景（同名时保留并补充，不要改名）：
{scene_context}

硬性规则：
1. {character_rule}
2. {scene_rule}
3. {shot_rule}
4. 不按固定字数切分，也不要为了凑字数平均切分。按“单张静止图片能否完整讲清”判断边界；对白换人、人物反应、姿态变化、动作发生与动作结果通常应拆成不同静态分镜。
5. 每个分镜只绑定一个固定场景。scene 使用场景库中的精确名称。
6. 每项 prompt 只写“对应人物 + 一个表情或一个动作”，控制在 8～30 个汉字，例如“苏晚脸颊微红，避开视线”或“林川惊讶地举起手机”；不要概括整段剧情。
7. prompt 不写人物外貌、服装、场景陈设、光线、画风、景别或构图；禁止推拉摇移、跟拍、环绕、变焦、转场、慢动作等动态描述；不带任何 -- 参数。
8. 分镜构图以中近景和近景为绝对主力；有人物时优先胸像、半身或腰部以上，让主要人物占画面高度约 55%～80%，面部和表情必须清楚。只有剧情核心就是环境且不这样拍无法讲清时才允许中景；不采用远景、大全景、超远景，也不要让人物在画面中过小。

请严格输出：
{{
  "characters": [{{"name": "姓名", "description": "年龄、五官、发型、服装、体态、标志物", "prompt": "仅人物、纯色背景、无任何场景的定妆图提示词"}}],
  "scenes": [{{"name": "固定场景名", "description": "空间布局、建筑、家具、门窗、标志物、色彩与基础光线", "prompt": "无人物的场景定景图提示词"}}],
  "shots": [{{"segment_id": "", "title": "镜头标题", "source": "未经改写的连续原文", "narration": "适合推文配音的简短旁白", "characters": ["角色名"], "scene": "固定场景名", "prompt": "人物的一个表情或动作短句"}}]
}}

本批待分析正文：
{source_text.strip()}
"""
    return system, user


def _extract_json_object(value: str) -> object:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", value.strip(), flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def parse_storyboard_response(
    value: str, *, art_style: str, generation_mode: str = "all"
) -> dict[str, list[dict[str, object]]]:
    if generation_mode == "both":
        generation_mode = "all"
    try:
        payload = _extract_json_object(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ComicEngineError("文本模型没有返回可用的分镜 JSON，请重试或使用本地拆分。") from exc
    if not isinstance(payload, Mapping):
        raise ComicEngineError("分镜结果不是 JSON 对象。")

    characters: list[dict[str, object]] = []
    for index, item in enumerate(payload.get("characters", [])):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "")).strip() or f"角色{index + 1}"
        description = str(item.get("description", "")).strip()
        prompt = enforce_character_reference_prompt(
            str(item.get("prompt", "")).strip() or build_character_prompt(name, description, art_style),
            art_style,
        )
        record = default_character(name)
        record.update({"description": description, "prompt": prompt})
        characters.append(record)

    scenes: list[dict[str, object]] = []
    for index, item in enumerate(payload.get("scenes", [])):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "")).strip() or f"场景{index + 1}"
        description = str(item.get("description", "")).strip()
        prompt = enforce_scene_reference_prompt(
            str(item.get("prompt", "")).strip() or build_scene_prompt(name, description, art_style),
            expand_art_style(art_style),
        )
        record = default_scene(name)
        record.update({"description": description, "prompt": prompt})
        scenes.append(record)

    shots: list[dict[str, object]] = []
    for index, item in enumerate(payload.get("shots", [])):
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("source", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if not source and not prompt:
            continue
        if prompt and _DYNAMIC_CAMERA_PATTERN.search(prompt):
            raise ComicEngineError("AI 返回了动态运镜描述，已拒绝该批结果并将按静态漫规则重试。")
        names = item.get("characters", [])
        if not isinstance(names, list):
            names = []
        shots.append(
            {
                "index": index + 1,
                "segment_id": str(item.get("segment_id", "")).strip(),
                "title": str(item.get("title", "")).strip() or f"分镜 {index + 1:02d}",
                "source": source,
                "narration": str(item.get("narration", "")).strip(),
                "characters": [str(name).strip() for name in names if str(name).strip()],
                "scene": str(item.get("scene", "")).strip(),
                "prompt": prompt or str(item.get("narration", "")).strip() or source[:60],
                "task_id": "",
                "status": "待生成",
                "progress": "0%",
                "image_url": "",
                "local_path": "",
                "error": "",
            }
        )
    if generation_mode in {"shots", "all"} and not shots:
        raise ComicEngineError("文本模型没有返回任何可用分镜。")
    return {"characters": characters, "scenes": scenes, "shots": shots}


def validate_storyboard_batch(
    shots: Sequence[Mapping[str, object]], expected_segments: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    expected_ids = [str(item.get("segment_id", "")).strip() for item in expected_segments]
    expected_ids = [item for item in expected_ids if item]
    by_id: dict[str, dict[str, object]] = {}
    duplicates: list[str] = []
    unexpected: list[str] = []
    expected_sources = {
        str(item.get("segment_id", "")).strip(): str(item.get("source", "")).strip()
        for item in expected_segments
    }
    positional_fallback = len(shots) == len(expected_ids) and all(not str(shot.get("segment_id", "")).strip() for shot in shots)
    for position, shot in enumerate(shots):
        segment_id = expected_ids[position] if positional_fallback else str(shot.get("segment_id", "")).strip()
        short_id = re.fullmatch(r"[sS]0*(\d+)", segment_id)
        if short_id:
            normalized_id = f"S{int(short_id.group(1)):05d}"
            if normalized_id in expected_ids:
                segment_id = normalized_id
        if segment_id not in expected_ids:
            unexpected.append(segment_id or "（空编号）")
            continue
        if segment_id in by_id:
            duplicates.append(segment_id)
            continue
        record = dict(shot)
        record["segment_id"] = segment_id
        record["source"] = expected_sources.get(segment_id, "")
        by_id[segment_id] = record
    missing = [item for item in expected_ids if item not in by_id]
    if missing or duplicates or unexpected or len(shots) != len(expected_ids):
        details: list[str] = []
        if missing:
            details.append("缺少 " + "、".join(missing))
        if duplicates:
            details.append("重复 " + "、".join(duplicates))
        if unexpected:
            details.append("异常编号 " + "、".join(unexpected))
        raise ComicEngineError("AI 分镜片段校验失败：" + "；".join(details or ["返回数量不一致"]))
    return [by_id[item] for item in expected_ids]


def validate_ai_storyboard_split(
    shots: Sequence[Mapping[str, object]], source_text: str, *, start_index: int = 1
) -> list[dict[str, object]]:
    """Verify an AI-selected split covers the source once, then assign stable IDs."""
    expected = re.sub(r"\s+", "", source_text)
    if not expected:
        return []
    if not shots:
        raise ComicEngineError("AI 没有为本批正文生成分镜。")
    sources = [str(item.get("source", "")).strip() for item in shots]
    if any(not item for item in sources):
        raise ComicEngineError("AI 拆分结果包含没有原文的空分镜。")
    actual = "".join(re.sub(r"\s+", "", item) for item in sources)
    if actual != expected:
        common = 0
        for expected_char, actual_char in zip(expected, actual):
            if expected_char != actual_char:
                break
            common += 1
        raise ComicEngineError(
            f"AI 拆分未完整覆盖本批原文（原文 {len(expected)} 字，返回 {len(actual)} 字，约在第 {common + 1} 字出现差异）。"
        )
    result: list[dict[str, object]] = []
    for offset, shot in enumerate(shots):
        index = start_index + offset
        record = dict(shot)
        record["index"] = index
        record["segment_id"] = f"S{index:05d}"
        record["source"] = sources[offset]
        result.append(record)
    return result


def fallback_storyboard(
    source_text: str,
    *,
    art_style: str,
    target_chars: int,
    characters: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    names = [str(item.get("name", "")).strip() for item in characters if str(item.get("name", "")).strip()]
    shots: list[dict[str, object]] = []
    for index, segment in enumerate(split_story_segments(source_text, target_chars), start=1):
        mentioned = [name for name in names if name in segment]
        shots.append(
            {
                "index": index,
                "segment_id": f"S{index:05d}",
                "title": f"分镜 {index:02d}",
                "source": segment,
                "narration": segment,
                "characters": mentioned or names[:1],
                "scene": "",
                "prompt": f"{(mentioned or names[:1] or ['人物'])[0]}：{segment[:48]}",
                "task_id": "",
                "status": "待生成",
                "progress": "0%",
                "image_url": "",
                "local_path": "",
                "error": "",
            }
        )
    return shots


def compose_shot_prompt(
    shot: Mapping[str, object],
    *,
    art_style: str,
    aspect: str,
    characters: Sequence[Mapping[str, object]],
    scenes: Sequence[Mapping[str, object]] = (),
) -> str:
    selected_names = {str(name) for name in shot.get("characters", []) if str(name).strip()}
    selected = [item for item in characters if str(item.get("name", "")) in selected_names]
    selected_character_names = [str(item.get("name", "")).strip() for item in selected if str(item.get("name", "")).strip()]
    visual = str(shot.get("prompt") or shot.get("narration") or shot.get("source") or "").strip()
    pieces = [
        "镜头景别固定以中近景或近景为主，人物采用胸像、半身或腰部以上构图，主要人物占画面高度约 55%～80%，面部表情清晰",
        visual,
    ]
    scene_name = str(shot.get("scene", "")).strip()
    selected_scene = next((item for item in scenes if str(item.get("name", "")).strip() == scene_name), None)
    if selected_scene:
        pieces.append(f"场景使用“{scene_name}”参考图")
    if selected_character_names:
        pieces.append(f"人物使用{'、'.join(selected_character_names)}参考图")
    pieces.extend(
        [
            expand_art_style(art_style),
            "静态漫画单幅画面，只表现这一刻",
            "只有剧情核心就是环境且不使用中景无法讲清时才允许中景；不使用远景、大全景、超远景，不让人物在画面中过小",
            "无文字，无对白气泡，无水印",
        ]
    )
    ratio = aspect.strip() if re.fullmatch(r"\d{1,2}:\d{1,2}", aspect.strip()) else "9:16"
    pieces.append(f"画面宽高比为 {ratio}")
    return "，".join(piece for piece in pieces if piece)


def replace_character_in_shots(shots: Sequence[dict[str, object]], source_name: str, target_name: str = "") -> int:
    """Replace or remove one character binding while preserving list order."""
    source_name = source_name.strip()
    target_name = target_name.strip()
    if not source_name or source_name == target_name:
        return 0
    changed = 0
    for shot in shots:
        names = [str(name).strip() for name in shot.get("characters", []) if str(name).strip()]
        if source_name not in names:
            continue
        replaced = [target_name if name == source_name else name for name in names]
        deduplicated: list[str] = []
        for name in replaced:
            if name and name not in deduplicated:
                deduplicated.append(name)
        shot["characters"] = deduplicated
        changed += 1
    return changed


def replace_scene_in_shots(shots: Sequence[dict[str, object]], source_name: str, target_name: str = "") -> int:
    """Replace or clear one fixed-scene binding."""
    source_name = source_name.strip()
    target_name = target_name.strip()
    if not source_name or source_name == target_name:
        return 0
    changed = 0
    for shot in shots:
        if str(shot.get("scene", "")).strip() != source_name:
            continue
        shot["scene"] = target_name
        changed += 1
    return changed


def _reset_storyboard_shot_generation(shot: dict[str, object]) -> dict[str, object]:
    shot.update(
        {
            "task_id": "",
            "status": "待重新生成",
            "progress": "0%",
            "image_url": "",
            "local_path": "",
            "error": "",
            "final_prompt": "",
        }
    )
    return shot


def merge_storyboard_shots(first: Mapping[str, object], second: Mapping[str, object]) -> dict[str, object]:
    """Merge two adjacent editable storyboard records without losing source order."""
    merged = dict(first)
    first_title = str(first.get("title", "")).strip()
    second_title = str(second.get("title", "")).strip()
    merged["title"] = " + ".join(item for item in (first_title, second_title) if item)[:120] or "合并分镜"
    merged["source"] = str(first.get("source", "")).rstrip() + str(second.get("source", "")).lstrip()
    merged["narration"] = "\n".join(
        item for item in (str(first.get("narration", "")).strip(), str(second.get("narration", "")).strip()) if item
    )
    names: list[str] = []
    for item in list(first.get("characters", [])) + list(second.get("characters", [])):
        name = str(item).strip()
        if name and name not in names:
            names.append(name)
    merged["characters"] = names
    first_scene = str(first.get("scene", "")).strip()
    second_scene = str(second.get("scene", "")).strip()
    merged["scene"] = first_scene if first_scene == second_scene else ""
    first_prompt = str(first.get("prompt", "")).strip()
    second_prompt = str(second.get("prompt", "")).strip()
    merged["prompt"] = first_prompt if first_prompt == second_prompt else (first_prompt or second_prompt)
    return _reset_storyboard_shot_generation(merged)


def split_storyboard_shot(shot: Mapping[str, object], offset: int) -> tuple[dict[str, object], dict[str, object]]:
    """Split one storyboard record at an exact source-text character offset."""
    source = str(shot.get("source", ""))
    if not 0 < offset < len(source):
        raise ValueError("拆分位置必须位于分镜原文中间。")
    first_source = source[:offset].strip()
    second_source = source[offset:].strip()
    if not first_source or not second_source:
        raise ValueError("拆分后的两个镜头都必须包含原文。")

    first = dict(shot)
    second = dict(shot)
    title = str(shot.get("title", "")).strip() or "分镜"
    first["title"] = f"{title}（上）"
    second["title"] = f"{title}（下）"
    first["source"] = first_source
    second["source"] = second_source
    first["characters"] = [str(item) for item in shot.get("characters", [])]
    second["characters"] = [str(item) for item in shot.get("characters", [])]

    narration = str(shot.get("narration", ""))
    if narration:
        narration_offset = round(len(narration) * len(first_source) / max(len(first_source) + len(second_source), 1))
        narration_offset = min(max(narration_offset, 1), len(narration) - 1) if len(narration) > 1 else 0
        first["narration"] = narration[:narration_offset].strip() if narration_offset else narration
        second["narration"] = narration[narration_offset:].strip() if narration_offset else ""
    return _reset_storyboard_shot_generation(first), _reset_storyboard_shot_generation(second)


def character_reference_data(characters: Iterable[Mapping[str, object]]) -> list[str]:
    references: list[str] = []
    for item in characters:
        path = str(item.get("local_path", "")).strip()
        if path and Path(path).is_file():
            references.append(image_data_url(path))
        else:
            image_url = str(item.get("image_url", "")).strip()
            if image_url.startswith(("http://", "https://", "data:image/")):
                references.append(image_url)
        if len(references) >= 10:
            break
    return references


def scene_reference_data(scene: Mapping[str, object] | None) -> list[str]:
    if not scene:
        return []
    path = str(scene.get("local_path", "")).strip()
    if path and Path(path).is_file():
        return [image_data_url(path)]
    image_url = str(scene.get("image_url", "")).strip()
    if image_url.startswith(("http://", "https://", "data:image/")):
        return [image_url]
    return []
