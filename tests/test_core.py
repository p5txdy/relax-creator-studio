from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.ai_client import (
    AIConfig,
    OpenAICompatibleClient,
    api_key_from_environment,
    infer_provider,
    provider_preset,
)
from core.novel_engine import build_post_prompt, build_rewrite_prompt, chapter_records, split_chapters
from core.jianying_engine import (
    clamp_srt_text,
    detect_jianying_drafts_path,
    detect_jianying_executable,
    open_jianying,
    sanitize_draft_name,
    unique_draft_name,
)
from core.storage import DEFAULT_STATE, StateStore
from core.video_engine import VideoClip, VideoProject, build_export_command, fit_clips_to_duration


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class AIClientTests(unittest.TestCase):
    def test_official_provider_presets(self) -> None:
        expected = {
            "deepseek": ("https://api.deepseek.com", "deepseek-v4-flash"),
            "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
            "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-5.2"),
            "kimi": ("https://api.moonshot.cn/v1", "kimi-k3"),
        }
        for provider_id, values in expected.items():
            preset = provider_preset(provider_id)
            self.assertEqual((preset.base_url, preset.model), values)
            self.assertEqual(infer_provider(preset.base_url, preset.model), provider_id)

    def test_provider_specific_environment_keys(self) -> None:
        values = {
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "DASHSCOPE_API_KEY": "qwen-secret",
            "ZHIPUAI_API_KEY": "glm-secret",
            "MOONSHOT_API_KEY": "kimi-secret",
        }
        self.assertEqual(api_key_from_environment("deepseek", values), "deepseek-secret")
        self.assertEqual(api_key_from_environment("qwen", values), "qwen-secret")
        self.assertEqual(api_key_from_environment("zhipu", values), "glm-secret")
        self.assertEqual(api_key_from_environment("kimi", values), "kimi-secret")

    def test_chat_completion_request_uses_selected_provider(self) -> None:
        config = AIConfig("https://api.moonshot.cn/v1", "kimi-k3", "test-key", provider="kimi")
        client = OpenAICompatibleClient(config)
        response = _FakeResponse({"choices": [{"message": {"content": "连接成功"}}]})
        with patch("core.ai_client.urllib.request.urlopen", return_value=response) as urlopen:
            result = client.complete("系统提示", "用户提示", temperature=0.2)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.moonshot.cn/v1/chat/completions")
        self.assertEqual(payload["model"], "kimi-k3")
        self.assertEqual(payload["messages"][1]["content"], "用户提示")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(result, "连接成功")


class NovelEngineTests(unittest.TestCase):
    def test_split_chinese_chapters(self) -> None:
        text = "序言内容\n\n第一章 相遇\n\n这是第一章。\n\n第二章 转折\n\n这是第二章。"
        chapters = split_chapters(text)
        self.assertEqual([chapter.title for chapter in chapters], ["序章", "第一章 相遇", "第二章 转折"])
        self.assertIn("第一章", chapters[1].content)

    def test_fallback_chunking(self) -> None:
        text = "第一段内容。\n\n第二段内容很长。\n\n第三段内容。"
        chapters = split_chapters(text, fallback_size=12)
        self.assertGreaterEqual(len(chapters), 2)
        self.assertEqual(chapters[0].title, "片段 1")

    def test_directly_pasted_text_becomes_editable_chapters(self) -> None:
        records = chapter_records("第一章 相遇\n\n她在雨夜推开门。\n\n第二章 转折\n\n灯突然熄灭。")
        self.assertEqual([item["title"] for item in records], ["第一章 相遇", "第二章 转折"])
        self.assertEqual(records[0]["content"], "她在雨夜推开门。")

    def test_rewrite_prompt_contains_rules_and_text(self) -> None:
        system, user = build_rewrite_prompt(
            "第一章",
            "原始正文",
            mode="深度改写",
            style="节奏紧凑",
            perspective="第一人称",
            target_length="与原文接近",
            custom_rules="名字不能改",
            story_bible="主角叫林川",
        )
        self.assertIn("小说编辑", system)
        for expected in ("原始正文", "名字不能改", "主角叫林川", "第一人称"):
            self.assertIn(expected, user)

    def test_post_prompt_uses_metadata(self) -> None:
        _system, user = build_post_prompt("雨夜白噪音", "治愈", "小红书", ["切肥皂", "整理地毯"], 28)
        self.assertIn("小红书", user)
        self.assertIn("28", user)
        self.assertIn("切肥皂", user)


class VideoEngineTests(unittest.TestCase):
    def test_clips_repeat_and_trim_to_audio_duration(self) -> None:
        clips = [VideoClip("a.mp4", duration=2.0), VideoClip("b.mp4", duration=3.0)]
        fitted = fit_clips_to_duration(clips, 7.25)
        self.assertEqual([clip.path for clip in fitted], ["a.mp4", "b.mp4", "a.mp4", "b.mp4"])
        self.assertAlmostEqual(sum(clip.duration for clip in fitted), 7.25)

    def test_build_command_with_transition_and_music(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "a.mp4"
            second = root / "b.mp4"
            music = root / "music.mp3"
            for path in (first, second, music):
                path.write_bytes(b"placeholder")
            project = VideoProject(
                clips=[VideoClip(str(first), 1.0, 5.0), VideoClip(str(second), 0.0, 6.0)],
                aspect="9:16",
                transition="fade",
                transition_duration=0.5,
                music_path=str(music),
            )
            command = build_export_command(project, "ffmpeg", str(root / "out.mp4"))
            joined = " ".join(command)
            self.assertIn("scale=1080:1920", joined)
            self.assertIn("xfade=transition=fade", joined)
            self.assertIn("volume=0.28", joined)
            self.assertAlmostEqual(project.output_duration, 10.5)

    def test_concat_without_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "clip.mp4"
            source.write_bytes(b"placeholder")
            project = VideoProject(clips=[VideoClip(str(source), duration=2.5)], transition="none")
            command = build_export_command(project, "ffmpeg", str(Path(temp) / "out.mp4"))
            self.assertIn("-an", command)
            self.assertNotIn("xfade", " ".join(command))


class StorageTests(unittest.TestCase):
    def test_roundtrip_and_secret_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            state = json.loads(json.dumps(DEFAULT_STATE))
            state["video"]["project_name"] = "测试项目"
            state["settings"]["api_key"] = "secret"
            store.save(state)
            raw = store.path.read_text(encoding="utf-8")
            self.assertNotIn("secret", raw)
            self.assertEqual(store.load()["video"]["project_name"], "测试项目")


class JianyingEngineTests(unittest.TestCase):
    def test_sanitize_and_unique_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(sanitize_draft_name('测试:/草稿?'), "测试__草稿_")
            (Path(temp) / "混剪").mkdir()
            self.assertEqual(unique_draft_name(temp, "混剪"), "混剪（2）")

    def test_configured_draft_folder_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(detect_jianying_drafts_path(temp), temp)

    def test_macos_app_bundle_is_detected_and_opened(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            app_bundle = Path(temp) / "剪映专业版.app"
            app_bundle.mkdir()
            with patch("core.jianying_engine.sys.platform", "darwin"):
                self.assertEqual(detect_jianying_executable(str(app_bundle)), str(app_bundle))
                with patch("core.jianying_engine.subprocess.Popen") as popen:
                    open_jianying(str(app_bundle))
                    popen.assert_called_once_with(["/usr/bin/open", str(app_bundle)], close_fds=True)

    def test_subtitles_are_clamped_to_audio_duration(self) -> None:
        source = (
            "1\n00:00:00,100 --> 00:00:01,500\n第一句\n\n"
            "2\n00:00:02,000 --> 00:00:05,000\n第二句\n\n"
            "3\n00:00:06,000 --> 00:00:07,000\n超出范围\n"
        )
        result = clamp_srt_text(source, 3_200_000)
        self.assertIn("00:00:03,200", result)
        self.assertNotIn("超出范围", result)


if __name__ == "__main__":
    unittest.main()
