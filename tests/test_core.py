from __future__ import annotations

import json
import base64
import io
import subprocess
import tempfile
import unittest
import urllib.error
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
    COMIC_COVER_OUTPUT_PLAN,
    ComicEngineError,
    batch_story_segments,
    build_ai_split_storyboard_prompt,
    build_character_prompt,
    build_cover_prompt,
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
from core.seedream_client import (
    SEEDREAM_LITE_MODEL,
    SEEDREAM_PRO_MODEL,
    DoubaoSeedreamClient,
    SeedreamConfig,
)
from core.comic_video_engine import allocate_shot_durations, build_comic_video_command, parse_srt_text
from core.comic_presentation import DOUYIN_COMIC_MOTION, normalize_motion_mode
from core.jianying_launcher import detect_jianying_executable as detect_jianying_launcher, open_jianying as open_jianying_launcher
from core.novel_engine import (
    NOVEL_COMMENTARY_MODE,
    NOVEL_COMMENTARY_STYLE,
    build_post_prompt,
    build_rewrite_prompt,
    chapter_records,
    split_chapters,
)
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

    def test_commentary_mode_builds_suspenseful_voiceover_prompt_without_fake_plot(self) -> None:
        system, user = build_rewrite_prompt(
            "第一章",
            "林川推开房门，看见失踪三年的姐姐。",
            mode=NOVEL_COMMENTARY_MODE,
            style=NOVEL_COMMENTARY_STYLE,
            perspective="第三人称限知",
            target_length="与原文接近",
            custom_rules="名字不能改",
            story_bible="林川不知道姐姐失踪的真相",
        )
        self.assertIn("可直接配音", system)
        self.assertIn("不得为了制造悬念捏造", system)
        for expected in ("开头前两句", "每 2—4 句", "短句和中短句", "下一段钩子", "不提前泄露"):
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

    def test_cover_prompt_uses_title_references_style_and_close_framing(self) -> None:
        prompt = build_cover_prompt(
            "豪门千金归来",
            "苏晚冷静回头，眼神坚定",
            art_style="现代都市韩漫",
            aspect="3:4",
            character_name="苏晚",
            scene_name="豪宅客厅",
        )
        self.assertIn("中文封面标题“豪门千金归来”", prompt)
        self.assertIn("主要人物使用“苏晚”参考图", prompt)
        self.assertIn("环境使用“豪宅客厅”固定场景参考图", prompt)
        self.assertIn("中近景或近景为主", prompt)
        self.assertIn("禁止远景和大全景", prompt)
        self.assertIn("二维韩系网络漫画", prompt)
        self.assertIn("画面宽高比为 3:4", prompt)
        self.assertIn("水平方向居中", prompt)
        self.assertIn("中间偏下", prompt)
        self.assertIn("65%～75%", prompt)

    def test_cover_output_plan_has_two_images_per_required_aspect(self) -> None:
        self.assertEqual(COMIC_COVER_OUTPUT_PLAN, (("3:4", 1), ("3:4", 2), ("4:3", 1), ("4:3", 2)))
        self.assertEqual(sum(1 for aspect, _ordinal in COMIC_COVER_OUTPUT_PLAN if aspect == "3:4"), 2)
        self.assertEqual(sum(1 for aspect, _ordinal in COMIC_COVER_OUTPUT_PLAN if aspect == "4:3"), 2)

    def test_new_project_has_persistent_cover_record(self) -> None:
        project = new_comic_project("封面项目")
        self.assertEqual(project["cover"]["status"], "未生成")
        self.assertEqual(project["cover"]["local_path"], "")
        self.assertEqual(project["cover"]["prompt"], "")
        self.assertEqual(project["cover"]["images"], [])

    def test_linked_character_variant_changes_clothing_only(self) -> None:
        prompt = build_character_prompt(
            "苏晚·晚礼服",
            "黑色露肩晚礼服，银色耳坠",
            "现代都市韩漫",
            "苏晚·日常装",
        )
        self.assertIn("以输入参考图中的“苏晚·日常装”为唯一人物本体", prompt)
        self.assertIn("本次只按以下要求换装：黑色露肩晚礼服，银色耳坠", prompt)
        self.assertIn("只允许按照当前描述改变服装", prompt)
        self.assertIn("脸型、五官", prompt)
        self.assertIn("发型、发色", prompt)
        self.assertIn("体态和身体比例完全一致", prompt)
        self.assertEqual(prompt.count("换装关联硬性要求"), 1)
        self.assertEqual(prompt.count("人物参考图硬性要求"), 1)

    def test_korean_webtoon_style_expands_to_non_photorealistic_constraints(self) -> None:
        art_style = "现代都市韩漫，二维韩系网络漫画插画，精致高颜值人物，禁止真人照片与3D写实"
        character_prompt = build_character_prompt("苏晚", "黑色长发，白色衬衫", art_style)
        scene_prompt = build_scene_prompt("办公室", "落地窗与深色办公桌", art_style)
        shot_prompt = compose_shot_prompt(
            {"prompt": "苏晚皱眉回头", "characters": ["苏晚"], "scene": "办公室"},
            art_style=art_style,
            aspect="9:16",
            characters=[{"name": "苏晚"}],
            scenes=[{"name": "办公室"}],
        )
        _, storyboard_prompt = build_storyboard_batch_prompt(
            [{"segment_id": "S00001", "source": "苏晚回头。"}],
            art_style=art_style,
            existing_characters=[],
            existing_scenes=[],
            generation_mode="all",
            batch_index=1,
            batch_total=1,
        )
        for prompt in (character_prompt, scene_prompt, shot_prompt, storyboard_prompt):
            self.assertIn("二维韩系网络漫画", prompt)
            self.assertIn("禁止真人照片", prompt)
            self.assertIn("3D人物渲染", prompt)
        self.assertEqual(character_prompt.count("人物参考图硬性要求"), 1)
        self.assertEqual(character_prompt.count("明确采用二维韩系网络漫画"), 1)

    def test_storyboard_prompts_prioritize_medium_and_close_framing(self) -> None:
        shot_prompt = compose_shot_prompt(
            {"prompt": "苏晚惊讶回头", "characters": ["苏晚"], "scene": "办公室"},
            art_style="现代都市韩漫",
            aspect="9:16",
            characters=[{"name": "苏晚"}],
            scenes=[{"name": "办公室"}],
        )
        _, batch_prompt = build_storyboard_batch_prompt(
            [{"segment_id": "S00001", "source": "苏晚惊讶地回头。"}],
            art_style="现代都市韩漫",
            existing_characters=[{"name": "苏晚"}],
            existing_scenes=[{"name": "办公室"}],
            generation_mode="shots",
            batch_index=1,
            batch_total=1,
        )
        self.assertIn("中近景或近景为主", shot_prompt)
        self.assertIn("胸像、半身或腰部以上", shot_prompt)
        self.assertIn("55%～80%", shot_prompt)
        self.assertIn("不使用远景、大全景、超远景", shot_prompt)
        self.assertIn("中近景和近景为绝对主力", batch_prompt)
        self.assertIn("不采用远景、大全景、超远景", batch_prompt)

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
                characters=[{"name": "林川·西装", "base_character": "林川·日常装", "description": "黑色西装", "prompt": "角色提示", "local_path": str(role_image)}],
                scenes=[{"name": "旧书店", "description": "木门在左", "prompt": "场景提示", "local_path": str(scene_image)}],
                metadata={"project_name": "测试漫画"},
            )
            self.assertEqual(summary, {"characters": 1, "scenes": 1, "references": 2})
            imported = import_comic_asset_pack(package, root / "imported")
            self.assertEqual(imported["characters"][0]["name"], "林川·西装")
            self.assertEqual(imported["characters"][0]["base_character"], "林川·日常装")
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
                "model": "doubao-seedream-5-0-260128",
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
        self.assertEqual(payload["model"], "doubao-seedream-5-0-260128")
        self.assertEqual(payload["image"], references)
        self.assertEqual(payload["size"], "2k")
        self.assertEqual(payload["response_format"], "url")
        self.assertEqual(payload["output_format"], "png")
        self.assertFalse(payload["watermark"])
        self.assertEqual(payload["optimize_prompt_options"], {"mode": "standard"})
        self.assertEqual(result["imageUrl"], "https://example.com/generated.png")

    def test_seedream_lite_uses_same_image_api_with_lite_model_id(self) -> None:
        client = DoubaoSeedreamClient(SeedreamConfig("ark-key", model=SEEDREAM_LITE_MODEL))
        response = _FakeResponse({"model": SEEDREAM_LITE_MODEL, "data": [{"url": "https://example.com/lite.png"}]})
        with patch("core.seedream_client.urllib.request.urlopen", return_value=response) as urlopen:
            result = client.generate_image(
                "苏晚皱眉，中近景",
                images=["data:image/png;base64,AAAA"],
                size="3K",
            )
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["model"], "doubao-seedream-5-0-lite-260128")
        self.assertEqual(payload["size"], "3k")
        self.assertEqual(payload["image"], "data:image/png;base64,AAAA")
        self.assertEqual(result["model"], SEEDREAM_LITE_MODEL)
        self.assertEqual(SeedreamConfig("ark-key").model, SEEDREAM_PRO_MODEL)

    def test_seedream_lite_rejects_unsupported_4k_before_request(self) -> None:
        client = DoubaoSeedreamClient(SeedreamConfig("ark-key", model=SEEDREAM_LITE_MODEL))
        with patch("core.seedream_client.urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(ComicEngineError, "Lite 只支持 2K、3K"):
                client.generate_image("苏晚皱眉，中近景", size="4K")
        urlopen.assert_not_called()

    def test_seedream_pro_converts_1k_to_explicit_size_for_aspect(self) -> None:
        client = DoubaoSeedreamClient(SeedreamConfig("ark-key", model=SEEDREAM_PRO_MODEL))
        response = _FakeResponse({"model": SEEDREAM_PRO_MODEL, "data": [{"url": "https://example.com/pro-1k.png"}]})
        with patch("core.seedream_client.urllib.request.urlopen", return_value=response) as urlopen:
            client.generate_image("苏晚皱眉，中近景", size="1K", aspect="9:16")
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["size"], "1440x2560")

    def test_seedream_pro_1k_sizes_meet_documented_minimum_for_every_aspect(self) -> None:
        expected = {
            "9:16": "1440x2560",
            "4:5": "1728x2160",
            "3:4": "1680x2240",
            "1:1": "1920x1920",
            "4:3": "2240x1680",
            "16:9": "2560x1440",
        }
        for aspect, size in expected.items():
            with self.subTest(aspect=aspect):
                client = DoubaoSeedreamClient(SeedreamConfig("ark-key", model=SEEDREAM_PRO_MODEL))
                response = _FakeResponse({"model": SEEDREAM_PRO_MODEL, "data": [{"url": "https://example.com/pro-1k.png"}]})
                with patch("core.seedream_client.urllib.request.urlopen", return_value=response) as urlopen:
                    client.generate_image("中近景", size="1K", aspect=aspect)
                payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
                self.assertEqual(payload["size"], size)
                width, height = (int(part) for part in size.split("x"))
                self.assertGreaterEqual(width * height, 3_686_400)

    def test_seedream_rejects_custom_size_below_documented_minimum_before_request(self) -> None:
        client = DoubaoSeedreamClient(SeedreamConfig("ark-key", model=SEEDREAM_PRO_MODEL))
        with patch("core.seedream_client.urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(ComicEngineError, "至少需要 3686400 像素"):
                client.generate_image("中近景", size="1024x1824")
        urlopen.assert_not_called()

    def test_unactivated_seedream_model_error_is_actionable(self) -> None:
        client = DoubaoSeedreamClient(SeedreamConfig("ark-key", model=SEEDREAM_LITE_MODEL))
        payload = {"error": {"message": "Your account has not activated the model service in the Ark Console."}}
        response_error = urllib.error.HTTPError(
            "https://ark.cn-beijing.volces.com/api/v3/images/generations",
            404,
            "Not Found",
            {},
            io.BytesIO(json.dumps(payload).encode("utf-8")),
        )
        with patch("core.seedream_client.urllib.request.urlopen", side_effect=response_error):
            with self.assertRaisesRegex(ComicEngineError, "尚未开通 Seedream 5.0 Lite"):
                client.generate_image("苏晚回头")

    def test_seedream_size_error_is_translated_and_legacy_presets_are_rejected(self) -> None:
        client = DoubaoSeedreamClient(SeedreamConfig("ark-key"))
        payload = {
            "error": {
                "message": "The parameter `size` specified in the request is not valid: "
                "size must be one of 'WIDTHxHEIGHT', '2k', '3k', or '4k'."
            }
        }
        response_error = urllib.error.HTTPError(
            "https://ark.cn-beijing.volces.com/api/v3/images/generations",
            400,
            "Bad Request",
            {},
            io.BytesIO(json.dumps(payload).encode("utf-8")),
        )
        with patch("core.seedream_client.urllib.request.urlopen", side_effect=response_error):
            with self.assertRaisesRegex(ComicEngineError, "图片分辨率参数不受当前 Seedream 5.0 接口支持"):
                client.generate_image("苏晚回头", size="2K")
        with self.assertRaisesRegex(ComicEngineError, "只支持 1K、2K、3K、4K"):
            client.generate_image("苏晚回头", size="1.5K")

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
        self.assertIn("PrimaryColour=&H0014CCFF", joined)
        self.assertIn("Outline=4", joined)
        self.assertIn("atrim=duration=5.000", joined)
        self.assertIn("libx264", command)

    def test_old_alternating_mode_migrates_to_douyin_presentation(self) -> None:
        self.assertEqual(normalize_motion_mode("上下交替关键帧"), DOUYIN_COMIC_MOTION)
        self.assertEqual(DEFAULT_STATE["comic"]["motion_mode"], DOUYIN_COMIC_MOTION)

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
    def test_legacy_novel_defaults_migrate_to_commentary_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            state = json.loads(json.dumps(DEFAULT_STATE))
            state["schema_version"] = 3
            state["novel"]["mode"] = "深度改写"
            state["novel"]["style"] = "节奏紧凑、画面感强"
            base.mkdir(parents=True, exist_ok=True)
            (base / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            loaded = StateStore(base).load()
            self.assertEqual(loaded["novel"]["mode"], NOVEL_COMMENTARY_MODE)
            self.assertEqual(loaded["novel"]["style"], NOVEL_COMMENTARY_STYLE)

    def test_legacy_image_resolution_is_migrated_to_seedream_5_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            state = json.loads(json.dumps(DEFAULT_STATE))
            project = new_comic_project("旧分辨率项目")
            project["resolution"] = "1.5K"
            state["projects"] = [project]
            state["active_project_id"] = project["project_id"]
            state["comic"] = project
            store.save(state)
            loaded = store.load()
            self.assertEqual(loaded["comic"]["resolution"], "2K")
            self.assertEqual(loaded["projects"][0]["resolution"], "2K")

    def test_roundtrip_and_secret_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            state = json.loads(json.dumps(DEFAULT_STATE))
            project = new_comic_project("测试项目")
            self.assertEqual(project["shot_image_model"], SEEDREAM_LITE_MODEL)
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
            self.assertEqual(store.load()["novel"]["mode"], NOVEL_COMMENTARY_MODE)

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
            backups_after_second_save = list((base / "backups").glob("state-*.json"))
            self.assertTrue(backups_after_second_save)
            first.save(state)
            self.assertEqual(len(list((base / "backups").glob("state-*.json"))), len(backups_after_second_save))

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
            subtitles = root / "voice.srt"
            subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n漫画字幕\n", encoding="utf-8")
            progress: list[float] = []
            result = create_comic_jianying_draft(
                [str(path) for path in images],
                [0.4, 0.6],
                audio_path=str(audio),
                subtitles_path=str(subtitles),
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
            subtitle_material = content["materials"]["texts"][0]
            subtitle_content = json.loads(subtitle_material["content"])
            subtitle_style = subtitle_content["styles"][0]
            self.assertTrue(subtitle_style["bold"])
            self.assertEqual(subtitle_style["fill"]["content"]["solid"]["color"], [1.0, 0.8, 0.08])
            self.assertTrue(subtitle_style["strokes"])
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
