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
COMIC_COVER_OUTPUT_PLAN = (("3:4", 1), ("3:4", 2), ("4:3", 1), ("4:3", 2))


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
        raise ComicEngineError(f"æ— æ³•å¯¼å‡ºæ¼«ç”»èµ„äº§åŒ…ï¼š{exc}") from exc
    return {
        "characters": len(manifest["characters"]),
        "scenes": len(manifest["scenes"]),
        "references": reference_count,
    }


def _validated_pack_records(payload: object, kind: str) -> list[dict[str, str]]:
    if not isinstance(payload, list):
        raise ComicEngineError(f"èµ„äº§åŒ…ä¸­çš„ {kind} ä¸æ˜¯æ•°ç»„ã€‚")
    if len(payload) > 500:
        raise ComicEngineError(f"èµ„äº§åŒ…ä¸­çš„ {kind} æ•°é‡è¶…è¿‡ 500ï¼Œå·²æ‹’ç»å¯¼å…¥ã€‚")
    records: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise ComicEngineError(f"èµ„äº§åŒ…ä¸­çš„ {kind} è®°å½•æ ¼å¼æ— æ•ˆã€‚")
        name = str(item.get("name", "")).strip()[:120]
        if not name:
            raise ComicEngineError(f"èµ„äº§åŒ…ä¸­çš„ {kind} å­˜åœ¨ç©ºåç§°ã€‚")
        reference = str(item.get("reference", "")).replace("\\", "/").strip()
        if reference:
            portable = PurePosixPath(reference)
            if portable.is_absolute() or ".." in portable.parts or not portable.parts or portable.parts[0] != kind:
                raise ComicEngineError("èµ„äº§åŒ…åŒ…å«ä¸å®‰å…¨çš„å‚è€ƒå›¾è·¯å¾„ï¼Œå·²æ‹’ç»å¯¼å…¥ã€‚")
            if portable.suffix.lower() not in COMIC_ASSET_IMAGE_SUFFIXES:
                raise ComicEngineError("èµ„äº§åŒ…åŒ…å«ä¸æ”¯æŒçš„å‚è€ƒå›¾æ ¼å¼ã€‚")
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
    raise ComicEngineError(f"æ— æ³•ä¸ºâ€œ{name}â€åˆ†é…æœ¬åœ°å‚è€ƒå›¾æ–‡ä»¶åã€‚")


def import_comic_asset_pack(source: str | Path, destination_root: str | Path) -> dict[str, object]:
    package = Path(source)
    destination = Path(destination_root)
    try:
        with zipfile.ZipFile(package, "r") as archive:
            try:
                manifest_info = archive.getinfo("manifest.json")
            except KeyError as exc:
                raise ComicEngineError("æ‰€é€‰æ–‡ä»¶ä¸æ˜¯æœ‰æ•ˆçš„æ¼«ç”»èµ„äº§åŒ…ï¼šç¼ºå°‘ manifest.jsonã€‚") from exc
            if manifest_info.file_size > 2 * 1024 * 1024:
                raise ComicEngineError("æ¼«ç”»èµ„äº§åŒ…æ¸…å•è¿‡å¤§ï¼Œå·²æ‹’ç»å¯¼å…¥ã€‚")
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            if not isinstance(manifest, Mapping) or manifest.get("format") != COMIC_ASSET_PACK_FORMAT:
                raise ComicEngineError("æ‰€é€‰æ–‡ä»¶ä¸æ˜¯æ¼«ç”»æŽ¨æ–‡æ¼«ç”»èµ„äº§åŒ…ã€‚")
            if int(manifest.get("version", 0)) != COMIC_ASSET_PACK_VERSION:
                raise ComicEngineError("æ¼«ç”»èµ„äº§åŒ…ç‰ˆæœ¬ä¸å—æ”¯æŒã€‚")
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
                        raise ComicEngineError(f"èµ„äº§åŒ…ç¼ºå°‘å‚è€ƒå›¾ï¼š{reference}") from exc
                    if info.file_size > 100 * 1024 * 1024:
                        raise ComicEngineError(f"å‚è€ƒå›¾è¿‡å¤§ï¼Œå·²æ‹’ç»å¯¼å…¥ï¼š{reference}")
                    total_size += info.file_size
                    if total_size > 1024 * 1024 * 1024:
                        raise ComicEngineError("èµ„äº§åŒ…è§£åŽ‹åŽçš„å‚è€ƒå›¾æ€»å¤§å°è¶…è¿‡ 1GBï¼Œå·²æ‹’ç»å¯¼å…¥ã€‚")
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
        raise ComicEngineError(f"æ— æ³•å¯¼å…¥æ¼«ç”»èµ„äº§åŒ…ï¼š{exc}") from exc

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
                record.update({"local_path": local_path, "status": "å®šå¦†å·²ç¡®è®¤" if factory is default_character else "å®šæ™¯å·²ç¡®è®¤"})
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
        paragraphs = [part.strip() for part in re.split(r"(?<=[ã€‚ï¼ï¼Ÿ!?])", normalized) if part.strip()]
    segments: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = [paragraph]
        if len(paragraph) > target * 2:
            pieces = [part.strip() for part in re.split(r"(?<=[ã€‚ï¼ï¼Ÿ!?ï¼›;])", paragraph) if part.strip()]
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

    units = [part for part in re.split(r"(?<=[ã€‚ï¼ï¼Ÿ!?ï¼›;])|\n+", normalized) if part and part.strip()]
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


def default_character(name: str = "æ–°è§’è‰²") -> dict[str, object]:
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
        "status": "æœªç”Ÿæˆ",
    }


def default_scene(name: str = "æ–°åœºæ™¯") -> dict[str, object]:
    return {
        "name": name,
        "description": "",
        "prompt": "",
        "task_id": "",
        "image_url": "",
        "local_path": "",
        "candidate_path": "",
        "candidate_image_url": "",
        "status": "æœªç”Ÿæˆ",
    }


KOREAN_WEBTOON_STYLE_CONSTRAINT = (
    "æ˜Žç¡®é‡‡ç”¨äºŒç»´éŸ©ç³»ç½‘ç»œæ¼«ç”»ï¼ˆKorean webtoonï¼‰æ’ç”»è¡¨çŽ°ï¼Œæ¸…æ™°ç²¾è‡´çº¿ç¨¿ï¼Œå¹³æ»‘èµ›ç’ç’ä¸ŽæŸ”å’Œæ¸å˜ä¸Šè‰²ï¼Œ"
    "é«˜é¢œå€¼ä½†ä¿æŒæ¼«ç”»åŒ–äº”å®˜ï¼Œä¿®é•¿è‡ªç„¶äººä½“æ¯”ä¾‹ï¼Œå¹²å‡€çš„æ¼«ç”»è‚¤è‰²å—å’Œå±‚æ¬¡åˆ†æ˜Žçš„æ¼«ç”»é˜´å½±ï¼Œç«–å±æ¡æ¼«è´¨æ„Ÿï¼›"
    "ç¦æ­¢çœŸäººç…§ç‰‡ã€å†™å®žæ‘„å½±ã€å½±è§†å‰§ç…§ã€çœŸäººçš®è‚¤æ¯›å­”ã€ç…§ç‰‡çº§çš®è‚¤çº¹ç†ã€3Däººç‰©æ¸²æŸ“å’Œè¶…å†™å®žé£Žæ ¼"
)


def expand_art_style(art_style: str) -> str:
    """Turn a short style preset into a concrete imagëÞ¸¶‰žËkºwµçj–*£š¢þC¦Vsš>?¢þÃ¾ò3–ÞËš.Kžîw¢¾—š&çžîOšzs–æÛ–Âš2'¦vgššò¯¢ž–"g¦7¢¾WŽˆ¤(€€€€€€€¹…µ•Ì€ô¥Ñ•´¹•Ð ‰¡…É…Ñ•ÉÌˆ°mt¤(€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡¹…µ•Ì°±¥ÍÐ¤è(€€€€€€€€€€€¹…µ•Ì€ômt(€€€€€€€Í¡½ÑÌ¹…ÁÁ•¹ (€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰¥¹‘•àˆè¥¹‘•à€¬€Ä°(€€€€€€€€€€€€€€€€‰Í•µ•¹Ñ}¥ˆèÍÑÈ¡¥Ñ•´¹•Ð ‰Í•µ•¹Ñ}¥ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤°(€€€€€€€€€€€€€€€€‰Ñ¥Ñ±”ˆèÍÑÈ¡¥Ñ•´¹•Ð ‰Ñ¥Ñ±”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤½È˜‹–"¦Vpí¥¹‘•à€¬€ÄèÀÉ‘ôˆ°(€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆèÍ½ÕÉ”°(€€€€€€€€€€€€€€€€‰¹…ÉÉ…Ñ¥½¸ˆèÍÑÈ¡¥Ñ•´¹•Ð ‰¹…ÉÉ…Ñ¥½¸ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤°(€€€€€€€€€€€€€€€€‰¡…É…Ñ•ÉÌˆèmÍÑÈ¡¹…µ”¤¹ÍÑÉ¥À ¤™½È¹…µ”¥¸¹…µ•Ì¥˜ÍÑÈ¡¹…µ”¤¹ÍÑÉ¥À ¥t°(€€€€€€€€€€€€€€€€‰Í•¹”ˆèÍÑÈ¡¥Ñ•´¹•Ð ‰Í•¹”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤°(€€€€€€€€€€€€€€€€‰ÁÉ½µÁÐˆèÁÉ½µÁÐ½ÈÍÑÈ¡¥Ñ•´¹•Ð ‰¹…ÉÉ…Ñ¥½¸ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤½ÈÍ½ÕÉ•lèØÁt°(€€€€€€€€€€€€€€€€‰Ñ…Í­}¥ˆè€ˆˆ°(€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‹–úžRš"@ˆ°(€€€€€€€€€€€€€€€€‰ÁÉ½É•ÍÌˆè€ˆÀ”ˆ°(€€€€€€€€€€€€€€€€‰¥µ…•}ÕÉ°ˆè€ˆˆ°(€€€€€€€€€€€€€€€€‰±½…±}Á…Ñ ˆè€ˆˆ°(€€€€€€€€€€€€€€€€‰•ÉÉ½Èˆè€ˆˆ°(€€€€€€€€€€€ô(€€€€€€€€¤(€€€¥˜•¹•É…Ñ¥½¹}µ½‘”¥¸ì‰Í¡½ÑÌˆ°€‰…±°‰ô…¹¹½ÐÍ¡½ÑÌè(€€€€€€€É…¥Í”½µ¥¹¥¹•ÉÉ½È ‹šZšr³š¢‡–z/šÊ‡šr'¢þS–n{’îï’öW–>¿žR£–"¦VsŽˆ¤(€€€É•ÑÕÉ¸ì‰¡…É…Ñ•ÉÌˆè¡…É…Ñ•ÉÌ°€‰Í•¹•ÌˆèÍ•¹•Ì°€‰Í¡½ÑÌˆèÍ¡½ÑÍô(()‘•˜Ù…±¥‘…Ñ•}ÍÑ½Éå‰½…É‘}‰…Ñ  (€€€Í¡½ÑÌèM•ÅÕ•¹•m5…ÁÁ¥¹mÍÑÈ°½‰©•Ñut°•áÁ•Ñ•‘}Í•µ•¹ÑÌèM•ÅÕ•¹•m5…ÁÁ¥¹mÍÑÈ°½‰©•Ñut(¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñutè(€€€•áÁ•Ñ•‘}¥‘Ì€ômÍÑÈ¡¥Ñ•´¹•Ð ‰Í•µ•¹Ñ}¥ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤™½È¥Ñ•´¥¸•áÁ•Ñ•‘}Í•µ•¹ÑÍt(€€€•áÁ•Ñ•‘}¥‘Ì€ôm¥Ñ•´™½È¥Ñ•´¥¸•áÁ•Ñ•‘}¥‘Ì¥˜¥Ñ•µt(€€€‰å}¥è‘¥ÑmÍÑÈ°‘¥ÑmÍÑÈ°½‰©•Ñut€ôíô(€€€‘ÕÁ±¥…Ñ•Ìè±¥ÍÑmÍÑÉt€ômt(€€€Õ¹•áÁ•Ñ•è±¥ÍÑmÍÑÉt€ômt(€€€•áÁ•Ñ•‘}Í½ÕÉ•Ì€ôì(€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ð ‰Í•µ•¹Ñ}¥ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤èÍÑÈ¡¥Ñ•´¹•Ð ‰Í½ÕÉ”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€€€€€™½È¥Ñ•´¥¸•áÁ•Ñ•‘}Í•µ•¹ÑÌ(€€€ô(€€€Á½Í¥Ñ¥½¹…±}™…±±‰…¬€ô±•¸¡Í¡½ÑÌ¤€ôô±•¸¡•áÁ•Ñ•‘}¥‘Ì¤…¹…±°¡¹½ÐÍÑÈ¡Í¡½Ð¹•Ð ‰Í•µ•¹Ñ}¥ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤™½ÈÍ¡½Ð¥¸Í¡½ÑÌ¤(€€€™½ÈÁ½Í¥Ñ¥½¸°Í¡½Ð¥¸•¹Õµ•É…Ñ”¡Í¡½ÑÌ¤è(€€€€€€€Í•µ•¹Ñ}¥€ô•áÁ•Ñ•‘}¥‘ÍmÁ½Í¥Ñ¥½¹t¥˜Á½Í¥Ñ¥½¹…±}™…±±‰…¬•±Í”ÍÑÈ¡Í¡½Ð¹•Ð ‰Í•µ•¹Ñ}¥ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€€€€€Í¡½ÉÑ}¥€ôÉ”¹™Õ±±µ…Ñ ¡È‰mÍMtÀ¨¡q¬¤ˆ°Í•µ•¹Ñ}¥¤(€€€€€€€¥˜Í¡½ÉÑ}¥è(€€€€€€€€€€€¹½Éµ…±¥é•‘}¥€ô˜‰Mí¥¹Ð¡Í¡½ÉÑ}¥¹É½ÕÀ Ä¤¤èÀÕ‘ôˆ(€€€€€€€€€€€¥˜¹½Éµ…±¥é•‘}¥¥¸•áÁ•Ñ•‘}¥‘Ìè(€€€€€€€€€€€€€€€Í•µ•¹Ñ}¥€ô¹½Éµ…±¥é•‘}¥(€€€€€€€¥˜Í•µ•¹Ñ}¥¹½Ð¥¸•áÁ•Ñ•‘}¥‘Ìè(€€€€€€€€€€€Õ¹•áÁ•Ñ•¹…ÁÁ•¹¡Í•µ•¹Ñ}¥½È€‹¾ò#ž¦ëžò[–>ß¾ò$ˆ¤(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€¥˜Í•µ•¹Ñ}¥¥¸‰å}¥è(€€€€€€€€€€€‘ÕÁ±¥…Ñ•Ì¹…ÁÁ•¹¡Í•µ•¹Ñ}¥¤(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€É•½É€ô‘¥Ð¡Í¡½Ð¤(€€€€€€€É•½É‘l‰Í•µ•¹Ñ}¥‰t€ôÍ•µ•¹Ñ}¥(€€€€€€€É•½É‘l‰Í½ÕÉ”‰t€ô•áÁ•Ñ•‘}Í½ÕÉ•Ì¹•Ð¡Í•µ•¹Ñ}¥°€ˆˆ¤(€€€€€€€‰å}¥‘mÍ•µ•¹Ñ}¥‘t€ôÉ•½É(€€€µ¥ÍÍ¥¹œ€ôm¥Ñ•´™½È¥Ñ•´¥¸•áÁ•Ñ•‘}¥‘Ì¥˜¥Ñ•´¹½Ð¥¸‰å}¥‘t(€€€¥˜µ¥ÍÍ¥¹œ½È‘ÕÁ±¥…Ñ•Ì½ÈÕ¹•áÁ•Ñ•½È±•¸¡Í¡½ÑÌ¤€„ô±•¸¡•áÁ•Ñ•‘}¥‘Ì¤è(€€€€€€€‘•Ñ…¥±Ìè±¥ÍÑmÍÑÉt€ômt(€€€€€€€¥˜µ¥ÍÍ¥¹œè(€€€€€€€€€€€‘•Ñ…¥±Ì¹…ÁÁ•¹ ‹žòë–ÂD€ˆ€¬€‹Žˆ¹©½¥¸¡µ¥ÍÍ¥¹œ¤¤(€€€€€€€¥˜‘ÕÁ±¥…Ñ•Ìè(€€€€€€€€€€€‘•Ñ…¥±Ì¹…ÁÁ•¹ ‹¦7–’4€ˆ€¬€‹Žˆ¹©½¥¸¡‘ÕÁ±¥…Ñ•Ì¤¤(€€€€€€€¥˜Õ¹•áÁ•Ñ•è(€€€€€€€€€€€‘•Ñ…¥±Ì¹…ÁÁ•¹ ‹–ò–âãžò[–>Ü€ˆ€¬€‹Žˆ¹©½¥¸¡Õ¹•áÁ•Ñ•¤¤(€€€€€€€É…¥Í”½µ¥¹¥¹•ÉÉ½È ‰$ƒ–"¦Vsž&šº×š‚‡¦ª3–’Ç¢Ò—¾òhˆ€¬€‹¾òlˆ¹©½¥¸¡‘•Ñ…¥±Ì½Èl‹¢þS–n{šVÃ¦?’â7’â¢Ð‰t¤¤(€€€É•ÑÕÉ¸m‰å}¥‘m¥Ñ•µt™½È¥Ñ•´¥¸•áÁ•Ñ•‘}¥‘Ít(()‘•˜Ù…±¥‘…Ñ•}…¥}ÍÑ½Éå‰½…É‘}ÍÁ±¥Ð (€€€Í¡½ÑÌèM•ÅÕ•¹•m5…ÁÁ¥¹mÍÑÈ°½‰©•Ñut°Í½ÕÉ•}Ñ•áÐèÍÑÈ°€¨°ÍÑ…ÉÑ}¥¹‘•àè¥¹Ð€ô€Ä(¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñutè(€€€€ˆˆ‰Y•É¥™ä…¸$µÍ•±•Ñ•ÍÁ±¥Ð½Ù•ÉÌÑ¡”Í½ÕÉ”½¹”°Ñ¡•¸…ÍÍ¥¸ÍÑ…‰±”%Ì¸ˆˆˆ(€€€•áÁ•Ñ•€ôÉ”¹ÍÕˆ¡È‰qÌ¬ˆ°€ˆˆ°Í½ÕÉ•}Ñ•áÐ¤(€€€¥˜¹½Ð•áÁ•Ñ•è(€€€€€€€É•ÑÕÉ¸mt(€€€¥˜¹½ÐÍ¡½ÑÌè(€€€€€€€É…¥Í”½µ¥¹¥¹•ÉÉ½È ‰$ƒšÊ‡šr'’âëšr³š&çš¶šZžRš"C–"¦VsŽˆ¤(€€€Í½ÕÉ•Ì€ômÍÑÈ¡¥Ñ•´¹•Ð ‰Í½ÕÉ”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤™½È¥Ñ•´¥¸Í¡½ÑÍt(€€€¥˜…¹ä¡¹½Ð¥Ñ•´™½È¥Ñ•´¥¸Í½ÕÉ•Ì¤è(€€€€€€€É…¥Í”½µ¥¹¥¹•ÉÉ½È ‰$ƒš.–"žîOšzs–2–B¯šÊ‡šr'–:šZžjž¦ë–"¦VsŽˆ¤(€€€…ÑÕ…°€ô€ˆˆ¹©½¥¸¡É”¹ÍÕˆ¡È‰qÌ¬ˆ°€ˆˆ°¥Ñ•´¤™½È¥Ñ•´¥¸Í½ÕÉ•Ì¤(€€€¥˜…ÑÕ…°€„ô•áÁ•Ñ•è(€€€€€€€½µµ½¸€ô€À(€€€€€€€™½È•áÁ•Ñ•‘}¡…È°…ÑÕ…±}¡…È¥¸é¥À¡•áÁ•Ñ•°…ÑÕ…°¤è(€€€€€€€€€€€¥˜•áÁ•Ñ•‘}¡…È€„ô…ÑÕ…±}¡…Èè(€€€€€€€€€€€€€€€‰É•…¬(€€€€€€€€€€€½µµ½¸€¬ô€Ä(€€€€€€€É…¥Í”½µ¥¹¥¹•ÉÉ½È (€€€€€€€€€€€˜‰$ƒš.–"šr«–º3šVÓ¢šžn[šr³š&ç–:šZ¾ò#–:šZí±•¸¡•áÁ•Ñ•¥ôƒ–¶_¾ò3¢þS–nxí±•¸¡…ÑÕ…°¥ôƒ–¶_¾ò3žê›–r£ž²°í½µµ½¸€¬€Åôƒ–¶_–ëž:Ã–Þ»–ò¾ò'Žˆ(€€€€€€€€¤(€€€É•ÍÕ±Ðè±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñut€ômt(€€€™½È½™™Í•Ð°Í¡½Ð¥¸•¹Õµ•É…Ñ”¡Í¡½ÑÌ¤è(€€€€€€€¥¹‘•à€ôÍÑ…ÉÑ}¥¹‘•à€¬½™™Í•Ð(€€€€€€€É•½É€ô‘¥Ð¡Í¡½Ð¤(€€€€€€€É•½É‘l‰¥¹‘•à‰t€ô¥¹‘•à(€€€€€€€É•½É‘l‰Í•µ•¹Ñ}¥‰t€ô˜‰Mí¥¹‘•àèÀÕ‘ôˆ(€€€€€€€É•½É‘l‰Í½ÕÉ”‰t€ôÍ½ÕÉ•Ím½™™Í•Ñt(€€€€€€€É•ÍÕ±Ð¹…ÁÁ•¹¡É•½É¤(€€€É•ÑÕÉ¸É•ÍÕ±Ð(()‘•˜™…±±‰…­}ÍÑ½Éå‰½…É (€€€Í½ÕÉ•}Ñ•áÐèÍÑÈ°(€€€€¨°(€€€…ÉÑ}ÍÑå±”èÍÑÈ°(€€€Ñ…É•Ñ}¡…ÉÌè¥¹Ð°(€€€¡…É…Ñ•ÉÌèM•ÅÕ•¹•m5…ÁÁ¥¹mÍÑÈ°½‰©•Ñut°(¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñutè(€€€¹…µ•Ì€ômÍÑÈ¡¥Ñ•´¹•Ð ‰¹…µ”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤™½È¥Ñ•´¥¸¡…É…Ñ•ÉÌ¥˜ÍÑÈ¡¥Ñ•´¹•Ð ‰¹…µ”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¥t(€€€Í¡½ÑÌè±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñut€ômt(€€€™½È¥¹‘•à°Í•µ•¹Ð¥¸•¹Õµ•É…Ñ”¡ÍÁ±¥Ñ}ÍÑ½Éå}Í•µ•¹ÑÌ¡Í½ÕÉ•}Ñ•áÐ°Ñ…É•Ñ}¡…ÉÌ¤°ÍÑ…ÉÐôÄ¤è(€€€€€€€µ•¹Ñ¥½¹•€ôm¹…µ”™½È¹…µ”¥¸¹…µ•Ì¥˜¹…µ”¥¸Í•µ•¹Ñt(€€€€€€€Í¡½ÑÌ¹…ÁÁ•¹ (€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰¥¹‘•àˆè¥¹‘•à°(€€€€€€€€€€€€€€€€‰Í•µ•¹Ñ}¥ˆè˜‰Mí¥¹‘•àèÀÕ‘ôˆ°(€€€€€€€€€€€€€€€€‰Ñ¥Ñ±”ˆè˜‹–"¦Vpí¥¹‘•àèÀÉ‘ôˆ°(€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆèÍ•µ•¹Ð°(€€€€€€€€€€€€€€€€‰¹…ÉÉ…Ñ¥½¸ˆèÍ•µ•¹Ð°(€€€€€€€€€€€€€€€€‰¡…É…Ñ•ÉÌˆèµ•¹Ñ¥½¹•½È¹…µ•ÍlèÅt°(€€€€€€€€€€€€€€€€‰Í•¹”ˆè€ˆˆ°(€€€€€€€€€€€€€€€€‰ÁÉ½µÁÐˆè˜‰ì¡µ•¹Ñ¥½¹•½È¹…µ•ÍlèÅt½ÈlŸ’êëž&¤t¥lÁu÷¾òiíÍ•µ•¹ÑlèÐáuôˆ°(€€€€€€€€€€€€€€€€‰Ñ…Í­}¥ˆè€ˆˆ°(€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‹–úžRš"@ˆ°(€€€€€€€€€€€€€€€€‰ÁÉ½É•ÍÌˆè€ˆÀ”ˆ°(€€€€€€€€€€€€€€€€‰¥µ…•}ÕÉ°ˆè€ˆˆ°(€€€€€€€€€€€€€€€€‰±½…±}Á…Ñ ˆè€ˆˆ°(€€€€€€€€€€€€€€€€‰•ÉÉ½Èˆè€ˆˆ°(€€€€€€€€€€€ô(€€€€€€€€¤(€€€É•ÑÕÉ¸Í¡½ÑÌ(()‘•˜½µÁ½Í•}Í¡½Ñ}ÁÉ½µÁÐ (€€€Í¡½Ðè5…ÁÁ¥¹mÍÑÈ°½‰©•Ñt°(€€€€¨°(€€€…ÉÑ}ÍÑå±”èÍÑÈ°(€€€…ÍÁ•ÐèÍÑÈ°(€€€¡…É…Ñ•ÉÌèM•ÅÕ•¹•m5…ÁÁ¥¹mÍÑÈ°½‰©•Ñut°(€€€Í•¹•ÌèM•ÅÕ•¹•m5…ÁÁ¥¹mÍÑÈ°½‰©•Ñut€ô€ ¤°(¤€´øÍÑÈè(€€€Í•±•Ñ•‘}¹…µ•Ì€ôíÍÑÈ¡¹…µ”¤™½È¹…µ”¥¸Í¡½Ð¹•Ð ‰¡…É…Ñ•ÉÌˆ°mt¤¥˜ÍÑÈ¡¹…µ”¤¹ÍÑÉ¥À ¥ô(€€€Í•±•Ñ•€ôm¥Ñ•´™½È¥Ñ•´¥¸¡…É…Ñ•ÉÌ¥˜ÍÑÈ¡¥Ñ•´¹•Ð ‰¹…µ”ˆ°€ˆˆ¤¤¥¸Í•±•Ñ•‘}¹…µ•Ít(€€€Í•±•Ñ•‘}¡…É…Ñ•É}¹…µ•Ì€ômÍÑÈ¡¥Ñ•´¹•Ð ‰¹…µ”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤™½È¥Ñ•´¥¸Í•±•Ñ•¥˜ÍÑÈ¡¥Ñ•´¹•Ð ‰¹…µ”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¥t(€€€Ù¥ÍÕ…°€ôÍÑÈ¡Í¡½Ð¹•Ð ‰ÁÉ½µÁÐˆ¤½ÈÍ¡½Ð¹•Ð ‰¹…ÉÉ…Ñ¥½¸ˆ¤½ÈÍ¡½Ð¹•Ð ‰Í½ÕÉ”ˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€Á¥••Ì€ôl(€€€€€€€€‹¦Vs–’Óšf¿–"¯–në–ºk’î—’â·¢þGšf¿š"[¢þGšf¿’âë’âï¾ò3’êëž&§¦žR£¢ã–?Ž–6+¢ê¯š"[¢Ã¦£’î—’â+šz–nû¾ò3’âï¢š’êëž&§–6ƒžRï¦v‹¦®c–ê›žê˜€ÔÔ—¾öxàÀ—¾ò3¦v‹¦£¢†£ššâšfÀˆ°(€€€€€€€Ù¥ÍÕ…°°(€€€t(€€€Í•¹•}¹…µ”€ôÍÑÈ¡Í¡½Ð¹•Ð ‰Í•¹”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€Í•±•Ñ•‘}Í•¹”€ô¹•áÐ ¡¥Ñ•´™½È¥Ñ•´¥¸Í•¹•Ì¥˜ÍÑÈ¡¥Ñ•´¹•Ð ‰¹…µ”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤€ôôÍ•¹•}¹…µ”¤°9½¹”¤(€€€¥˜Í•±•Ñ•‘}Í•¹”è(€€€€€€€Á¥••Ì¹…ÁÁ•¹¡˜‹–rëšf¿’öÿžR£ŠqíÍ•¹•}¹…µ•÷Šw–>¢–nøˆ¤(€€€¥˜Í•±•Ñ•‘}¡…É…Ñ•É}¹…µ•Ìè(€€€€€€€Á¥••Ì¹…ÁÁ•¹¡˜‹’êëž&§’öÿžR¡ìŸŽœ¹©½¥¸¡Í•±•Ñ•‘}¡…É…Ñ•É}¹…µ•Ì¥÷–>¢–nøˆ¤(€€€Á¥••Ì¹•áÑ•¹ (€€€€€€€l(€€€€€€€€€€€•áÁ…¹‘}…ÉÑ}ÍÑå±”¡…ÉÑ}ÍÑå±”¤°(€€€€€€€€€€€€‹¦vgššò¯žRï–6W–æžRï¦v‹¾ò3–>«¢†£ž:Ã¢þg’â–"ìˆ°(€€€€€€€€€€€€‹–>«šr'–&Ÿšš‚ã–þ–ÂÇšb¿ž:¿–Š’âS’â7’öÿžR£’â·šf¿š^ƒšÎW¢ºËšâš^Ûš&7–¢ºã’â·šf¿¾òo’â7’öÿžR£¢þsšf¿Ž–’Ÿ–£šf¿Ž¢Ú¢þsšf¿¾ò3’â7¢º§’êëž&§–r£žRï¦v‹’â·¢þ–Â<ˆ°(€€€€€€€€€€€€‹š^ƒšZ–¶_¾ò3š^ƒ–¾çžf÷šÂSšÎ‡¾ò3š^ƒšÂÓ–6Àˆ°(€€€€€€€t(€€€€¤(€€€É…Ñ¥¼€ô…ÍÁ•Ð¹ÍÑÉ¥À ¤¥˜É”¹™Õ±±µ…Ñ ¡È‰q‘ìÄ°Éôéq‘ìÄ°Éôˆ°…ÍÁ•Ð¹ÍÑÉ¥À ¤¤•±Í”€ˆäèÄØˆ(€€€Á¥••Ì¹…ÁÁ•¹¡˜‹žRï¦v‹–º÷¦®cš¾S’âèíÉ…Ñ¥½ôˆ¤(€€€É•ÑÕÉ¸€‹¾ò0ˆ¹©½¥¸¡Á¥•”™½ÈÁ¥•”¥¸Á¥••Ì¥˜Á¥•”¤(()‘•˜‰Õ¥±‘}½Ù•É}ÁÉ½µÁÐ (€€€Ñ¥Ñ±”èÍÑÈ°(€€€Ù¥ÍÕ…±}ÁÉ½µÁÐèÍÑÈ°(€€€€¨°(€€€…ÉÑ}ÍÑå±”èÍÑÈ°(€€€…ÍÁ•ÐèÍÑÈ°(€€€¡…É…Ñ•É}¹…µ”èÍÑÈ€ô€ˆˆ°(€€€Í•¹•}¹…µ”èÍÑÈ€ô€ˆˆ°(¤€´øÍÑÈè(€€€€ˆˆ‰	Õ¥±„ÍÑ…Ñ¥Œ½µ¥ŒµÁ½ÍÐ½Ù•ÈÁÉ½µÁÐÝ¡¥±”ÁÉ•Í•ÉÙ¥¹œÍ•±•Ñ•É•™•É•¹•Ì¸ˆˆˆ(€€€½Ù•É}Ñ¥Ñ±”€ôÑ¥Ñ±”¹ÍÑÉ¥À ¤½È€‹šò¯žRïš:£šZˆ(€€€É…Ñ¥¼€ô…ÍÁ•Ð¹ÍÑÉ¥À ¤¥˜É”¹™Õ±±µ…Ñ ¡È‰q‘ìÄ°Éôéq‘ìÄ°Éôˆ°…ÍÁ•Ð¹ÍÑÉ¥À ¤¤•±Í”€ˆäèÄØˆ(€€€Ù¥ÍÕ…°€ôÙ¥ÍÕ…±}ÁÉ½µÁÐ¹ÍÑÉ¥À ¤½È€‹’âï¢žK’î—–òëž#šžî«¦v‹–¾ç¦Vs–’Ó¾ò3žRï¦v‹–ßšr'š
³–þ×–J3–&Ÿš–Ëžªš|ˆ(€€€Á¥••Ì€ôl(€€€€€€€€‹ž~·¢ž¦ŠG–æÏ–>Ãšò¯žRïš:£šZ–Â¦v‹¾ò3¦vgš–6W–æš>KžRìˆ°(€€€€€€€Ù¥ÍÕ…°°(€€€€€€€€‹¦Vs–’Ó’î—’â·¢þGšf¿š"[¢þGšf¿’âë’âï¾ò3¦žR£¢ã–?Ž–6+¢ê¯š"[¢Ã¦£’î—’â+šz–nû¾ò3’âï’öOžª–ë¾ò3¦v‹¦£¢†£ššâšfÃ¾ò3žšš¶‹¢þsšf¿–J3–’Ÿ–£šf¼ˆ°(€€€t(€€€¥˜¡…É…Ñ•É}¹…µ”¹ÍÑÉ¥À ¤è(€€€€€€€Á¥••Ì¹…ÁÁ•¹¡˜‹’âï¢š’êëž&§’öÿžR£Šqí¡…É…Ñ•É}¹…µ”¹ÍÑÉ¥À ¥÷Šw–>¢–nû¾ò3’â—š‚ó’þwš2–B3’â’êëž&§žj¢ãŽ–>G–z/Žšr7¢Ž’â;’öOšˆ¤(€€€¥˜Í•¹•}¹…µ”¹ÍÑÉ¥À ¤è(€€€€€€€Á¥••Ì¹…ÁÁ•¹¡˜‹ž:¿–Š’öÿžR£ŠqíÍ•¹•}¹…µ”¹ÍÑÉ¥À ¥÷Šw–në–ºk–rëšf¿–>¢–nû¾ò3’þwš2ž¦ë¦^Ó’â;¦f#¢ºû’â¢Ðˆ¤(€€€Á¥••Ì¹•áÑ•¹ (€€€€€€€l(€€€€€€€€€€€•áÁ…¹‘}…ÉÑ}ÍÑå±”¡…ÉÑ}ÍÑå±”¤°(€€€€€€€€€€€˜‹žRï¦v‹šâšfÃšbûž’ë¦Kžn»žj’â·šZ–Â¦v‹š‚¦ŠcŠqí½Ù•É}Ñ¥Ñ±•÷Šw¾ò3š‚¦Šc–¶_–’ŸŽšbO¢¾ïŽ’â7¢÷¦»š2‡’êëž&§¦v‹¦£¾òlˆ(€€€€€€€€€€€€‹š‚¦ŠcšVÓ’öO’ö7’ê;žRï¦v‹šÂÓ–æÏšZç–BG–Æ’â·Ž–zžnÓšZç–BG’â·¦^Ó–?’â/žj’ö7žö»¾ò3’â·–þžê›–r£žRï¦v‹¦®c–ê›žj€ØÔ—¾öxÜÔ—¾ò3žšš¶‹šRû–r£¦†Û¦£š"[žÒŸ¢ÒÓ–êW¢úäˆ°(€€€€€€€€€€€€‹¦®cž
ç–ïž:–Â¦v‹šz–nû¾ò3–òë–¾çš¾S–Æš²‡¾ò3’âï’öO’â;š‚¦Šc–r£š&/šrëžò§žV—–nû’â·’î7žÛšâš–hˆ°(€€€€€€€€€€€˜‹žRï¦v‹–º÷¦®cš¾S’âèíÉ…Ñ¥½ôˆ°(€€€€€€€€€€€€‹š^ƒ–¾çžf÷šÂSšÎ‡¾ò3š^ƒšÂÓ–6Ã¾ò3š^ƒ–æÏ–>Ãš‚–þ\ˆ°(€€€€€€€t(€€€€¤(€€€É•ÑÕÉ¸€‹¾ò0ˆ¹©½¥¸¡Á¥•”™½ÈÁ¥•”¥¸Á¥••Ì¥˜Á¥•”¤(()‘•˜É•Á±…•}¡…É…Ñ•É}¥¹}Í¡½ÑÌ¡Í¡½ÑÌèM•ÅÕ•¹•m‘¥ÑmÍÑÈ°½‰©•Ñut°Í½ÕÉ•}¹…µ”èÍÑÈ°Ñ…É•Ñ}¹…µ”èÍÑÈ€ô€ˆˆ¤€´ø¥¹Ðè(€€€€ˆˆ‰I•Á±…”½ÈÉ•µ½Ù”½¹”¡…É…Ñ•È‰¥¹‘¥¹œÝ¡¥±”ÁÉ•Í•ÉÙ¥¹œ±¥ÍÐ½É‘•È¸ˆˆˆ(€€€Í½ÕÉ•}¹…µ”€ôÍ½ÕÉ•}¹…µ”¹ÍÑÉ¥À ¤(€€€Ñ…É•Ñ}¹…µ”€ôÑ…É•Ñ}¹…µ”¹ÍÑÉ¥À ¤(€€€¥˜¹½ÐÍ½ÕÉ•}¹…µ”½ÈÍ½ÕÉ•}¹…µ”€ôôÑ…É•Ñ}¹…µ”è(€€€€€€€É•ÑÕÉ¸€À(€€€¡…¹•€ô€À(€€€™½ÈÍ¡½Ð¥¸Í¡½ÑÌè(€€€€€€€¹…µ•Ì€ômÍÑÈ¡¹…µ”¤¹ÍÑÉ¥À ¤™½È¹…µ”¥¸Í¡½Ð¹•Ð ‰¡…É…Ñ•ÉÌˆ°mt¤¥˜ÍÑÈ¡¹…µ”¤¹ÍÑÉ¥À ¥t(€€€€€€€¥˜Í½ÕÉ•}¹…µ”¹½Ð¥¸¹…µ•Ìè(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€É•Á±…•€ômÑ…É•Ñ}¹…µ”¥˜¹…µ”€ôôÍ½ÕÉ•}¹…µ”•±Í”¹…µ”™½È¹…µ”¥¸¹…µ•Ít(€€€€€€€‘•‘ÕÁ±¥…Ñ•è±¥ÍÑmÍÑÉt€ômt(€€€€€€€™½È¹…µ”¥¸É•Á±…•è(€€€€€€€€€€€¥˜¹…µ”…¹¹…µ”¹½Ð¥¸‘•‘ÕÁ±¥…Ñ•è(€€€€€€€€€€€€€€€‘•‘ÕÁ±¥…Ñ•¹…ÁÁ•¹¡¹…µ”¤(€€€€€€€Í¡½Ñl‰¡…É…Ñ•ÉÌ‰t€ô‘•‘ÕÁ±¥…Ñ•(€€€€€€€¡…¹•€¬ô€Ä(€€€É•ÑÕÉ¸¡…¹•(()‘•˜É•Á±…•}Í•¹•}¥¹}Í¡½ÑÌ¡Í¡½ÑÌèM•ÅÕ•¹•m‘¥ÑmÍÑÈ°½‰©•Ñut°Í½ÕÉ•}¹…µ”èÍÑÈ°Ñ…É•Ñ}¹…µ”èÍÑÈ€ô€ˆˆ¤€´ø¥¹Ðè(€€€€ˆˆ‰I•Á±…”½È±•…È½¹”™¥á•µÍ•¹”‰¥¹‘¥¹œ¸ˆˆˆ(€€€Í½ÕÉ•}¹…µ”€ôÍ½ÕÉ•}¹…µ”¹ÍÑÉ¥À ¤(€€€Ñ…É•Ñ}¹…µ”€ôÑ…É•Ñ}¹…µ”¹ÍÑÉ¥À ¤(€€€¥˜¹½ÐÍ½ÕÉ•}¹…µ”½ÈÍ½ÕÉ•}¹…µ”€ôôÑ…É•Ñ}¹…µ”è(€€€€€€€É•ÑÕÉ¸€À(€€€¡…¹•€ô€À(€€€™½ÈÍ¡½Ð¥¸Í¡½ÑÌè(€€€€€€€¥˜ÍÑÈ¡Í¡½Ð¹•Ð ‰Í•¹”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤€„ôÍ½ÕÉ•}¹…µ”è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€Í¡½Ñl‰Í•¹”‰t€ôÑ…É•Ñ}¹…µ”(€€€€€€€¡…¹•€¬ô€Ä(€€€É•ÑÕÉ¸¡…¹•(()‘•˜}É•Í•Ñ}ÍÑ½Éå‰½…É‘}Í¡½Ñ}•¹•É…Ñ¥½¸¡Í¡½Ðè‘¥ÑmÍÑÈ°½‰©•Ñt¤€´ø‘¥ÑmÍÑÈ°½‰©•Ñtè(€€€Í¡½Ð¹ÕÁ‘…Ñ” (€€€€€€€ì(€€€€€€€€€€€€‰Ñ…Í­}¥ˆè€ˆˆ°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‹–ú¦7šZÃžRš"@ˆ°(€€€€€€€€€€€€‰ÁÉ½É•ÍÌˆè€ˆÀ”ˆ°(€€€€€€€€€€€€‰¥µ…•}ÕÉ°ˆè€ˆˆ°(€€€€€€€€€€€€‰±½…±}Á…Ñ ˆè€ˆˆ°(€€€€€€€€€€€€‰•ÉÉ½Èˆè€ˆˆ°(€€€€€€€€€€€€‰™¥¹…±}ÁÉ½µÁÐˆè€ˆˆ°(€€€€€€€ô(€€€€¤(€€€É•ÑÕÉ¸Í¡½Ð(()‘•˜µ•É•}ÍÑ½Éå‰½…É‘}Í¡½ÑÌ¡™¥ÉÍÐè5…ÁÁ¥¹mÍÑÈ°½‰©•Ñt°Í•½¹è5…ÁÁ¥¹mÍÑÈ°½‰©•Ñt¤€´ø‘¥ÑmÍÑÈ°½‰©•Ñtè(€€€€ˆˆ‰5•É”ÑÝ¼…‘©…•¹Ð•‘¥Ñ…‰±”ÍÑ½Éå‰½…ÉÉ•½É‘ÌÝ¥Ñ¡½ÕÐ±½Í¥¹œÍ½ÕÉ”½É‘•È¸ˆˆˆ(€€€µ•É•€ô‘¥Ð¡™¥ÉÍÐ¤(€€€™¥ÉÍÑ}Ñ¥Ñ±”€ôÍÑÈ¡™¥ÉÍÐ¹•Ð ‰Ñ¥Ñ±”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€Í•½¹‘}Ñ¥Ñ±”€ôÍÑÈ¡Í•½¹¹•Ð ‰Ñ¥Ñ±”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€µ•É•‘l‰Ñ¥Ñ±”‰t€ô€ˆ€¬€ˆ¹©½¥¸¡¥Ñ•´™½È¥Ñ•´¥¸€¡™¥ÉÍÑ}Ñ¥Ñ±”°Í•½¹‘}Ñ¥Ñ±”¤¥˜¥Ñ•´¥lèÄÈÁt½È€‹–B#–æÛ–"¦Vpˆ(€€€µ•É•‘l‰Í½ÕÉ”‰t€ôÍÑÈ¡™¥ÉÍÐ¹•Ð ‰Í½ÕÉ”ˆ°€ˆˆ¤¤¹ÉÍÑÉ¥À ¤€¬ÍÑÈ¡Í•½¹¹•Ð ‰Í½ÕÉ”ˆ°€ˆˆ¤¤¹±ÍÑÉ¥À ¤(€€€µ•É•‘l‰¹…ÉÉ…Ñ¥½¸‰t€ô€‰q¸ˆ¹©½¥¸ (€€€€€€€¥Ñ•´™½È¥Ñ•´¥¸€¡ÍÑÈ¡™¥ÉÍÐ¹•Ð ‰¹…ÉÉ…Ñ¥½¸ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤°ÍÑÈ¡Í•½¹¹•Ð ‰¹…ÉÉ…Ñ¥½¸ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤¤¥˜¥Ñ•´(€€€€¤(€€€¹…µ•Ìè±¥ÍÑmÍÑÉt€ômt(€€€™½È¥Ñ•´¥¸±¥ÍÐ¡™¥ÉÍÐ¹•Ð ‰¡…É…Ñ•ÉÌˆ°mt¤¤€¬±¥ÍÐ¡Í•½¹¹•Ð ‰¡…É…Ñ•ÉÌˆ°mt¤¤è(€€€€€€€¹…µ”€ôÍÑÈ¡¥Ñ•´¤¹ÍÑÉ¥À ¤(€€€€€€€¥˜¹…µ”…¹¹…µ”¹½Ð¥¸¹…µ•Ìè(€€€€€€€€€€€¹…µ•Ì¹…ÁÁ•¹¡¹…µ”¤(€€€µ•É•‘l‰¡…É…Ñ•ÉÌ‰t€ô¹…µ•Ì(€€€™¥ÉÍÑ}Í•¹”€ôÍÑÈ¡™¥ÉÍÐ¹•Ð ‰Í•¹”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€Í•½¹‘}Í•¹”€ôÍÑÈ¡Í•½¹¹•Ð ‰Í•¹”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€µ•É•‘l‰Í•¹”‰t€ô™¥ÉÍÑ}Í•¹”¥˜™¥ÉÍÑ}Í•¹”€ôôÍ•½¹‘}Í•¹”•±Í”€ˆˆ(€€€™¥ÉÍÑ}ÁÉ½µÁÐ€ôÍÑÈ¡™¥ÉÍÐ¹•Ð ‰ÁÉ½µÁÐˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€Í•½¹‘}ÁÉ½µÁÐ€ôÍÑÈ¡Í•½¹¹•Ð ‰ÁÉ½µÁÐˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€µ•É•‘l‰ÁÉ½µÁÐ‰t€ô™¥ÉÍÑ}ÁÉ½µÁÐ¥˜™¥ÉÍÑ}ÁÉ½µÁÐ€ôôÍ•½¹‘}ÁÉ½µÁÐ•±Í”€¡™¥ÉÍÑ}ÁÉ½µÁÐ½ÈÍ•½¹‘}ÁÉ½µÁÐ¤(€€€É•ÑÕÉ¸}É•Í•Ñ}ÍÑ½Éå‰½…É‘}Í¡½Ñ}•¹•É…Ñ¥½¸¡µ•É•¤(()‘•˜ÍÁ±¥Ñ}ÍÑ½Éå‰½…É‘}Í¡½Ð¡Í¡½Ðè5…ÁÁ¥¹mÍÑÈ°½‰©•Ñt°½™™Í•Ðè¥¹Ð¤€´øÑÕÁ±•m‘¥ÑmÍÑÈ°½‰©•Ñt°‘¥ÑmÍÑÈ°½‰©•Ñutè(€€€€ˆˆ‰MÁ±¥Ð½¹”ÍÑ½Éå‰½…ÉÉ•½É…Ð…¸•á…ÐÍ½ÕÉ”µÑ•áÐ¡…É…Ñ•È½™™Í•Ð¸ˆˆˆ(€€€Í½ÕÉ”€ôÍÑÈ¡Í¡½Ð¹•Ð ‰Í½ÕÉ”ˆ°€ˆˆ¤¤(€€€¥˜¹½Ð€À€ð½™™Í•Ð€ð±•¸¡Í½ÕÉ”¤è(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹š.–"’ö7žö»–þ¦†ï’ö7’ê;–"¦Vs–:šZ’â·¦^ÓŽˆ¤(€€€™¥ÉÍÑ}Í½ÕÉ”€ôÍ½ÕÉ•lé½™™Í•Ñt¹ÍÑÉ¥À ¤(€€€Í•½¹‘}Í½ÕÉ”€ôÍ½ÕÉ•m½™™Í•Ðét¹ÍÑÉ¥À ¤(€€€¥˜¹½Ð™¥ÉÍÑ}Í½ÕÉ”½È¹½ÐÍ•½¹‘}Í½ÕÉ”è(€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‹š.–"–B;žj’â“’â«¦Vs–’Ó¦÷–þ¦†ï–2–B¯–:šZŽˆ¤((€€€™¥ÉÍÐ€ô‘¥Ð¡Í¡½Ð¤(€€€Í•½¹€ô‘¥Ð¡Í¡½Ð¤(€€€Ñ¥Ñ±”€ôÍÑÈ¡Í¡½Ð¹•Ð ‰Ñ¥Ñ±”ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤½È€‹–"¦Vpˆ(€€€™¥ÉÍÑl‰Ñ¥Ñ±”‰t€ô˜‰íÑ¥Ñ±•÷¾ò#’â+¾ò$ˆ(€€€Í•½¹‘l‰Ñ¥Ñ±”‰t€ô˜‰íÑ¥Ñ±•÷¾ò#’â/¾ò$ˆ(€€€™¥ÉÍÑl‰Í½ÕÉ”‰t€ô™¥ÉÍÑ}Í½ÕÉ”(€€€Í•½¹‘l‰Í½ÕÉ”‰t€ôÍ•½¹‘}Í½ÕÉ”(€€€™¥ÉÍÑl‰¡…É…Ñ•ÉÌ‰t€ômÍÑÈ¡¥Ñ•´¤™½È¥Ñ•´¥¸Í¡½Ð¹•Ð ‰¡…É…Ñ•ÉÌˆ°mt¥t(€€€Í•½¹‘l‰¡…É…Ñ•ÉÌ‰t€ômÍÑÈ¡¥Ñ•´¤™½È¥Ñ•´¥¸Í¡½Ð¹•Ð ‰¡…É…Ñ•ÉÌˆ°mt¥t((€€€¹…ÉÉ…Ñ¥½¸€ôÍÑÈ¡Í¡½Ð¹•Ð ‰¹…ÉÉ…Ñ¥½¸ˆ°€ˆˆ¤¤(€€€¥˜¹…ÉÉ…Ñ¥½¸è(€€€€€€€¹…ÉÉ…Ñ¥½¹}½™™Í•Ð€ôÉ½Õ¹¡±•¸¡¹…ÉÉ…Ñ¥½¸¤€¨±•¸¡™¥ÉÍÑ}Í½ÕÉ”¤€¼µ…à¡±•¸¡™¥ÉÍÑ}Í½ÕÉ”¤€¬±•¸¡Í•½¹‘}Í½ÕÉ”¤°€Ä¤¤(€€€€€€€¹…ÉÉ…Ñ¥½¹}½™™Í•Ð€ôµ¥¸¡µ…à¡¹…ÉÉ…Ñ¥½¹}½™™Í•Ð°€Ä¤°±•¸¡¹…ÉÉ…Ñ¥½¸¤€´€Ä¤¥˜±•¸¡¹…ÉÉ…Ñ¥½¸¤€ø€Ä•±Í”€À(€€€€€€€™¥ÉÍÑl‰¹…ÉÉ…Ñ¥½¸‰t€ô¹…ÉÉ…Ñ¥½¹lé¹…ÉÉ…Ñ¥½¹}½™™Í•Ñt¹ÍÑÉ¥À ¤¥˜¹…ÉÉ…Ñ¥½¹}½™™Í•Ð•±Í”¹…ÉÉ…Ñ¥½¸(€€€€€€€Í•½¹‘l‰¹…ÉÉ…Ñ¥½¸‰t€ô¹…ÉÉ…Ñ¥½¹m¹…ÉÉ…Ñ¥½¹}½™™Í•Ðét¹ÍÑÉ¥À ¤¥˜¹…ÉÉ…Ñ¥½¹}½™™Í•Ð•±Í”€ˆˆ(€€€É•ÑÕÉ¸}É•Í•Ñ}ÍÑ½Éå‰½…É‘}Í¡½Ñ}•¹•É…Ñ¥½¸¡™¥ÉÍÐ¤°}É•Í•Ñ}ÍÑ½Éå‰½…É‘}Í¡½Ñ}•¹•É…Ñ¥½¸¡Í•½¹¤(()‘•˜¡…É…Ñ•É}É•™•É•¹•}‘…Ñ„¡¡…É…Ñ•ÉÌè%Ñ•É…‰±•m5…ÁÁ¥¹mÍÑÈ°½‰©•Ñut¤€´ø±¥ÍÑmÍÑÉtè(€€€É•™•É•¹•Ìè±¥ÍÑmÍÑÉt€ômt(€€€™½È¥Ñ•´¥¸¡…É…Ñ•ÉÌè(€€€€€€€Á…Ñ €ôÍÑÈ¡¥Ñ•´¹•Ð ‰±½…±}Á…Ñ ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€€€€€¥˜Á…Ñ …¹A…Ñ ¡Á…Ñ ¤¹¥Í}™¥±” ¤è(€€€€€€€€€€€É•™•É•¹•Ì¹…ÁÁ•¹¡¥µ…•}‘…Ñ…}ÕÉ°¡Á…Ñ ¤¤(€€€€€€€•±Í”è(€€€€€€€€€€€¥µ…•}ÕÉ°€ôÍÑÈ¡¥Ñ•´¹•Ð ‰¥µ…•}ÕÉ°ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€¥˜¥µ…•}ÕÉ°¹ÍÑ…ÉÑÍÝ¥Ñ   ‰¡ÑÑÀè¼¼ˆ°€‰¡ÑÑÁÌè¼¼ˆ°€‰‘…Ñ„é¥µ…”¼ˆ¤¤è(€€€€€€€€€€€€€€€É•™•É•¹•Ì¹…ÁÁ•¹¡¥µ…•}ÕÉ°¤(€€€€€€€¥˜±•¸¡É•™•É•¹•Ì¤€øô€ÄÀè(€€€€€€€€€€€‰É•…¬(€€€É•ÑÕÉ¸É•™•É•¹•Ì(()‘•˜Í•¹•}É•™•É•¹•}‘…Ñ„¡Í•¹”è5…ÁÁ¥¹mÍÑÈ°½‰©•Ñtð9½¹”¤€´ø±¥ÍÑmÍÑÉtè(€€€¥˜¹½ÐÍ•¹”è(€€€€€€€É•ÑÕÉ¸mt(€€€Á…Ñ €ôÍÑÈ¡Í•¹”¹•Ð ‰±½…±}Á…Ñ ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€¥˜Á…Ñ …¹A…Ñ ¡Á…Ñ ¤¹¥Í}™¥±” ¤è(€€€€€€€É•ÑÕÉ¸m¥µ…•}‘…Ñ…}ÕÉ°¡Á…Ñ ¥t(€€€¥µ…•}ÕÉ°€ôÍÑÈ¡Í•¹”¹•Ð ‰¥µ…•}ÕÉ°ˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€¥˜¥µ…•}ÕÉ°¹ÍÑ…ÉÑÍÝ¥Ñ   ‰¡ÑÑÀè¼¼ˆ°€‰¡ÑÑÁÌè¼¼ˆ°€‰‘…Ñ„é¥µ…”¼ˆ¤¤è(€€€€€€€É•ÑÕÉ¸m¥µ…•}ÕÉ±t(€€€É•ÑÕÉ¸mt(