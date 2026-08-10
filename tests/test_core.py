from __future__ import annotations

import json
import base64
import subprocess
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.ai_client import (
    AIConfig,
    OpenAICompatibleClient,
    api_key_from_environment,
    infer_provider,
    provider_preset,
)
from core.comic_engine import (
    ComicEngineError,
    batch_story_segments,
    build_ai_split_storyboard_prompt,
    build_character_prompt,
    build_scene_prompt,
    build_storyboard_batch_prompt,
    compose_shot_prompt,
    export_comic_asset_pack,
    fallback_storyboard,
    has_local_reference,
    import_comic_asset_pack,
    numbered_story_segments,
    parse_storyboard_response,
    merge_storyboard_shots,
    replace_character_in_shots,
    replace_scene_in_shots,
    scene_reference_data,
    split_story_segments,
    split_storyboard_shot,
    split_story_source_chunks,
    validate_ai_storyboard_split,
    validate_storyboard_batch,
)
from core.seedream_client import DoubaoSeedreamClient, SeedreamConfig
from core.comic_video_engine import allocate_shot_durations, build_comic_video_command, parse_srt_text
from core.jianying_launcher import detect_jianying_executable as detect_jianying_launcher, open_jianying as open_jianying_launcher
from core.novel_engine import build_post_prompt, build_rewrite_prompt, chapter_records, split_chapters
from core.jianying_engine import (
    SOURCE_VIDEO_VOLUME,
    clamp_srt_text,
    create_comic_jianying_draft,
    detect_jianying_drafts_path,
    detect_jianying_executable,
    open_jianying,
    sanitize_draft_name,
    unique_draft_name,
)
from core.storage import DEFAULT_STATE, StateStore, new_comic_project
from core.secret_store import SecretStoreError, delete_api_key, load_api_key, save_api_key
from core.video_engine import VideoClip, VideoProject, build_export_command, find_executable, fit_clips_to_duration


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


class ComicEngineTests(unittest.TestCase):
    def test_transport_chunks_do_not_define_storyboard_length(self) -> None:
        text = "".join(f"第{index}句剧情推进。" for index in range(1, 501))
        chunks = split_story_source_chunks(text, max_chars=900)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(item) <= 900 for item in chunks))
        self.assertEqual("".join(item.replace("\n", "") for item in chunks), text)

    def test_ai_split_prompt_and_validation_leave_boundaries_to_model(self) -> None:
        source = "林川推开门。苏晚回头。电话突然响了。"
        _system, user = build_ai_split_storyboard_prompt(
            source,
            art_style="现代都市韩漫",
            existing_characters=[{"name": "林川", "description": "黑发"}],
            generation_mode="shots",
            batch_index=1,
            batch_total=2,
        )
        self.assertIn("不按固定字数", user)
        self.assertIn("由你决定 shots 数量", user)
        self.assertIn("单张静止图片能否完整讲清", user)
        self.assertIn("不得把需要连续播放才能理解的动作过程压缩成一张图", user)
        self.assertIn("一个表情或一个动作", user)
        self.assertIn("控制在 8～30 个汉字", user)
        self.assertIn("禁止推拉摇移、跟拍", user)
        shots = validate_ai_storyboard_split(
            [
                {"source": "林川推开门。", "title": "推门"},
                {"source": "苏晚回头。电话突然响了。", "title": "来电"},
            ],
            source,
            start_index=4,
        )
        self.assertEqual([item["segment_id"] for item in shots], ["S00004", "S00005"])
        with self.assertRaisesRegex(ComicEngineError, "未完整覆盖"):
            validate_ai_storyboard_split([{"source": "林川推开门。"}], source)

    def test_story_segments_keep_text_and_target_readable_shots(self) -> None:
        text = "林川推开门。雨水顺着外套滴落。\n\n屋里没有开灯。苏晚站在窗前。\n\n电话突然响了。"
        segments = split_story_segments(text, target_chars=24)
        self.assertGreaterEqual(len(segments), 2)
        self.assertEqual("".join(item.replace("\n", "") for item in segments), text.replace("\n", ""))

    def test_numbered_segments_are_batched_without_omission(self) -> None:
        text = "".join(f"第{index}句剧情推进。" for index in range(1, 401))
        segments = numbered_story_segments(text, target_chars=120)
        batches = batch_story_segments(segments, max_chars=900)
        flattened = [item for batch in batches for item in batch]
        self.assertGreater(len(batches), 1)
        self.assertEqual([item["segment_id"] for item in flattened], [f"S{index:05d}" for index in range(1, len(segments) + 1)])
        self.assertEqual("".join(item["source"].replace("\n", "") for item in flattened), text)

    def test_storyboard_only_prompt_requires_one_result_per_segment(self) -> None:
        segments = [
            {"segment_id": "S00001", "source": "林川推开门。"},
            {"segment_id": "S00002", "source": "苏晚站在窗前。"},
        ]
        _system, user = build_storyboard_batch_prompt(
            segments,
            art_style="现代都市韩漫",
            existing_characters=[{"name": "林川", "description": "黑发"}],
            generation_mode="shots",
            batch_index=2,
            batch_total=4,
        )
        self.assertIn("只生成分镜", user)
        self.assertIn("shots 必须恰好返回 2 项", user)
        self.assertIn("S00001、S00002", user)
        self.assertIn("characters 必须返回空数组", user)
        self.assertIn("scenes 必须返回空数组", user)

    def test_all_prompt_builds_fixed_scene_library_and_binding(self) -> None:
        _system, user = build_storyboard_batch_prompt(
            [{"segment_id": "S00001", "source": "林川走进旧书店。"}],
            art_style="现代都市韩漫",
            existing_characters=[{"name": "林川", "description": "黑发"}],
            existing_scenes=[{"name": "旧书店", "description": "木门在左，柜台在右"}],
            generation_mode="all",
            batch_index=1,
            batch_total=1,
        )
        self.assertIn("已有固定场景", user)
        self.assertIn("旧书店：木门在左，柜台在右", user)
        self.assertIn("人物 prompt 只能描述角色本身与纯色背景", user)
        self.assertIn("无场景、无建筑、无家具", user)
        self.assertIn('"scenes"', user)
        self.assertIn('"scene": "固定场景名"', user)

    def test_storyboard_batch_validation_orders_repairs_source_and_rejects_missing_shots(self) -> None:
        expected = [
            {"segment_id": "S00001", "source": "第一段。"},
            {"segment_id": "S00002", "source": "第二段。"},
        ]
        ordered = validate_storyboard_batch(
            [
                {"segment_id": "S00002", "source": "第二段。"},
                {"segment_id": "S00001", "source": "第一段。"},
            ],
            expected,
        )
        self.assertEqual([item["segment_id"] for item in ordered], ["S00001", "S00002"])
        repaired = validate_storyboard_batch(
            [
                {"segment_id": "S1", "source": "模型改写了第一段"},
                {"segment_id": "S2", "source": "第二"},
            ],
            expected,
        )
        self.assertEqual([item["source"] for item in repaired], ["第一段。", "第二段。"])
        positional = validate_storyboard_batch([{"source": "甲"}, {"source": "乙"}], expected)
        self.assertEqual([item["segment_id"] for item in positional], ["S00001", "S00002"])
        with self.assertRaisesRegex(ComicEngineError, "缺少"):
            validate_storyboard_batch(
                [
                    {"segment_id": "S00001", "source": "第一段。"},
                ],
                expected,
            )

    def test_parse_storyboard_json_creates_editable_records(self) -> None:
        raw = """```json
        {"characters":[{"name":"林川","description":"黑发，黑色风衣"}],"scenes":[{"name":"旧书店","description":"木门在左，柜台在右"}],"shots":[{"title":"雨夜","source":"林川推门","narration":"门开了","characters":["林川"],"scene":"旧书店","prompt":"雨夜推门，中景"}]}
        ```"""
        result = parse_storyboard_response(raw, art_style="日系动漫")
        self.assertEqual(result["characters"][0]["name"], "林川")
        self.assertIn("不得出现任何室内外场景", result["characters"][0]["prompt"])
        self.assertIn("只出现该角色一人", result["characters"][0]["prompt"])
        self.assertEqual(result["scenes"][0]["name"], "旧书店")
        self.assertIn("无人物", result["scenes"][0]["prompt"])
        self.assertIn("林川", result["shots"][0]["characters"])
        self.assertEqual(result["shots"][0]["scene"], "旧书店")
        self.assertEqual(result["shots"][0]["status"], "待生成")
        self.assertEqual(result["shots"][0]["prompt"], "雨夜推门，中景")

    def test_dynamic_camera_storyboard_is_rejected_for_static_comic(self) -> None:
        raw = '{"characters":[],"scenes":[],"shots":[{"source":"林川进门。","prompt":"镜头跟拍林川走进房间"}]}'
        with self.assertRaisesRegex(ComicEngineError, "动态运镜"):
            parse_storyboard_response(raw, art_style="现代都市韩漫", generation_mode="shots")

    def test_fallback_storyboard_links_named_characters(self) -> None:
        characters = [{"name": "苏晚", "description": "白色长发"}]
        shots = fallback_storyboard("苏晚站在窗前。\n\n雨停了。", art_style="国风动漫", target_chars=60, characters=characters)
        self.assertEqual(shots[0]["characters"], ["苏晚"])
        self.assertTrue(shots[0]["prompt"])
        self.assertTrue(str(shots[0]["prompt"]).startswith("苏晚："))

    def test_character_and_shot_prompts_lock_identity(self) -> None:
        character_prompt = build_character_prompt("苏晚", "白色长发，青色长裙", "国风动漫")
        self.assertIn("固定发型与服装", character_prompt)
        self.assertIn("纯白或浅灰无缝背景", character_prompt)
        self.assertIn("不得出现任何室内外场景", character_prompt)
        self.assertIn("家具", character_prompt)
        prompt = compose_shot_prompt(
            {"prompt": "苏晚回头，中景", "characters": ["苏晚"], "scene": "旧书店"},
            art_style="国风动漫",
            aspect="9:16",
            characters=[{"name": "苏晚", "description": "白色长发，青色长裙", "image_url": "https://example.com/suwan.png"}],
            scenes=[{"name": "旧书店", "description": "木门在左，柜台在右", "image_url": "https://example.com/bookstore.png"}],
        )
        self.assertIn("人物使用苏晚参考图", prompt)
        self.assertIn("场景使用“旧书店”参考图", prompt)
        self.assertIn("静态漫画单幅画面，只表现这一刻", prompt)
        self.assertNotIn("白色长发", prompt)
        self.assertNotIn("木门在左", prompt)
        self.assertIn("画面宽高比为 9:16", prompt)
        self.assertNotIn("--cref", prompt)

    def test_batch_character_and_scene_replacement_updates_bindings(self) -> None:
        shots = [
            {"characters": ["林川", "苏晚"], "scene": "客厅"},
            {"characters": ["林川"], "scene": "客厅"},
            {"characters": ["苏晚"], "scene": "厨房"},
        ]
        self.assertEqual(replace_character_in_shots(shots, "林川", "苏晚"), 2)
        self.assertEqual(shots[0]["characters"], ["苏晚"])
        self.assertEqual(shots[1]["characters"], ["苏晚"])
        self.assertEqual(replace_scene_in_shots(shots, "客厅", "厨房"), 2)
        self.assertEqual([shot["scene"] for shot in shots], ["厨房", "厨房", "厨房"])
        self.assertEqual(replace_character_in_shots(shots, "苏晚", ""), 3)
        self.assertTrue(all(not shot["characters"] for shot in shots))

    def test_scene_prompt_and_reference_are_reusable(self) -> None:
        prompt = build_scene_prompt("旧书店", "木门在左，柜台在右", "现代都市韩漫", "16:9")
        self.assertIn("无人物", prompt)
        self.assertIn("固定建筑结构、空间布局", prompt)
        self.assertIn("画面风格必须与角色定妆保持一致", prompt)
        self.assertIn("统一采用现代都市韩漫", prompt)
        self.assertIn("16:9", prompt)
        self.assertEqual(scene_reference_data({"image_url": "https://example.com/bookstore.png"}), ["https://example.com/bookstore.png"])

    def test_manual_storyboard_merge_and_split_preserve_order_and_reset_images(self) -> None:
        first = {
            "title": "门外",
            "source": "苏晚走到门口。",
            "narration": "她来到门口。",
            "characters": ["苏晚"],
            "scene": "走廊",
            "prompt": "苏晚抬手",
            "local_path": "first.png",
            "image_url": "https://example.com/first.png",
        }
        second = {
            "title": "开门",
            "source": "林川从门后出现。",
            "narration": "门后站着林川。",
            "characters": ["林川", "苏晚"],
            "scene": "房间",
            "prompt": "林川皱眉",
            "local_path": "second.png",
        }
        merged = merge_storyboard_shots(first, second)
        self.assertEqual(merged["source"], "苏晚走到门口。林川从门后出现。")
        self.assertEqual(merged["characters"], ["苏晚", "林川"])
        self.assertEqual(merged["scene"], "")
        self.assertEqual(merged["local_path"], "")
        self.assertEqual(merged["status"], "待重新生成")

        upper, lower = split_storyboard_shot(merged, len("苏晚走到门口。"))
        self.assertEqual(upper["source"], "苏晚走到门口。")
        self.assertEqual(lower["source"], "林川从门后出现。")
        self.assertEqual(upper["characters"], ["苏晚", "林川"])
        self.assertEqual(lower["characters"], ["苏晚", "林川"])
        self.assertEqual(upper["status"], "待重新生成")
        with self.assertRaises(ValueError):
            split_storyboard_shot(merged, 0)

    def test_character_and_scene_asset_pack_roundtrip_copies_local_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            role_image = root / "role.png"
            scene_image = root / "scene.webp"
            role_image.write_bytes(b"\x89PNG\r\n\x1a\nrole")
            scene_image.write_bytes(b"RIFFxxxxWEBPscene")
            package = root / "assets.zip"
            summary = export_comic_asset_pack(
                package,
                characters=[{"name": "林川", "description": "黑发", "prompt": "角色提示", "local_path": str(role_image)}],
                scenes=[{"name": "旧书店", "description": "木门在左", "prompt": "场景提示", "local_path": str(scene_image)}],
                metadata={"project_name": "测试漫画"},
            )
            self.assertEqual(summary, {"characters": 1, "scenes": 1, "references": 2})
            imported = import_comic_asset_pack(package, root / "imported")
            self.assertEqual(imported["characters"][0]["name"], "林川")
            self.assertEqual(imported["scenes"][0]["name"], "旧书店")
            self.assertEqual(imported["metadata"]["project_name"], "测试漫画")
            self.assertTrue(has_local_reference(imported["characters"][0]))
            self.assertTrue(has_local_reference(imported["scenes"][0]))
            self.assertNotEqual(imported["characters"][0]["local_path"], str(role_image))
            self.assertEqual(Path(imported["characters"][0]["local_path"]).read_bytes(), role_image.read_bytes())

    def test_asset_pack_rejects_unsafe_reference_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "unsafe.zip"
            manifest = {
                "format": "relax-creator-studio/comic-assets",
                "version": 1,
                "characters": [{"name": "林川", "description": "", "prompt": "", "reference": "../outside.png"}],
                "scenes": [],
            }
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                archive.writestr("../outside.png", b"unsafe")
            with self.assertRaisesRegex(ComicEngineError, "不安全"):
                import_comic_asset_pack(package, root / "imported")
            self.assertFalse((root / "outside.png").exists())

    def test_seedream_generation_request_matches_official_schema(self) -> None:
        client = DoubaoSeedreamClient(SeedreamConfig("ark-key"))
        response = _FakeResponse(
            {
                "model": "doubao-seedream-5-0-pro-260628",
                "created": 1786248000,
                "data": [{"url": "https://example.com/generated.png"}],
            }
        )
        references = ["data:image/png;base64,AAAA", "https://example.com/role-2.png"]
        with patch("core.seedream_client.urllib.request.urlopen", return_value=response) as urlopen:
            result = client.generate_image("雨夜街道，画面宽高比为 9:16", images=references, size="2K")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://ark.cn-beijing.volces.com/api/v3/images/generations")
        self.assertEqual(request.get_header("Authorization"), "Bearer ark-key")
        self.assertEqual(payload["model"], "doubao-seedream-5-0-pro-260628")
        self.assertEqual(payload["image"], references)
        self.assertEqual(payload["size"], "2K")
        self.assertEqual(payload["response_format"], "url")
        self.assertEqual(payload["output_format"], "png")
        self.assertFalse(payload["watermark"])
        self.assertEqual(payload["optimize_prompt_options"], {"mode": "standard"})
        self.assertEqual(result["imageUrl"], "https://example.com/generated.png")

    def test_seedream_connection_check_is_non_generating(self) -> None:
        client = DoubaoSeedreamClient(SeedreamConfig("ark-key"))
        with patch("core.seedream_client.urllib.request.urlopen", return_value=_FakeResponse({"data": []})) as urlopen:
            self.assertEqual(client.check_connection(), {"data": []})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://ark.cn-beijing.volces.com/api/v3/models")
        self.assertEqual(request.method, "GET")
        self.assertIsNone(request.data)


class ComicVideoEngineTests(unittest.TestCase):
    def test_subtitle_timeline_controls_shot_durations(self) -> None:
        cues = parse_srt_text(
            "1\n00:00:00,000 --> 00:00:02,000\n第一句\n\n"
            "2\n00:00:02,000 --> 00:00:05,000\n第二句\n"
        )
        self.assertEqual(len(cues), 2)
        self.assertEqual(allocate_shot_durations(5.0, 2, cues), [2.0, 3.0])

    def test_storyboard_source_text_aligns_images_to_spoken_plot(self) -> None:
        cues = parse_srt_text(
            "1\n00:00:00,000 --> 00:00:01,000\n开头\n\n"
            "2\n00:00:01,000 --> 00:00:05,000\n中间内容很多\n\n"
            "3\n00:00:05,000 --> 00:00:09,000\n结尾\n"
        )
        durations = allocate_shot_durations(
            9.0,
            3,
            cues,
            ["开头，中间", "内容很多。", "结尾"],
        )
        self.assertAlmostEqual(durations[0], 1.0 + 4.0 * 2 / 6)
        self.assertAlmostEqual(sum(durations[:2]), 5.0)
        self.assertAlmostEqual(durations[2], 4.0)

    def test_storyboard_text_length_controls_timing_without_subtitles(self) -> None:
        durations = allocate_shot_durations(12.0, 3, shot_texts=["短", "中等", "这是更长的一段"])
        self.assertAlmostEqual(sum(durations), 12.0)
        self.assertLess(durations[0], durations[1])
        self.assertLess(durations[1], durations[2])

    def test_comic_video_command_adds_motion_audio_and_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images = [root / "shot1.png", root / "shot2.png"]
            for image in images:
                image.write_bytes(b"image")
            audio = root / "voice.mp3"
            audio.write_bytes(b"audio")
            subtitles = root / "voice.srt"
            subtitles.write_text("1\n00:00:00,000 --> 00:00:02,000\n字幕\n", encoding="utf-8")
            command = build_comic_video_command(
                [str(item) for item in images],
                [2.0, 3.0],
                audio_path=str(audio),
                subtitles_path=str(subtitles),
                output_path=str(root / "out.mp4"),
            )
        joined = " ".join(command)
        self.assertEqual(command.count("-i"), 3)
        self.assertNotIn("-loop", command)
        self.assertIn("zoompan=", joined)
        self.assertIn("(ih-ih/zoom)*on/", joined)
        self.assertIn("(ih-ih/zoom)*(1-on/", joined)
        self.assertNotIn("zoom+", joined)
        self.assertIn("subtitles=filename=", joined)
        self.assertIn("atrim=duration=5.000", joined)
        self.assertIn("libx264", command)

    def test_no_keyframe_mode_keeps_static_image_centered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "shot.png"
            audio = root / "voice.mp3"
            image.write_bytes(b"image")
            audio.write_bytes(b"audio")
            command = build_comic_video_command(
                [str(image)],
                [2.0],
                audio_path=str(audio),
                motion_mode="无关键帧",
                output_path=str(root / "out.mp4"),
            )
        joined = " ".join(command)
        self.assertIn("zoompan=z='1.0':x='0':y='0'", joined)

    def test_find_executable_supports_bundled_vendor_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = root / "vendor" / "ffmpeg" / "bin" / ("ffmpeg.exe" if __import__("sys").platform == "win32" else "ffmpeg")
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"placeholder")
            with patch("core.video_engine.shutil.which", return_value=None), patch(
                "core.video_engine.__file__", str(root / "core" / "video_engine.py")
            ):
                self.assertEqual(find_executable("", "ffmpeg"), str(executable))


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
            self.assertIn("setpts=(PTS-STARTPTS)/1.750000", joined)
            self.assertIn("xfade=transition=fade", joined)
            self.assertIn("volume=0.28", joined)
            self.assertAlmostEqual(project.output_duration, 5.0 / 1.75 + 6.0 / 1.75 - 0.5)

    def test_default_playback_speed_shortens_visual_timeline(self) -> None:
        project = VideoProject(
            clips=[VideoClip("a.mp4", duration=15.0)],
            transition="none",
        )
        self.assertEqual(project.playback_speed, 1.75)
        self.assertAlmostEqual(project.output_duration, 15.0 / 1.75)

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
            self.assertIn("setpts=(PTS-STARTPTS)/1.750000", joined)
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
            project = new_comic_project("测试项目")
            state["projects"] = [project]
            state["active_project_id"] = project["project_id"]
            state["comic"] = project
            state["settings"]["api_key"] = "secret"
            state["settings"]["ark_api_key"] = "ark-secret"
            state["settings"]["yunwu_base_url"] = "https://legacy.example"
            state["comic"]["bot_type"] = "NIJI_JOURNEY"
            state["comic"]["upscale_index"] = 3
            store.save(state)
            raw = store.path.read_text(encoding="utf-8")
            self.assertNotIn("secret", raw)
            self.assertNotIn("ark-secret", raw)
            self.assertEqual(store.load()["comic"]["project_name"], "测试项目")
            self.assertTrue(store.load()["settings"]["remember_api_key"])
            self.assertIn("comic", store.load())
            self.assertNotIn("yunwu_base_url", store.load()["settings"])
            self.assertNotIn("bot_type", store.load()["comic"])
            self.assertNotIn("upscale_index", store.load()["comic"])
            self.assertNotIn("video", store.load())
            self.assertIn("novel", store.load())
            self.assertEqual(store.load()["novel"]["mode"], "深度改写")

    def test_multiple_projects_share_one_character_library(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            state = json.loads(json.dumps(DEFAULT_STATE))
            first = new_comic_project("第一条推文")
            second = new_comic_project("第二条推文")
            state["projects"] = [first, second]
            state["active_project_id"] = second["project_id"]
            state["shared_characters"] = [{"name": "林川", "description": "黑发", "prompt": "林川正面全身定妆，纯色背景"}]
            state["comic"] = second
            store.save(state)
            loaded = store.load()
            self.assertEqual(len(loaded["projects"]), 2)
            self.assertEqual(loaded["comic"]["project_name"], "第二条推文")
            self.assertIs(loaded["comic"]["characters"], loaded["shared_characters"])
            self.assertIs(loaded["projects"][0]["characters"], loaded["shared_characters"])
            self.assertEqual(loaded["projects"][1]["characters"][0]["name"], "林川")
            self.assertEqual(loaded["shared_characters"][0]["prompt"], "林川正面全身定妆，纯色背景")

    def test_legacy_single_comic_is_migrated_to_project_and_shared_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            store.base_dir.mkdir(parents=True, exist_ok=True)
            store.path.write_text(
                json.dumps(
                    {
                        "settings": {},
                        "comic": {
                            "project_name": "旧版推文",
                            "source_text": "旧正文",
                            "characters": [{"name": "苏晚", "description": "白发"}],
                            "scenes": [],
                            "shots": [],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            loaded = store.load()
            self.assertEqual(len(loaded["projects"]), 1)
            self.assertEqual(loaded["comic"]["project_name"], "旧版推文")
            self.assertEqual(loaded["shared_characters"][0]["name"], "苏晚")

    def test_orphaned_character_and_scene_images_are_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            role_dir = base / "shared_assets" / "characters"
            scene_dir = base / "project-output" / "scenes"
            role_dir.mkdir(parents=True)
            scene_dir.mkdir(parents=True)
            role_reference = role_dir / "苏晚_reference.png"
            role_candidate = role_dir / "苏晚_candidate.png"
            scene_reference = scene_dir / "客厅_reference.png"
            role_reference.write_bytes(b"reference")
            role_candidate.write_bytes(b"candidate")
            scene_reference.write_bytes(b"scene")
            store = StateStore(base)
            state = json.loads(json.dumps(DEFAULT_STATE))
            project = new_comic_project("恢复测试")
            project["output_dir"] = str(base / "project-output")
            state["projects"] = [project]
            state["active_project_id"] = project["project_id"]
            state["comic"] = project
            store.save(state)

            loaded = store.load()
            role = next(item for item in loaded["shared_characters"] if item["name"] == "苏晚")
            scene = next(item for item in loaded["comic"]["scenes"] if item["name"] == "客厅")
            self.assertEqual(role["status"], "定妆已确认")
            self.assertEqual(role["local_path"], str(role_reference))
            self.assertEqual(role["candidate_path"], str(role_candidate))
            self.assertEqual(scene["status"], "定景已确认")
            self.assertEqual(scene["local_path"], str(scene_reference))

    def test_state_backups_and_single_instance_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            first = StateStore(base)
            second = StateStore(base)
            self.assertTrue(first.acquire_instance_lock())
            self.assertFalse(second.acquire_instance_lock())
            first.release_instance_lock()
            self.assertTrue(second.acquire_instance_lock())
            second.release_instance_lock()
            state = json.loads(json.dumps(DEFAULT_STATE))
            first.save(state)
            first.save(state)
            self.assertTrue(any((base / "backups").glob("state-*.json")))

    def test_legacy_assets_are_copied_into_the_new_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old_base = root / "RelaxCreatorStudio"
            new_base = root / "ComicPostStudio"
            old_role = old_base / "shared_assets" / "characters" / "苏晚_reference.png"
            old_scene = old_base / "comic_projects" / "旧项目" / "scenes" / "客厅_reference.png"
            old_role.parent.mkdir(parents=True)
            old_scene.parent.mkdir(parents=True)
            old_role.write_bytes(b"role")
            old_scene.write_bytes(b"scene")
            old_state = {
                "settings": {},
                "comic": {
                    "project_name": "旧项目",
                    "output_dir": str(old_base / "comic_projects" / "旧项目"),
                    "characters": [],
                    "scenes": [],
                    "shots": [],
                },
            }
            (old_base / "state.json").write_text(json.dumps(old_state, ensure_ascii=False), encoding="utf-8")
            store = StateStore(new_base)
            store.legacy_base_dir = old_base
            loaded = store.load()
            role = next(item for item in loaded["shared_characters"] if item["name"] == "苏晚")
            scene = next(item for item in loaded["comic"]["scenes"] if item["name"] == "客厅")
            self.assertTrue(Path(role["local_path"]).is_relative_to(new_base))
            self.assertTrue(Path(scene["local_path"]).is_relative_to(new_base))
            self.assertTrue(Path(role["local_path"]).is_file())
            self.assertTrue(Path(scene["local_path"]).is_file())


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
    def test_lightweight_launcher_passes_generated_video_to_jianying(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = root / "JianyingPro.exe"
            video = root / "静态漫.mp4"
            executable.write_bytes(b"exe")
            video.write_bytes(b"video")
            self.assertEqual(detect_jianying_launcher(str(executable)), str(executable))
            with patch("core.jianying_launcher.subprocess.Popen") as popen:
                open_jianying_launcher(str(executable), str(video))
            command = popen.call_args.args[0]
            self.assertEqual(command, [str(executable), str(video)])

    def test_imported_video_audio_is_muted(self) -> None:
        self.assertEqual(SOURCE_VIDEO_VOLUME, 0.0)

    def test_comic_draft_keeps_photos_and_vertical_keyframes_editable(self) -> None:
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFElEQVR4nGP8z8DAwMDAxMDAwMAAAAwAAf4BqSAAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images = [root / "01.png", root / "02.png"]
            for image in images:
                image.write_bytes(png)
            audio = root / "voice.wav"
            with wave.open(str(audio), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(8000)
                handle.writeframes(b"\0\0" * 8000)
            progress: list[float] = []
            result = create_comic_jianying_draft(
                [str(path) for path in images],
                [0.4, 0.6],
                audio_path=str(audio),
                drafts_root=str(root),
                requested_name="静态漫测试",
                motion_mode="上下交替关键帧",
                on_progress=lambda value, _detail: progress.append(value),
            )
            content = json.loads((Path(result.path) / "draft_content.json").read_text(encoding="utf-8"))
            video_track = next(track for track in content["tracks"] if track["name"] == "静态漫画")
            self.assertEqual(len(video_track["segments"]), 2)
            for segment in video_track["segments"]:
                position = next(item for item in segment["common_keyframes"] if item["property_type"] == "KFTypePositionY")
                self.assertEqual(len(position["keyframe_list"]), 2)
            self.assertEqual(progress[-1], 1.0)
            self.assertAlmostEqual(result.duration_seconds, 1.0, places=2)

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
from __future__ import annotations

import json
import base64
import subprocess
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.ai_client import (
    AIConfig,
    OpenAICompatibleClient,
    api_key_from_environment,
    infer_provider,
    provider_preset,
)
from core.comic_engine import (
    ComicEngineError,
    batch_story_segments,
    build_ai_split_storyboard_prompt,
    build_character_prompt,
    build_scene_prompt,
    build_storyboard_batch_prompt,
    compose_shot_prompt,
    export_comic_asset_pack,
    fallback_storyboard,
    has_local_reference,
    import_comic_asset_pack,
    numbered_story_segments,
    parse_storyboard_response,
    merge_storyboard_shots,
    replace_character_in_shots,
    replace_scene_in_shots,
    scene_reference_data,
    split_story_segments,
    split_storyboard_shot,
    split_story_source_chunks,
    validate_ai_storyboard_split,
    validate_storyboard_batch,
)
from core.seedream_client import DoubaoSeedreamClient, SeedreamConfig
from core.comic_video_engine import allocate_shot_durations, build_comic_video_command, parse_srt_text
from core.jianying_launcher import detect_jianying_executable as detect_jianying_launcher, open_jianying as open_jianying_launcher
from core.novel_engine import build_post_prompt, build_rewrite_prompt, chapter_records, split_chapters
from core.jianying_engine import (
    SOURCE_VIDEO_VOLUME,
    clamp_srt_text,
    create_comic_jianying_draft,
    detect_jianying_drafts_path,
    detect_jianying_executable,
    open_jianying,
    sanitize_draft_name,
    unique_draft_name,
)
from core.storage import DEFAULT_STATE, StateStore, new_comic_project
from core.secret_store import SecretStoreError, delete_api_key, load_api_key, save_api_key
from core.video_engine import VideoClip, VideoProject, build_export_command, find_executable, fit_clips_to_duration


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
        response = _FakeResponse({"choices": [{"message": {"content": "餈��"}}]})
        with patch("core.ai_client.urllib.request.urlopen", return_value=response) as urlopen:
            result = client.complete("蝟餌��內", "�冽�內", temperature=0.2)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.moonshot.cn/v1/chat/completions")
        self.assertEqual(payload["model"], "kimi-k3")
        self.assertEqual(payload["messages"][1]["content"], "�冽�內")
        self.assertNotIn("temperature", payload)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(result, "餈��")

    def test_non_kimi_provider_keeps_requested_temperature(self) -> None:
        config = AIConfig("https://api.deepseek.com", "deepseek-v4-flash", "test-key", provider="deepseek")
        client = OpenAICompatibleClient(config)
        response = _FakeResponse({"choices": [{"message": {"content": "摰�"}}]})
        with patch("core.ai_client.urllib.request.urlopen", return_value=response) as urlopen:
            client.complete("蝟餌��內", "�冽�內", temperature=0.72)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["temperature"], 0.72)


class NovelEngineTests(unittest.TestCase):
    def test_split_chinese_chapters(self) -> None:
        text = "摨��捆\n\n蝚砌�蝡� �賊�\n\n餈蝚砌�蝡n\n蝚砌�蝡� 頧祆�\n\n餈蝚砌�蝡�"
        chapters = split_chapters(text)
        self.assertEqual([chapter.title for chapter in chapters], ["摨�", "蝚砌�蝡� �賊�", "蝚砌�蝡� 頧祆�"])
        self.assertIn("蝚砌�蝡�", chapters[1].content)

    def test_fallback_chunking(self) -> None:
        text = "蝚砌�畾萄�摰嫘n\n蝚砌�畾萄�摰孵��踴n\n蝚砌�畾萄�摰嫘�"
        chapters = split_chapters(text, fallback_size=12)
        self.assertGreaterEqual(len(chapters), 2)
        self.assertEqual(chapters[0].title, "�挾 1")

    def test_directly_pasted_text_becomes_editable_chapters(self) -> None:
        records = chapter_records("蝚砌�蝡� �賊�\n\n憟孵�典��典��具n\n蝚砌�蝡� 頧祆�\n\n�舐��嗥��准�")
        self.assertEqual([item["title"] for item in records], ["蝚砌�蝡� �賊�", "蝚砌�蝡� 頧祆�"])
        self.assertEqual(records[0]["content"], "憟孵�典��典��具�")

    def test_rewrite_prompt_contains_rules_and_text(self) -> None:
        system, user = build_rewrite_prompt(
            "蝚砌�蝡�",
            "��甇��",
            mode="瘛勗漲�孵�",
            style="��蝝批�",
            perspective="蝚砌�鈭箇妍",
            target_length="銝��餈�",
            custom_rules="��銝��",
            story_bible="銝餉��急�撌�",
        )
        self.assertIn("撠秩蝻�", system)
        for expected in ("��甇��", "��銝��", "銝餉��急�撌�", "蝚砌�鈭箇妍"):
            self.assertIn(expected, user)

    def test_post_prompt_uses_metadata(self) -> None:
        _system, user = build_post_prompt("�典��賢��", "瘝餅�", "撠滯銋�", ["���", "�渡��唳秤"], 28)
        self.assertIn("撠滯銋�", user)
        self.assertIn("28", user)
        self.assertIn("���", user)


class ComicEngineTests(unittest.TestCase):
    def test_transport_chunks_do_not_define_storyboard_length(self) -> None:
        text = "".join(f"蝚洌index}�亙�餈�" for index in range(1, 501))
        chunks = split_story_source_chunks(text, max_chars=900)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(item) <= 900 for item in chunks))
        self.assertEqual("".join(item.replace("\n", "") for item in chunks), text)

    def test_ai_split_prompt_and_validation_leave_boundaries_to_model(self) -> None:
        source = "���典��具���憭氬霂��嗅�鈭�"
        _system, user = build_ai_split_storyboard_prompt(
            source,
            art_style="�唬誨�賢��拇憤",
            existing_characters=[{"name": "��", "description": "暺�"}],
            generation_mode="shots",
            batch_index=1,
            batch_total=2,
        )
        self.assertIn("銝��箏�摮", user)
        self.assertIn("�曹��喳� shots �圈�", user)
        self.assertIn("���迫�曄��賢摰霈脫�", user)
        self.assertIn("銝���閬�蝏剜�暹��賜�閫���其�餈��憬��撘", user)
        self.assertIn("銝銝芾”��銝銝芸雿�", user)
        self.assertIn("�批�� 8嚚�30 銝芣�摮�", user)
        self.assertIn("蝳迫�冽��宏����", user)
        shots = validate_ai_storyboard_split(
            [
                {"source": "���典��具�", "title": "�券"},
                {"source": "���仍�霂��嗅�鈭�", "title": "�亦"},
            ],
            source,
            start_index=4,
        )
        self.assertEqual([item["segment_id"] for item in shots], ["S00004", "S00005"])
        with self.assertRaisesRegex(ComicEngineError, "�芸��渲���"):
            validate_ai_storyboard_split([{"source": "���典��具�"}], source)

    def test_story_segments_keep_text_and_target_readable_shots(self) -> None:
        text = "���典��具瘞湧◇�憭�皛渲�n\n撅�瘝⊥�撘�胯����函��n\n�菔�蝒����"
        segments = split_story_segments(text, target_chars=24)
        self.assertGreaterEqual(len(segments), 2)
        self.assertEqual("".join(item.replace("\n", "") for item in segments), text.replace("\n", ""))

    def test_numbered_segments_are_batched_without_omission(self) -> None:
        text = "".join(f"蝚洌index}�亙�餈�" for index in range(1, 401))
        segments = numbered_story_segments(text, target_chars=120)
        batches = batch_story_segments(segments, max_chars=900)
        flattened = [item for batch in batches for item in batch]
        self.assertGreater(len(batches), 1)
        self.assertEqual([item["segment_id"] for item in flattened], [f"S{index:05d}" for index in range(1, len(segments) + 1)])
        self.assertEqual("".join(item["source"].replace("\n", "") for item in flattened), text)

    def test_storyboard_only_prompt_requires_one_result_per_segment(self) -> None:
        segments = [
            {"segment_id": "S00001", "source": "���典��具�"},
            {"segment_id": "S00002", "source": "��蝡蝒���"},
        ]
        _system, user = build_storyboard_batch_prompt(
            segments,
            art_style="�唬誨�賢��拇憤",
            existing_characters=[{"name": "��", "description": "暺�"}],
            generation_mode="shots",
            batch_index=2,
            batch_total=4,
        )
        self.assertIn("�芰�����", user)
        self.assertIn("shots 敹◆�啣末餈� 2 憿�", user)
        self.assertIn("S00001�00002", user)
        self.assertIn("characters 敹◆餈�蝛箸蝏�", user)
        self.assertIn("scenes 敹◆餈�蝛箸蝏�", user)

    def test_all_prompt_builds_fixed_scene_library_and_binding(self) -> None:
        _system, user = build_storyboard_batch_prompt(
            [{"segment_id": "S00001", "source": "��韏啗��找髡摨�"}],
            art_style="�唬誨�賢��拇憤",
            existing_characters=[{"name": "��", "description": "暺�"}],
            existing_scenes=[{"name": "�找髡摨�", "description": "�券�典椰嚗��啣��"}],
            generation_mode="all",
            batch_index=1,
            batch_total=1,
        )
        self.assertIn("撌脫��箏��箸", user)
        self.assertIn("�找髡摨��券�典椰嚗��啣��", user)
        self.assertIn("鈭箇 prompt �芾�膩閫�祈澈銝滲�脰���", user)
        self.assertIn("��胯�撱箇���摰嗅", user)
        self.assertIn('"scenes"', user)
        self.assertIn('"scene": "�箏��箸��"', user)

    def test_storyboard_batch_validation_orders_repairs_source_and_rejects_missing_shots(self) -> None:
        expected = [
            {"segment_id": "S00001", "source": "蝚砌�畾萸�"},
            {"segment_id": "S00002", "source": "蝚砌�畾萸�"},
        ]
        ordered = validate_storyboard_batch(
            [
                {"segment_id": "S00002", "source": "蝚砌�畾萸�"},
                {"segment_id": "S00001", "source": "蝚砌�畾萸�"},
            ],
            expected,
        )
        self.assertEqual([item["segment_id"] for item in ordered], ["S00001", "S00002"])
        repaired = validate_storyboard_batch(
            [
                {"segment_id": "S1", "source": "璅∪��孵�鈭洵銝畾�"},
                {"segment_id": "S2", "source": "蝚砌�"},
            ],
            expected,
        )
        self.assertEqual([item["source"] for item in repaired], ["蝚砌�畾萸�", "蝚砌�畾萸�"])
        positional = validate_storyboard_batch([{"source": "��"}, {"source": "銋�"}], expected)
        self.assertEqual([item["segment_id"] for item in positional], ["S00001", "S00002"])
        with self.assertRaisesRegex(ComicEngineError, "蝻箏�"):
            validate_storyboard_batch(
                [
                    {"segment_id": "S00001", "source": "蝚砌�畾萸�"},
                ],
                expected,
            )

    def test_parse_storyboard_json_creates_editable_records(self) -> None:
        raw = """```json
        {"characters":[{"name":"��","description":"暺�嚗��脤�銵�"}],"scenes":[{"name":"�找髡摨�","description":"�券�典椰嚗��啣��"}],"shots":[{"title":"�典�","source":"���券","narration":"�典�鈭�","characters":["��"],"scene":"�找髡摨�","prompt":"�典��券嚗葉��"}]}
        ```"""
        result = parse_storyboard_response(raw, art_style="�亦頂�冽憤")
        self.assertEqual(result["characters"][0]["name"], "��")
        self.assertIn("銝��箇隞颱�摰文�憭��", result["characters"][0]["prompt"])
        self.assertIn("�芸�啗砲閫銝鈭�", result["characters"][0]["prompt"])
        self.assertEqual(result["scenes"][0]["name"], "�找髡摨�")
        self.assertIn("�犖��", result["scenes"][0]["prompt"])
        self.assertIn("��", result["shots"][0]["characters"])
        self.assertEqual(result["shots"][0]["scene"], "�找髡摨�")
        self.assertEqual(result["shots"][0]["status"], "敺���")
        self.assertEqual(result["shots"][0]["prompt"], "�典��券嚗葉��")

    def test_dynamic_camera_storyboard_is_rejected_for_static_comic(self) -> None:
        raw = '{"characters":[],"scenes":[],"shots":[{"source":"��餈��","prompt":"�仍頝���韏啗��輸"}]}'
        with self.assertRaisesRegex(ComicEngineError, "�冽���"):
            parse_storyboard_response(raw, art_style="�唬誨�賢��拇憤", generation_mode="shots")

    def test_fallback_storyboard_links_named_characters(self) -> None:
        characters = [{"name": "��", "description": "�質�踹�"}]
        shots = fallback_storyboard("��蝡蝵4�k滴萇@膝�僱檜�(�奩犮�}�怴�螫�蔣���(膝�奩犮�}�怴�螫�蔣���(刓��Y�A刓��(�悼賻鰅Y�
悼嚏迕晱�慾��悒螂蠙堋壎t�(膝�}��攛捑≧膝�什(�恚�悒螂蠙幫(�嗶由末路蝴��(�(膝����忝斥�掍�}謗奷}紫賽蔗�訄����迕刓請�桸僱檗中(�奷接談�纓℅刓�厭桻轅恚�悒螂�饑蛻�(�奷%�蹊泔，QL然QIQAQL兮譫嗩擬擬膝�(�奷%��挸打�桾末路饑蛻擬膝�(�奷%�犮渦�悒螂蠙擬��忝旦��((��恚�}搘恁調恚挸穸悒螂﹠湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(調���A�耋���悼戴耋�(調�楦由�怴�螫�蔣���(刓��Y�A刓��悼賻鰅Y�
悼嚏迕芺桾�什�桾末路唹t�挸穸悒螂�厭�(�蔥���弗謗奷}紫賽蔗�訄����迕A�耋��桸僱檗�(�奷%��蔥���(�奷9諸%����忝斥�蔥��中(()�M挼��Q捈＜馴旄邿Q�
�(��恚刓桯犮艱�}生}�善����湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(挼��M��M挼�，�耋中(���怛螂僚�〝芺號聒怴U1Q}MQQ中(刓���搠等賽蔗����/ⅥW��n��(��l刓��t�m賽蔗�帨(��l�矛賽蔗�恚��賽蔗�峬刓�}�(��l�蔥��賽蔗��(��l悒��ul�薔}��t����(��l悒��ul�伬}囚��t��优腴���(��l悒��ul桯楙}��}桾�t�旄賻頛蔣��銋螫�(��l�蔥�l�諸}栫���9%)%})=UI9d�(��l�蔥�l趨��}旦��t��(挼�嗶迕(�迕褕��嘖��慛���旦�毯(�奷9諸%������雂(�奷9諸%��优腴�����雂(�奷纓﹠挼�僚��召�蔥�l刓�}�����/ⅥW��n��(�奷Q尥迕褕掃�l悒��ul�扐囚��t�(�奷%��蔥�挼�僚��中(�奷9諸%�桯楙}��}桾��迕褕掃�l悒��t�(�奷9諸%��諸}栫�挼�僚��召�蔥��(�奷9諸%�趨��}旦���迕褕掃�l�蔥��(�奷9諸%��挼�僚��中(�奷%�誶挼�僚��中(�奷纓﹠挼�僚��召誶l��t��痯�R�d((��恚菅敗民�}賽蔗�敊}矷蝴��扐悼銦���湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(挼��M��M挼�，�耋中(���怛螂僚�〝芺號聒怴U1Q}MQQ中(�玉迖��搠等賽蔗���眾�:�Z(�蝴���搠等賽蔗���眾3�:�Z(��l刓��t�m奼訄�蝴(��l�矛賽蔗�恚����l刓�}�(��l����妀�m����z_t�民悒螂薎�>D刓聒���z_wv�ㄑ穄k�橢3縈&芃3�鰗(��l�蔥����(挼�嗶迕(����迕褕掃�(�奷纓§§��刓��t什(�奷纓§���蔥�l刓�}����眾3�:�Z(�奷%怴掃��l�蔥�l����t�掃��l����妀�(�奷%怴掃��l刓��ul臂l����t�掃��l����妀�(�奷纓§��刓��ul攤l����ul臂l�t��z_t(�奷纓§������妀l臂l刓聒�t��z_wv�ㄑ穄k�橢3縈&芃3��((��恚��}穸��}等生}等�}挼}賽蔗�恚矷�}刓�怴���湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(挼��M��M挼�，�耋中(挼��筏��塾沭Q尥�嵽迕}蓮顟尥(挼�嘗楦由�慛�(芺號聒�((悒���薀�(�蔥�(刓�}���^�&#�:�Z(調��慛�^��(�����m���.?h�民悒螂�f�>Dt�(����mt�(■捈t�(�(�(�嗶桾火��(什(���旦�毯(�(����迕褕掃�(�奷纓§§��刓��t什臚(�奷纓§���蔥�l刓�}����^�&#�:�Z(�奷纓§������妀l臂l�t�.?h((��恚褕薄�}��扐�扔�}������湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(���A�耋�(蔣��������捈������(��}�����刓�善桻轅�����(蔣筏��塾沭Q尥(��}筏��塾沭Q尥(蔣������刓�}��.?i}����褸�(蔣�����刓�}��.?i}���褸�(��}������������:}����褸�(蔣����楦由�怴�������(蔣���楦由�怴������(��}����楦由�怴����(挼��M��M挼��(���怛螂僚�〝芺號聒怴U1Q}MQQ中(刓���搠等賽蔗�����7/ⅥT(刓�l桻轅恚�t�迕���刓�善桻轅��(��l刓��t�m賽蔗�帨(��l�矛賽蔗�恚��賽蔗�峬刓�}�(��l�蔥��賽蔗��(挼�嗶迕((����迕褕掃�(蔣���慛‘���由�掃��l����妀�由l�t鐀.?h(����慛‘���由�掃��l�蔥�l���t�由l�t鐀���:(�奷纓﹎蔣�晇�t��榭��Ⅱ��(�奷纓﹎蔣�}���迕刓�}�����(�奷纓﹎蔣���}���迕刓�}����(�奷纓﹠��l�晇�t��榭�痾�誥�(�奷纓﹠��l�}���迕������((��恚迕�桷矻穸��}旦迕�}掃﹠湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(���A�耋�(�玉迖�M��M挼��(�蝴��M��M挼��(�奷Q尥奼邿�纓玉旦迕�}掃�(�奷�﹠�蝴�纓玉旦迕�}掃�(�玉迖嘖��}旦迕�}掃(�奷Q尥���掍�}旦迕�}掃�(�蝴��旦迕�}掃(���怛螂僚�〝芺號聒怴U1Q}MQQ中(�玉迖嗶迕(�玉迖嗶迕(�奷Q尥������倘賻�掃���捶鼎芺�中�((��恚��}�敊}薔�}旦挼}恁�搠��}�褕銦���湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(蝸��A�耋�(�}���刓請�
�褕M晇�(}���刓請��
蔥�A談埒晇�(�}刓��蔣������捈�������.?i}����褸�(�}���蔣����蔥�}賽蔗�捈���^���������:}����褸�(�}刓�嘗邿筏��塾沭Q尥(�}���塵僱�玄℅捈顟尥(�}刓�奩犮�}�怴�刓�(�}�楦由�怴����(�}迕���(悒���薀�(�蔥�(刓�}���^��(桻轅恚��迕蔣����蔥�}賽蔗�捈���^�什(�����mt�(����mt�(■捈t�(�((■�}�����鼎芺�允楦由�慛〝芺號聒怴蔣迕�嗶桾火�什���旦�毯(挼��M��M挼�★}���(挼�僚��憢��}��蔣��(����迕褕掃�(蔣���慛‘���由�掃��l����妀�由l�t鐀.?h(����慛‘���由�掃��l�蔥�l���t�由l�t鐀���:(�奷Q尥A刓�l�}��允生}��悒�}捊★}��中(�奷Q尥A��}��允生}��悒�}捊★}��中(�奷Q尥A刓�l�}��允生}��(�奷Q尥A��}��允生}��(()�M��埒挼�Q捈＜馴旄邿Q�
�(��恚搘�豎矻刓桻}挼}�悒}������湍�9蝴(由�����褕�}迕褕俜拊螫伝斥昈�(由�����褕�}迕褕}搘�豎矻��桾飼�敕�筏�����(�奷纓§�囚��扔�什�筏(��奷}�控�}蝴�}搘��郊竣(由�����褕�}迕褕}搘�豎矻楦由��犮��(囚��扔���筏(犮��迋恚�控�}蝴�}搘��郊竣筏(由�����褕�}迕褕}搘�豎矻���������(�囚��扔��(��奷}�控�}蝴�}搘��郊竣((��恚�矻���旦}��}挸扔矻蝴勗}悼�}�活���湍�9蝴(梇��邯刓�迒�
蔥螫�A刓�迒《t�幫��鉾搘�腴���q����(由�����褕�}迕褕俜拊螫伝�搘�方(由�����褕�}迕褕}尥飼�犮栵桾飼�敕�邯教��桾由儰(�奷纓§�囚����趨��什��鉾搘�腴���(�桾由銋�奷}�控�}蝴�}搘��(�旦��犮�迋斒���
�褕M晇������趨����t(�((��恚旦�悼賽誶�}�}生}���﹠湍�9蝴(由����迋圂�怴M��埒挼�圪褓方(�囚��蜇桯��((��恚桯邯謠褕�螫伂}}厭恚��}��}迕褕����湍�9蝴(由�����褕�}迕褕俜拊螫伝旦淀�(�奷纓§�囚����什��(由����迋圂�怴M��埒挼�圪褓方(囚���������(()�)�孵旦��旦捈＜馴旄邿Q�
�(��恚悼栳恚�桯}�迋}����晱�罷挼}岩憟�﹠湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(蝸��A�耋�(�����刓請��)�孵旦冱���(��刓請�vg�牲耋�(���楦由�怴���(�奩犮�}�怴�晱���(�奷纓��恚岩憟�}�桯﹠捑���什捑����(由�����褕岩憟�}�桯嗶�賽�拊A蝌��謗�貉(�飼岩憟�}�桯﹠捑����迕晱�慾�(�蔥���謗�號�控}��玬膺(�奷纓�蔥���m迕�桻��什捑≧�另�((��恚扔謗奷�}晱�罷罷生}菅����湍�9蝴(�奷纓﹐=UI
}Y%=}Y=1U5�戲壑((��恚等�恚�矻薄諸談}�奷�}����矻�由��﹠湍�9蝴(����寪�寍���(Y	=I僋-9MU�U�%

%靻)鍾�EYH揤@摜��5�5�5�	鐸MUY=I,�
e%$�(�(由��聒�鄒謗�扤玉�挼扞���耋�(蝸��A�耋�(����m刓請�贏褸��刓請�褸�t(�褓���扔��(��奩犮�}�怴褸(��未�刓請�膝�奩�(由���厭�腹迕慾���(�揤�捺�(�敊鄹���(������擬壑(楦由���怴�p聯��鈶擬�(刓枔生峿�帨�mt(梇����}等岩憟�}��(迕�������扔�t�(戲訄戲敪�(��末}��攛捑��未什(��矻刓請攛捑﹎蝸苳�(�迕�}���宅g���/ⅥT(諸末飼善��+/�n�浀R��(飼賽��迒麙���敕��弘�賽��迒�謠�敕�(�(�蝴�塵�怛螂僚�A�邯教嘗���}塾邿怛螂嘖��慛���旦�毯�(�}挸����慛×��褓��塾峬��t�挸�衍�t鐀vg��R��(�奷纓§≧�}挸�衍��塾�t什(�褓��塵�晱�罷挸�衍��塾�t�(談由末���慛‘���由��峬�蔥善飼�����t�由l刓�奷憢栫�鐀�-Q斲談由末鉅(�奷纓§℅談由末雍悼迖什(�奷纓℅刓玬棠t�譫壑(�奷接談�纓﹎梇邿�悒蝴}��怜譫幫��泔((��恚�馴悒�}桯汀�}��﹠湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(�奷纓﹠由仿�恚���/ⅥT餈�6'���/ⅥU}�6'(，�耋���睧�&�允筏��(�奷纓＜馴纓�恚��×幫�睧�&�什�睧�&姥� 侗�$((��恚��桾�}�恚�}�矻賽末犮栵﹠湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(�奷纓��恚岩憟�}�敊}��×壑��耋�((��恚�矻艱��}生}���}蝌�﹠湍�9蝴(由��聒�鄒謗�扤玉�挼扞���耋�(�謠}���A�耋���&�b�槄k�& �謊�(�謠}��僱�玄(由�����褕岩憟�}�嗶樗嘗��褕���搘�方(�奷纓��恚岩憟�}�桻��﹠捑�謠}��中�迕艱��中(由�����褕岩憟�}�嗶�賽�拊A蝌��謗�貉(�飼岩憟�﹠捑�謠}��中(蝌�迋恚�控�}蝴�}搘�︼梠蜇蝌捑�謠}��另�談�沭Q尥((��恚邯由�矻�挼}罷�悒螂﹠湍�9蝴(調����(q蛻燮擬飺幫釋�斐�擬飺燮斂啪擱q�眾�>電�(q蛻燮擬飺擬�斐�擬飺燮殮偯擱q�眾3�>電�(q蛻燮擬飺堸擬�斐�擬飺燮濤偯擱q誥��諲2崀�(�(梇��艱虭恚�慛﹠調�矹舉擬壑(�奷%�燮擬飺怜���邯教�(�奷9諸%���諲2���邯教�(()�}��}|鐀}�旦}|(馴旄邿�斥(
