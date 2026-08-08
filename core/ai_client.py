from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping


class AIClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    base_url: str
    model: str
    environment_keys: tuple[str, ...]
    description: str


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com",
        "deepseek-v4-flash",
        ("DEEPSEEK_API_KEY",),
        "DeepSeek 官方 OpenAI 兼容接口，默认使用 V4 Flash。",
    ),
    ProviderPreset(
        "qwen",
        "千问（阿里云百炼）",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        ("DASHSCOPE_API_KEY",),
        "阿里云百炼 OpenAI 兼容接口，默认使用 qwen-plus 稳定别名。",
    ),
    ProviderPreset(
        "zhipu",
        "智谱 GLM",
        "https://open.bigmodel.cn/api/paas/v4",
        "glm-5.2",
        ("ZHIPUAI_API_KEY", "ZAI_API_KEY"),
        "智谱开放平台官方接口，默认使用 GLM-5.2。",
    ),
    ProviderPreset(
        "kimi",
        "Kimi",
        "https://api.moonshot.cn/v1",
        "kimi-k3",
        ("MOONSHOT_API_KEY",),
        "Kimi API 官方 OpenAI 兼容接口，默认使用 kimi-k3。",
    ),
    ProviderPreset(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        "gpt-4.1-mini",
        ("OPENAI_API_KEY",),
        "OpenAI 官方 Chat Completions 接口。",
    ),
    ProviderPreset(
        "custom",
        "自定义兼容接口",
        "",
        "",
        ("OPENAI_API_KEY",),
        "用于其他兼容 OpenAI Chat Completions 格式的服务。",
    ),
)

PROVIDER_BY_ID = {preset.id: preset for preset in PROVIDER_PRESETS}


def provider_preset(provider_id: str) -> ProviderPreset:
    return PROVIDER_BY_ID.get(provider_id, PROVIDER_BY_ID["custom"])


def infer_provider(base_url: str, model: str = "") -> str:
    normalized_url = base_url.strip().rstrip("/").lower()
    normalized_model = model.strip().lower()
    for preset in PROVIDER_PRESETS:
        if preset.id == "custom":
            continue
        if normalized_url == preset.base_url.rstrip("/").lower():
            return preset.id
    model_prefixes = {
        "deepseek": ("deepseek-",),
        "qwen": ("qwen",),
        "zhipu": ("glm-",),
        "kimi": ("kimi-", "moonshot-"),
        "openai": ("gpt-", "o1", "o3", "o4"),
    }
    for provider_id, prefixes in model_prefixes.items():
        if any(normalized_model.startswith(prefix) for prefix in prefixes):
            return provider_id
    return "custom"


def api_key_from_environment(provider_id: str, environ: Mapping[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    for name in provider_preset(provider_id).environment_keys:
        value = values.get(name, "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True)
class AIConfig:
    base_url: str
    model: str
    api_key: str
    timeout: int = 180
    provider: str = "custom"


class OpenAICompatibleClient:
    """Dependency-free client for OpenAI-compatible chat completion endpoints."""

    def __init__(self, config: AIConfig) -> None:
        self.config = config

    def _endpoint(self) -> str:
        base = self.config.base_url.strip().rstrip("/")
        if not base.startswith(("https://", "http://")):
            raise AIClientError("模型 Base URL 必须以 http:// 或 https:// 开头。")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def complete(self, system: str, user: str, temperature: float = 0.7) -> str:
        if not self.config.api_key.strip():
            raise AIClientError("请先在“模型设置”中填写 API Key。")
        if not self.config.model.strip():
            raise AIClientError("请先填写模型名称。")
        payload = {
            "model": self.config.model.strip(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # Kimi's current model families constrain temperature to model-specific
        # fixed values. Omitting it lets the API select the valid default for
        # both thinking and non-thinking modes.
        if self.config.provider != "kimi":
            payload["temperature"] = temperature
        request = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key.strip()}",
                "Content-Type": "application/json",
                "User-Agent": "RelaxCreatorStudio/0.2.3",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(detail)
                detail = parsed.get("error", {}).get("message", detail)
            except (json.JSONDecodeError, AttributeError):
                detail = str(exc)
            label = provider_preset(self.config.provider).label
            raise AIClientError(f"{label} 接口返回错误（{exc.code}）：{detail}") from exc
        except urllib.error.URLError as exc:
            raise AIClientError(f"无法连接模型接口：{exc.reason}") from exc
        except TimeoutError as exc:
            raise AIClientError("模型请求超时，请稍后重试或调小单章长度。") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AIClientError("模型接口返回了无法解析的数据。") from exc

        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            message = result.get("error", {}).get("message") if isinstance(result, dict) else None
            raise AIClientError(message or "模型返回中没有可用文本。") from exc
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        return str(content).strip()
