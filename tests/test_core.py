from __future__ import annotations

import json
import subprocess
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
    SOURCE_VIDEO_VOLUME,
    clamp_srt_text,
    detect_jianying_drafts_path,
    detect_jianying_executable,
    open_jianying,
    sanitize_draft_name,
    unique_draft_name,
)
from core.storage import DEFAULT_STATE, StateStore
from core.secret_store import SecretStoreError, delete_api_key, load_api_key, save_api_key
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
        self.assertNotIn("temperature", payload)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(result, "连接成功")

    def test_non_kimi_provider_keeps_requested_temperature(self) -> None:
        config = AIConfig("https://api.deepseek.com", "deepseek-v4-flash", "test-key", provider="deepseek")
        client = OpenAICompatibleClient(config)
        response = _FakeResponse({"choices": [{"message": {"content": "完成"}}]})
        with patch("core.ai_client.urllib.request.urlopen", return_value=response) as urlopen:
            client.complete("系统提示", "用户提示", temperature=0.72)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["temperature"], 0.72)


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

    def test_balanced_mix_uses_every_source_instead_of_only_the_first(self) -> None:
        clips = [
            VideoClip("a.mp4", duration=60.0, source_duration=60.0),
            VideoClip("b.mp4", duration=60.0, source_duration=60.0),
            VideoClip("c.mp4", duration=60.0, source_duration=60.0),
        ]
        fitted = fit_clips_to_duration(clips, 30.0, overlap=0.35, strategy="balanced")
        self.assertEqual([clip.path for clip in fitted], ["a.mp4", "b.mp4", "c.mp4"])
        self.assertAlmostEqual(sum(clip.duration for clip in fitted) - 0.35 * 2, 30.0)
        self.assertTrue(all(clip.source_duration == 60.0 for clip in fitted))

    def test_sequential_mix_can_keep_a_long_clip_intact(self) -> None:
        clips = [VideoClip("a.mp4", duration=60.0), VideoClip("b.mp4", duration=60.0)]
        fitted = fit_clips_to_duration(clips, 30.0, strategy="sequential")
        self.assertEqual(len(fitted), 1)
        self.assertEqual(fitted[0].path, "a.mp4")
        self.assertAlmostEqual(fitted[0].duration, 30.0)

    def test_balanced_timeline_hits_target_across_short_and_long_sources(self) -> None:
        cases = [
            ([60.0, 60.0, 60.0], 30.0, 0.35),
            ([2.0, 3.0], 7.25, 0.35),
            ([1.0, 4.0, 9.0, 2.0], 12.0, 0.5),
            ([0.4, 0.6, 1.2], 5.0, 0.1),
            ([120.0], 45.0, 0.35),
        ]
        for durations, target, requested_overlap in cases:
            clips = [VideoClip(str(index), duration=duration) for index, duration in enumerate(durations)]
            fitted = fit_clips_to_duration(clips, target, requested_overlap, "balanced")
            overlap = min(requested_overlap, min(durations) / 2)
            effective = sum(clip.duration for clip in fitted) - overlap * max(0, len(fitted) - 1)
            self.assertAlmostEqual(effective, target, places=3)

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
            self.assertIn("setpts=(PTS-STARTPTS)/1.500000", joined)
            self.assertIn("xfade=transition=fade", joined)
            self.assertIn("volume=0.28", joined)
            self.assertAlmostEqual(project.output_duration, 5.0 / 1.5 + 6.0 / 1.5 - 0.5)

    def test_default_playback_speed_shortens_visual_timeline(self) -> None:
        project = VideoProject(
            clips=[VideoClip("a.mp4", duration=15.0)],
            transition="none",
        )
        self.assertEqual(project.playback_speed, 1.5)
        self.assertAlmostEqual(project.output_duration, 10.0)

    def test_voice_audio_keeps_exact_output_duration_at_normal_speed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "video.mp4"
            voice = root / "voice.mp3"
            video.write_bytes(b"placeholder")
            voice.write_bytes(b"placeholder")
            project = VideoProject(
                clips=[VideoClip(str(video), duration=15.0)],
                voice_path=str(voice),
                target_duration=12.0,
                transition="none",
            )
            joined = " ".join(build_export_command(project, "ffmpeg", str(root / "out.mp4")))
            self.assertAlmostEqual(project.output_duration, 12.0)
            self.assertIn("setpts=(PTS-STARTPTS)/1.500000", joined)
            self.assertIn("atrim=duration=12.000", joined)
            self.assertIn("trim=duration=12.000", joined)

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
            self.assertTrue(store.load()["settings"]["remember_api_key"])


class SecretStoreTests(unittest.TestCase):
    def test_windows_routes_to_credential_manager(self) -> None:
        with patch("core.secret_store.sys.platform", "win32"):
            with patch("core.secret_store._windows_read", return_value="saved-key") as read:
                self.assertEqual(load_api_key("kimi"), "saved-key")
                read.assert_called_once_with("kimi")
            with patch("core.secret_store._windows_write") as write:
                save_api_key("kimi", "  new-key  ")
                write.assert_called_once_with("kimi", "new-key")
            with patch("core.secret_store._windows_delete") as delete:
                delete_api_key("kimi")
                delete.assert_called_once_with("kimi")

    def test_macos_keychain_read_trims_only_line_break(self) -> None:
        result = subprocess.CompletedProcess([], 0, " key-with-spaces \n", "")
        with patch("core.secret_store.sys.platform", "darwin"):
            with patch("core.secret_store._run_security", return_value=result) as security:
                self.assertEqual(load_api_key("deepseek"), " key-with-spaces ")
        security.assert_called_once_with(
            ["find-generic-password", "-s", "RelaxCreatorStudio", "-a", "deepseek", "-w"]
        )

    def test_invalid_provider_id_is_rejected(self) -> None:
        with self.assertRaises(SecretStoreError):
            load_api_key("../unsafe")

    def test_unsupported_platform_does_not_fake_secure_storage(self) -> None:
        with patch("core.secret_store.sys.platform", "linux"):
            self.assertEqual(load_api_key("qwen"), "")
            with self.assertRaises(SecretStoreError):
                save_api_key("qwen", "secret")


class JianyingEngineTests(unittest.TestCase):
    def test_imported_video_audio_is_muted(self) -> None:
        self.assertEqual(SOURCE_VIDEO_VOLUME, 0.0)

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
