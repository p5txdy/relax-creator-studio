from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .comic_engine import ComicEngineError


SEEDREAM_PRO_MODEL = "doubao-seedream-5-0-260128"
SEEDREAM_LITE_MODEL = "doubao-seedream-5-0-lite-260128"
LEGACY_SEEDREAM_PRO_MODEL = "doubao-seedream-5-0-pro-260628"
SEEDREAM_MODEL = SEEDREAM_PRO_MODEL
SEEDREAM_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
SEEDREAM_SIZES = ("1K", "2K", "3K", "4K")
SEEDREAM_MIN_CUSTOM_PIXELS = 3_686_400
SEEDREAM_PRO_1K_SIZES = {
    "9:16": "1440x2560",
    "4:5": "1728x2160",
    "3:4": "1680x2240",
    "1:1": "1920x1920",
    "4:3": "2240x1680",
    "16:9": "2560x1440",
}


@dataclass(frozen=True)
class SeedreamConfig:
    api_key: str
    base_url: str = SEEDREAM_BASE_URL
    model: str = SEEDREAM_MODEL
    timeout: int = 300


def _clean_base_url(value: str) -> str:
    base = value.strip().rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise ComicEngineError("火山方舟 API 地址必须以 http:// 或 https:// 开头。")
    return base


def _error_detail(payload: object, fallback: str) -> str:
    if not isinstance(payload, Mapping):
        return fallback
    error = payload.get("error")
    if isinstance(error, Mapping):
        return str(error.get("message") or error.get("code") or fallback)
    return str(error or payload.get("message") or fallback)


def _api_size(value: str, aspect: str = "1:1") -> str:
    """Translate the UI resolution label to Seedream 5.0's API spelling."""
    normalized = value.strip()
    preset = normalized.upper()
    if preset == "1K":
        return SEEDREAM_PRO_1K_SIZES.get(aspect.strip(), SEEDREAM_PRO_1K_SIZES["1:1"])
    if preset in SEEDREAM_SIZES:
        return preset.lower()
    if re.fullmatch(r"\d{3,5}x\d{3,5}", normalized.lower()):
        width, height = (int(part) for part in normalized.lower().split("x", 1))
        if width * height < SEEDREAM_MIN_CUSTOM_PIXELS:
            raise ComicEngineError(
                f"Seedream 5.0 自定义尺寸至少需要 {SEEDREAM_MIN_CUSTOM_PIXELS} 像素，"
                f"当前 {width}×{height} 只有 {width * height} 像素。"
            )
        return normalized.lower()
    raise ComicEngineError("Seedream 5.0 分辨率只支持 1K、2K、3K、4K 或明确的宽x高像素。")


class DoubaoSeedreamClient:
    """Dependency-free client for Volcengine Ark's Seedream image API."""

    def __init__(self, config: SeedreamConfig) -> None:
        self.config = config

    def _request_json(self, path: str, *, method: str = "GET", payload: object | None = None) -> object:
        if not self.config.api_key.strip():
            raise ComicEngineError("请先填写火山方舟 API Key。")
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{_clean_base_url(self.config.base_url)}{path}",
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.api_key.strip()}",
                "Content-Type": "application/json",
                "User-Agent": "ComicPostStudio/1.1",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed: object = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            detail = _error_detail(parsed, raw or str(exc))
            if exc.code == 404 and "has not activated" in detail.lower():
                model_name = "Seedream 5.0 Lite" if self.config.model == SEEDREAM_LITE_MODEL else "Seedream 5.0"
                detail = (
                    f"当前火山方舟账号尚未开通 {model_name}（{self.config.model}）。"
                    "请在火山方舟控制台的“开通管理 → 视觉模型”中开通该模型，"
                    "或回到批量出图页改选已经开通的模型。"
                )
            elif exc.code == 400 and "parameter `size`" in detail.lower():
                if self.config.model.strip() == SEEDREAM_LITE_MODEL:
                    supported = "2K、3K"
                else:
                    supported = "1K（按画幅转换为明确宽高）、2K、3K 或 4K"
                detail = f"图片分辨率参数不受当前 Seedream 5.0 接口支持。请使用 {supported}。接口原始提示：{detail}"
            raise ComicEngineError(f"火山方舟接口返回错误（{exc.code}）：{detail}") from exc
        except urllib.error.URLError as exc:
            raise ComicEngineError(f"无法连接火山方舟接口：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ComicEngineError("Seedream 请求超时，请稍后重试。") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ComicEngineError("火山方舟接口返回了无法解析的数据。") from exc

    def check_connection(self) -> object:
        """Validate the Ark key without submitting a paid image generation."""
        return self._request_json("/models")

    def generate_image(
        self,
        prompt: str,
        *,
        images: Sequence[str] | None = None,
        size: str = "2K",
        aspect: str = "1:1",
        output_format: str = "png",
        watermark: bool = False,
        optimize_mode: str = "standard",
        progress: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        if not prompt.strip():
            raise ComicEngineError("图片提示词不能为空。")
        api_size = _api_size(size, aspect)
        if self.config.model.strip() == SEEDREAM_LITE_MODEL and api_size not in {"2k", "3k"}:
            raise ComicEngineError("Seedream 5.0 Lite 只支持 2K、3K 分辨率，不支持 1K 或 4K。")
        if output_format not in {"png", "jpeg"}:
            raise ComicEngineError("Seedream 输出格式只支持 png 或 jpeg。")
        if optimize_mode not in {"standard", "fast"}:
            raise ComicEngineError("Seedream 提示词优化模式只支持 standard 或 fast。")

        references = [str(item).strip() for item in (images or []) if str(item).strip()]
        if len(references) > 10:
            raise ComicEngineError("Seedream 5.0 Pro 最多支持 10 张参考图。")
        request_payload: dict[str, object] = {
            "model": self.config.model.strip() or SEEDREAM_MODEL,
            "prompt": prompt.strip(),
            "size": api_size,
            "response_format": "url",
            "output_format": output_format,
            "watermark": bool(watermark),
            "optimize_prompt_options": {"mode": optimize_mode},
        }
        if references:
            request_payload["image"] = references[0] if len(references) == 1 else references

        if progress:
            progress({"status": "SUBMITTED", "progress": "15%", "id": ""})
        payload = self._request_json("/images/generations", method="POST", payload=request_payload)
        if not isinstance(payload, Mapping):
            raise ComicEngineError("Seedream 没有返回有效的图片数据。")
        data = payload.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], Mapping):
            raise ComicEngineError(_error_detail(payload, "Seedream 没有返回图片。"))
        image = data[0]
        image_url = str(image.get("url") or "").strip()
        b64_json = str(image.get("b64_json") or "").strip()
        if not image_url and b64_json:
            image_url = f"data:image/{output_format};base64,{b64_json}"
        if not image_url:
            raise ComicEngineError("Seedream 已完成生成，但响应中没有图片 URL。")

        result = {
            "id": str(image.get("id") or payload.get("id") or payload.get("created") or ""),
            "status": "SUCCESS",
            "progress": "100%",
            "imageUrl": image_url,
            "model": str(payload.get("model") or request_payload["model"]),
            "created": payload.get("created"),
        }
        if progress:
            progress(result)
        return result

    def download_image(self, image_url: str, destination: Path) -> Path:
        if image_url.startswith("data:image/"):
            try:
                encoded = image_url.split(",", 1)[1]
                content = base64.b64decode(encoded, validate=True)
            except (IndexError, ValueError) as exc:
                raise ComicEngineError("Seedream 返回的图片数据无效。") from exc
        else:
            request = urllib.request.Request(
                image_url,
                headers={"User-Agent": "ComicPostStudio/1.1"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    content = response.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                raise ComicEngineError(f"图片下载失败：{exc}") from exc
        if not content:
            raise ComicEngineError("图片下载失败：服务器返回了空文件。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_suffix(destination.suffix + ".tmp")
        temp.write_bytes(content)
        temp.replace(destination)
        return destination
