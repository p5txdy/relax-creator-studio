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
        response = _FakeResponse({"choices": [{"message": {"content": "è¿æ¥æˆåŠŸ"}}]})
        with patch("core.ai_client.urllib.request.urlopen", return_value=response) as urlopen:
            result = client.complete("ç³»ç»Ÿæç¤º", "ç”¨æˆ·æç¤º", temperature=0.2)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.moonshot.cn/v1/chat/completions")
        self.assertEqual(payload["model"], "kimi-k3")
        self.assertEqual(payload["messages"][1]["content"], "ç”¨æˆ·æç¤º")
        self.assertNotIn("temperature", payload)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(result, "è¿æ¥æˆåŠŸ")

    def test_non_kimi_provider_keeps_requested_temperature(self) -> None:
        config = AIConfig("https://api.deepseek.com", "deepseek-v4-flash", "test-key", provider="deepseek")
        client = OpenAICompatibleClient(config)
        response = _FakeResponse({"choices": [{"message": {"content": "å®Œæˆ"}}]})
        with patch("core.ai_client.urllib.request.urlopen", return_value=response) as urlopen:
            client.complete("ç³»ç»Ÿæç¤º", "ç”¨æˆ·æç¤º", temperature=0.72)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["temperature"], 0.72)


class NovelEngineTests(unittest.TestCase):
    def test_split_chinese_chapters(self) -> None:
        text = "åºè¨€å†…å®¹\n\nç¬¬ä¸€ç«  ç›¸é‡\n\nè¿™æ˜¯ç¬¬ä¸€ç« ã€‚\n\nç¬¬äºŒç«  è½¬æŠ˜\n\nè¿™æ˜¯ç¬¬äºŒç« ã€‚"
        chapters = split_chapters(text)
        self.assertEqual([chapter.title for chapter in chapters], ["åºç« ", "ç¬¬ä¸€ç«  ç›¸é‡", "ç¬¬äºŒç«  è½¬æŠ˜"])
        self.assertIn("ç¬¬ä¸€ç« ", chapters[1].content)

    def test_fallback_chunking(self) -> None:
        text = "ç¬¬ä¸€æ®µå†…å®¹ã€‚\n\nç¬¬äºŒæ®µå†…å®¹å¾ˆé•¿ã€‚\n\nç¬¬ä¸‰æ®µå†…å®¹ã€‚"
        chapters = split_chapters(text, fallback_size=12)
        self.assertGreaterEqual(len(chapters), 2)
        self.assertEqual(chapters[0].title, "ç‰‡æ®µ 1")

    def test_directly_pasted_text_becomes_editable_chapters(self) -> None:
        records = chapter_records("ç¬¬ä¸€ç«  ç›¸é‡\n\nå¥¹åœ¨é›¨å¤œæ¨å¼€é—¨ã€‚\n\nç¬¬äºŒç«  è½¬æŠ˜\n\nç¯çªç„¶ç†„ç­ã€‚")
        self.assertEqual([item["title"] for item in records], ["ç¬¬ä¸€ç«  ç›¸é‡", "ç¬¬äºŒç«  è½¬æŠ˜"])
        self.assertEqual(records[0]["content"], "å¥¹åœ¨é›¨å¤œæ¨å¼€é—¨ã€‚")

    def test_rewrite_prompt_contains_rules_and_text(self) -> None:
        system, user = build_rewrite_prompt(
            "ç¬¬ä¸€ç« ",
            "åŸå§‹æ­£æ–‡",
            mode="æ·±åº¦æ”¹å†™",
            style="èŠ‚å¥ç´§å‡‘",
            perspective="ç¬¬ä¸€äººç§°",
            target_length="ä¸åŸæ–‡æ¥è¿‘",
            custom_rules="åå­—ä¸èƒ½æ”¹",
            story_bible="ä¸»è§’å«æ—å·",
        )
        self.assertIn("å°è¯´ç¼–è¾‘", system)
        for expected in ("åŸå§‹æ­£æ–‡", "åå­—ä¸èƒ½æ”¹", "ä¸»è§’å«æ—å·", "ç¬¬ä¸€äººç§°"):
            self.assertIn(expected, user)

    def test_commentary_mode_builds_suspenseful_voiceover_prompt_without_fake_plot(self) -> None:
        system, user = build_rewrite_prompt(
            "ç¬¬ä¸€ç« ",
            "æ—å·æ¨å¼€æˆ¿é—¨ï¼Œçœ‹è§å¤±è¸ªä¸‰å¹´çš„å§å§ã€‚",
            mode=NOVEL_COMMENTARY_MODE,
            style=NOVEL_COMMENTARY_STYLE,
            perspective="ç¬¬ä¸‰äººç§°é™çŸ¥",
            target_length="ä¸åŸæ–‡æ¥è¿‘",
            custom_rules="åå­—ä¸èƒ½æ”¹",
            story_bible="æ—å·ä¸çŸ¥é“å§å§å¤±è¸ªçš„çœŸç›¸",
        )
        self.assertIn("å¯ç›´æ¥é…éŸ³", system)
        self.assertIn("ä¸å¾—ä¸ºäº†åˆ¶é€ æ‚¬å¿µæé€ ", system)
        for expected in ("å¼€å¤´å‰ä¸¤å¥", "æ¯ 2â€”4 å¥", "çŸ­å¥å’Œä¸­çŸ­å¥", "ä¸‹ä¸€æ®µé’©å­", "ä¸æå‰æ³„éœ²"):
            self.assertIn(expected, user)

    def test_post_prompt_uses_metadata(self) -> None:
        _system, user = build_post_prompt("é›¨å¤œç™½å™ªéŸ³", "æ²»æ„ˆ", "å°çº¢ä¹¦", ["åˆ‡è‚¥çš‚", "æ•´ç†åœ°æ¯¯"], 28)
        self.assertIn("å°çº¢ä¹¦", user)
        self.assertIn("28", user)
        self.assertIn("åˆ‡è‚¥çš‚", user)


class ComicEngineTests(unittest.TestCase):
    def test_transport_chunks_do_not_define_storyboard_length(self) -> None:
        text = "".join(f"ç¬¬{index}å¥å‰§æƒ…æ¨è¿›ã€‚" for index in range(1, 501))
        chunks = split_story_source_chunks(text, max_chars=900)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(item) <= 900 for item in chunks))
        self.assertEqual("".join(item.replace("\n", "") for item in chunks), text)

    def test_ai_split_prompt_and_validation_leave_boundaries_to_model(self) -> None:
        source = "æ—å·æ¨å¼€é—¨ã€‚è‹æ™šå›å¤´ã€‚ç”µè¯çªç„¶å“äº†ã€‚"
        _system, user = build_ai_split_storyboard_prompt(
            source,
            art_style="ç°ä»£éƒ½å¸‚éŸ©æ¼«",
            existing_characters=[{"name": "æ—å·", "description": "é»‘å‘"}],
            generation_mode="shots",
            batch_index=1,
            batch_total=2,
        )
        self.assertIn("ä¸æŒ‰å›ºå®šå­—æ•°", user)
        self.assertIn("ç”±ä½ å†³å®š shots æ•°é‡", user)
        self.assertIn("å•å¼ é™æ­¢å›¾ç‰‡èƒ½å¦å®Œæ•´è®²æ¸…", user)
        self.assertIn("ä¸å¾—æŠŠéœ€è¦è¿ç»­æ’­æ”¾æ‰èƒ½ç†è§£çš„åŠ¨ä½œè¿‡ç¨‹å‹ç¼©æˆä¸€å¼ å›¾", user)
        self.assertIn("ä¸€ä¸ªè¡¨æƒ…æˆ–ä¸€ä¸ªåŠ¨ä½œ", user)
        self.assertIn("æ§åˆ¶åœ¨ 8ï½30 ä¸ªæ±‰å­—", user)
        self.assertIn("ç¦æ­¢æ¨æ‹‰æ‘‡ç§»ã€è·Ÿæ‹", user)
        shots = validate_ai_storyboard_split(
            [
                {"source": "æ—å·æ¨å¼€é—¨ã€‚", "title": "æ¨é—¨"},
                {"source": "è‹æ™šå›å¤´ã€‚ç”µè¯çªç„¶å“äº†ã€‚", "title": "æ¥ç”µ"},
            ],
            source,
            start_index=4,
        )
        self.assertEqual([item["segment_id"] for item in shots], ["S00004", "S00005"])
        with self.assertRaisesRegex(ComicEngineError, "æœªå®Œæ•´è¦†ç›–"):
            validate_ai_storyboard_split([{"source": "æ—å·æ¨å¼€é—¨ã€‚"}], source)

    def test_story_segments_keep_text_and_target_readable_shots(self) -> None:
        text = "æ—å·æ¨å¼€é—¨ã€‚é›¨æ°´é¡ºç€å¤–å¥—æ»´è½ã€‚\n\nå±‹é‡Œæ²¡æœ‰å¼€ç¯ã€‚è‹æ™šç«™åœ¨çª—å‰ã€‚\n\nç”µè¯çªç„¶å“äº†ã€‚"
        segments = split_story_segments(text, target_chars=24)
        self.assertGreaterEqual(len(segments), 2)
        self.assertEqual("".join(item.replace("\n", "") for item in segments), text.replace("\n", ""))

    def test_numbered_segments_are_batched_without_omission(self) -> None:
        text = "".join(f"ç¬¬{index}å¥å‰§æƒ…æ¨è¿›ã€‚" for index in range(1, 401))
        segments = numbered_story_segments(text, target_chars=120)
        batches = batch_story_segments(segments, max_chars=900)
        flattened = [item for batch in batches for item in batch]
        self.assertGreater(len(batches), 1)
        self.assertEqual([item["segment_id"] for item in flattened], [f"S{index:05d}" for index in range(1, len(segments) + 1)])
        self.assertEqual("".join(item["source"].replace("\n", "") for item in flattened), text)

    def test_storyboard_only_prompt_requires_one_result_per_segment(self) -> None:
        segments = [
            {"segment_id": "S00001", "source": "æ—å·æ¨å¼€é—¨ã€‚"},
            {"segment_id": "S00002", "source": "è‹æ™šç«™åœ¨çª—å‰ã€‚"},
        ]
        _system, user = build_storyboard_batch_prompt(
            segments,
            art_style="ç°ä»£éƒ½å¸‚éŸ©æ¼«",
            existing_characters=[{"name": "æ—å·", "description": "é»‘å‘"}],
            generation_mode="shots",
            batch_index=2,
            batch_total=4,
        )
        self.assertIn("åªç”Ÿæˆåˆ†é•œ", user)
        self.assertIn("shots å¿…é¡»æ°å¥½è¿”å› 2 é¡¹", user)
        self.assertIn("S00001ã€S00002", user)
        self.assertIn("characters å¿…é¡»è¿”å›ç©ºæ•°ç»„", user)
        self.assertIn("scenes å¿…é¡»è¿”å›ç©ºæ•°ç»„", user)

    def test_all_prompt_builds_fixed_scene_library_and_binding(self) -> None:
        _system, user = build_storyboard_batch_prompt(
            [{"segment_id": "S00001", "source": "æ—å·èµ°è¿›æ—§ä¹¦åº—ã€‚"}],
            art_style="ç°ä»£éƒ½å¸‚éŸ©æ¼«",
            existing_characters=[{"name": "æ—å·", "description": "é»‘å‘"}],
            existing_scenes=[{"name": "æ—§ä¹¦åº—", "description": "æœ¨é—¨åœ¨å·¦ï¼ŒæŸœå°åœ¨å³"}],
            generation_mode="all",
            batch_index=1,
            batch_total=1,
        )
        self.assertIn("å·²æœ‰å›ºå®šåœºæ™¯", user)
        self.assertIn("æ—§ä¹¦åº—ï¼šæœ¨é—¨åœ¨å·¦ï¼ŒæŸœå°åœ¨å³", user)
        self.assertIn("äººç‰© prompt åªèƒ½æè¿°è§’è‰²æœ¬èº«ä¸çº¯è‰²èƒŒæ™¯", user)
        self.assertIn("æ— åœºæ™¯ã€æ— å»ºç­‘ã€æ— å®¶å…·", user)
        self.assertIn('"scenes"', user)
        self.assertIn('"scene": "å›ºå®šåœºæ™¯å"', user)

    def test_storyboard_batch_validation_orders_repairs_source_and_rejects_missing_shots(self) -> None:
        expected = [
            {"segment_id": "S00001", "source": "ç¬¬ä¸€æ®µã€‚"},
            {"segment_id": "S00002", "source": "ç¬¬äºŒæ®µã€‚"},
        ]
        ordered = validate_storyboard_batch(
            [
                {"segment_id": "S00002", "source": "ç¬¬äºŒæ®µã€‚"},
                {"segment_id": "S00001", "source": "ç¬¬ä¸€æ®µã€‚"},
            ],
            expected,
        )
        self.assertEqual([item["segment_id"] for item in ordered], ["S00001", "S00002"])
        repaired = validate_storyboard_batch(
            [
                {"segment_id": "S1", "source": "æ¨¡å‹æ”¹å†™äº†ç¬¬ä¸€æ®µ"},
                {"segment_id": "S2", "source": "ç¬¬äºŒ"},
            ],
            expected,
        )
        self.assertEqual([item["source"] for item in repaired], ["ç¬¬ä¸€æ®µã€‚", "ç¬¬äºŒæ®µã€‚"])
        positional = validate_storyboard_batch([{"source": "ç”²"}, {"source": "ä¹™"}], expected)
        self.assertEqual([item["segment_id"] for item in positional], ["S00001", "S00002"])
        with self.assertRaisesRegex(ComicEngineError, "ç¼ºå°‘"):
            validate_storyboard_batch(
                [
                    {"segment_id": "S00001", "source": "ç¬¬ä¸€æ®µã€‚"},
                ],
                expected,
            )

    def test_parse_storyboard_json_creates_editable_records(self) -> None:
        raw = """```json
        {"characters":[{"name":"æ—å·","description":"é»‘å‘ï¼Œé»‘è‰²é£è¡£"}],"scenes":[{"name":"æ—§ä¹¦åº—","description":"æœ¨é—¨åœ¨å·¦ï¼ŒæŸœå°åœ¨å³"}],"shots":[{"title":"é›¨å¤œ","source":"æ—å·æ¨é—¨","narration":"é—¨å¼€äº†","characters":["æ—å·"],"scene":"æ—§ä¹¦åº—","prompt":"é›¨å¤œæ¨é—¨ï¼Œä¸­æ™¯"}]}
        ```"""
        result = parse_storyboard_response(raw, art_style="æ—¥ç³»åŠ¨æ¼«")
        self.assertEqual(result["characters"][0]["name"], "æ—å·")
        self.assertIn("ä¸å¾—å‡ºç°ä»»ä½•å®¤å†…å¤–åœºæ™¯", re×N|âÚ$z{-®éÜj×&ö¦V7EĞ¢7FFU²&7F—fU÷&ö¦V7Eö–B%ÒÒ&ö¦V7E²'&ö¦V7Eö–B%Ğ¢7FFU²&6öÖ–2%ÒÒ&ö¦V7@¢7F÷&Rç6fR‡7FFR¢ÆöFVBÒ7F÷&RæÆöB‚¢6VÆbæ76W'DWVÂ†ÆöFVE²&6öÖ–2%Õ²'&W6öÇWF–öâ%ÒÂ#$²"¢6VÆbæ76W'DWVÂ†ÆöFVE²'&ö¦V7G2%Õ³Õ²'&W6öÇWF–öâ%ÒÂ#$²" ¢FVbFW7E÷&÷VæGG&—öæE÷6V7&WEö—5÷&VÖ÷fVB‡6VÆb’ÓâæöæS ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢7F÷&RÒ7FFU7F÷&R…F‚‡FV×’¢7FFRÒ§6öâæÆöG2†§6öâæGV×2„DTdTÅEõ5DDR’¢&ö¦V7BÒæWuö6öÖ–5÷&ö¦V7B‚.kX¾Šù^šyºâ"¢6VÆbæ76W'DWVÂ‡&ö¦V7E²'6†÷Eö–ÖvUöÖöFVÂ%ÒÂ4TTE$TÕôÄ•DUôÔôDTÂ¢7FFU²'&ö¦V7G2%ÒÒ·&ö¦V7EĞ¢7FFU²&7F—fU÷&ö¦V7Eö–B%ÒÒ&ö¦V7E²'&ö¦V7Eö–B%Ğ¢7FFU²&6öÖ–2%ÒÒ&ö¦V7@¢7FFU²'6WGF–æw2%Õ²&•ö¶W’%ÒÒ'6V7&WB ¢7FFU²'6WGF–æw2%Õ²&&µö•ö¶W’%ÒÒ&&²×6V7&WB ¢7FFU²'6WGF–æw2%Õ²'—VçwUö&6U÷W&Â%ÒÒ&‡GG3¢òöÆVv7’æW†×ÆR ¢7FFU²&6öÖ–2%Õ²&&÷E÷G—R%ÒÒ$ä”¤•ô¤õU$äU’ ¢7FFU²&6öÖ–2%Õ²'W66ÆUö–æFW‚%ÒÒ0¢7F÷&Rç6fR‡7FFR¢&rÒ7F÷&RçF‚ç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"¢6VÆbæ76W'Dæ÷D–â‚'6V7&WB"Â&r¢6VÆbæ76W'Dæ÷D–â‚&&²×6V7&WB"Â&r¢6VÆbæ76W'DWVÂ‡7F÷&RæÆöB‚•²&6öÖ–2%Õ²'&ö¦V7EöæÖR%ÒÂ.kX¾Šù^šyºâ"¢6VÆbæ76W'EG'VR‡7F÷&RæÆöB‚•²'6WGF–æw2%Õ²'&VÖVÖ&W%ö•ö¶W’%Ò¢6VÆbæ76W'D–â‚&6öÖ–2"Â7F÷&RæÆöB‚’¢6VÆbæ76W'Dæ÷D–â‚'—VçwUö&6U÷W&Â"Â7F÷&RæÆöB‚•²'6WGF–æw2%Ò¢6VÆbæ76W'Dæ÷D–â‚&&÷E÷G—R"Â7F÷&RæÆöB‚•²&6öÖ–2%Ò¢6VÆbæ76W'Dæ÷D–â‚'W66ÆUö–æFW‚"Â7F÷&RæÆöB‚•²&6öÖ–2%Ò¢6VÆbæ76W'Dæ÷D–â‚'f–FVò"Â7F÷&RæÆöB‚’¢6VÆbæ76W'D–â‚&æ÷fVÂ"Â7F÷&RæÆöB‚’¢6VÆbæ76W'DWVÂ‡7F÷&RæÆöB‚•²&æ÷fVÂ%Õ²&ÖöFR%ÒÂäõdTÅô4ôÔÔTåD%•ôÔôDR ¢FVbFW7Eö×VÇF—ÆU÷&ö¦V7G5÷6†&UööæUö6†&7FW%öÆ–'&'’‡6VÆb’ÓâæöæS ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢7F÷&RÒ7FFU7F÷&R…F‚‡FV×’¢7FFRÒ§6öâæÆöG2†§6öâæGV×2„DTdTÅEõ5DDR’¢f—'7BÒæWuö6öÖ–5÷&ö¦V7B‚.zÊÎKˆiÚhêihr"¢6V6öæBÒæWuö6öÖ–5÷&ö¦V7B‚.zÊÎK¨ÎiÚhêihr"¢7FFU²'&ö¦V7G2%ÒÒ¶f—'7BÂ6V6öæEĞ¢7FFU²&7F—fU÷&ö¦V7Eö–B%ÒÒ6V6öæE²'&ö¦V7Eö–B%Ğ¢7FFU²'6†&VEö6†&7FW'2%ÒÒ·²&æÖR#¢.ié~[yÒ"Â&FW67&—F–öâ#¢.›¹Xù"Â'&ö×B#¢.ié~[yŞjÚ>™Ú.XZ‹ª¾Zé®ZhnûÈÎ{ªşˆ›.ˆ8Îišò'ÕĞ¢7FFU²&6öÖ–2%ÒÒ6V6öæ@¢7F÷&Rç6fR‡7FFR¢ÆöFVBÒ7F÷&RæÆöB‚¢6VÆbæ76W'DWVÂ†ÆVâ†ÆöFVE²'&ö¦V7G2%Ò’Â"¢6VÆbæ76W'DWVÂ†ÆöFVE²&6öÖ–2%Õ²'&ö¦V7EöæÖR%ÒÂ.zÊÎK¨ÎiÚhêihr"¢6VÆbæ76W'D—2†ÆöFVE²&6öÖ–2%Õ²&6†&7FW'2%ÒÂÆöFVE²'6†&VEö6†&7FW'2%Ò¢6VÆbæ76W'D—2†ÆöFVE²'&ö¦V7G2%Õ³Õ²&6†&7FW'2%ÒÂÆöFVE²'6†&VEö6†&7FW'2%Ò¢6VÆbæ76W'DWVÂ†ÆöFVE²'&ö¦V7G2%Õ³Õ²&6†&7FW'2%Õ³Õ²&æÖR%ÒÂ.ié~[yÒ"¢6VÆbæ76W'DWVÂ†ÆöFVE²'6†&VEö6†&7FW'2%Õ³Õ²'&ö×B%ÒÂ.ié~[yŞjÚ>™Ú.XZ‹ª¾Zé®ZhnûÈÎ{ªşˆ›.ˆ8Îišò" ¢FVbFW7EöÆVv7•÷6–ævÆUö6öÖ–5ö—5öÖ–w&FVE÷Fõ÷&ö¦V7EöæE÷6†&VE÷&öÆW2‡6VÆb’ÓâæöæS ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢7F÷&RÒ7FFU7F÷&R…F‚‡FV×’¢7F÷&Ræ&6UöF—"æÖ¶F—"‡&VçG3ÕG'VRÂW†—7Eöö³ÕG'VR¢7F÷&RçF‚çw&—FU÷FW‡B€¢§6öâæGV×2€¢°¢'6WGF–æw2#¢·ÒÀ¢&6öÖ–2#¢°¢'&ö¦V7EöæÖR#¢.iz~x˜hêihr"À¢'6÷W&6U÷FW‡B#¢.iz~jÚ>ihr"À¢&6†&7FW'2#¢·²&æÖR#¢.ˆ¸şi™¢"Â&FW67&—F–öâ#¢.y›ŞXù'ÕÒÀ¢'66VæW2#¢µÒÀ¢'6†÷G2#¢µÒÀ¢ÒÀ¢ÒÀ¢Vç7W&Uö66–“ÔfÇ6RÀ¢’À¢Væ6öF–æsÒ'WFbÓ‚"À¢¢ÆöFVBÒ7F÷&RæÆöB‚¢6VÆbæ76W'DWVÂ†ÆVâ†ÆöFVE²'&ö¦V7G2%Ò’Â¢6VÆbæ76W'DWVÂ†ÆöFVE²&6öÖ–2%Õ²'&ö¦V7EöæÖR%ÒÂ.iz~x˜hêihr"¢6VÆbæ76W'DWVÂ†ÆöFVE²'6†&VEö6†&7FW'2%Õ³Õ²&æÖR%ÒÂ.ˆ¸şi™¢" ¢FVbFW7Eö÷'†æVEö6†&7FW%öæE÷66VæUö–ÖvW5ö&U÷&V6÷fW&VB‡6VÆb’ÓâæöæS ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢&6RÒF‚‡FV×¢&öÆUöF—"Ò&6Rò'6†&VEö76WG2"ò&6†&7FW'2 ¢66VæUöF—"Ò&6Rò'&ö¦V7BÖ÷WGWB"ò'66VæW2 ¢&öÆUöF—"æÖ¶F—"‡&VçG3ÕG'VR¢66VæUöF—"æÖ¶F—"‡&VçG3ÕG'VR¢&öÆU÷&VfW&Væ6RÒ&öÆUöF—"ò.ˆ¸şi™¥÷&VfW&Væ6Rçær ¢&öÆUö6æF–FFRÒ&öÆUöF—"ò.ˆ¸şi™¥ö6æF–FFRçær ¢66VæU÷&VfW&Væ6RÒ66VæUöF—"ò.Zê.XèU÷&VfW&Væ6Rçær ¢&öÆU÷&VfW&Væ6Rçw&—FUö'—FW2†"'&VfW&Væ6R"¢&öÆUö6æF–FFRçw&—FUö'—FW2†"&6æF–FFR"¢66VæU÷&VfW&Væ6Rçw&—FUö'—FW2†"'66VæR"¢7F÷&RÒ7FFU7F÷&R†&6R¢7FFRÒ§6öâæÆöG2†§6öâæGV×2„DTdTÅEõ5DDR’¢&ö¦V7BÒæWuö6öÖ–5÷&ö¦V7B‚.h.ZHŞkX¾ŠùR"¢&ö¦V7E²&÷WGWEöF—"%ÒÒ7G"†&6Rò'&ö¦V7BÖ÷WGWB"¢7FFU²'&ö¦V7G2%ÒÒ·&ö¦V7EĞ¢7FFU²&7F—fU÷&ö¦V7Eö–B%ÒÒ&ö¦V7E²'&ö¦V7Eö–B%Ğ¢7FFU²&6öÖ–2%ÒÒ&ö¦V7@¢7F÷&Rç6fR‡7FFR ¢ÆöFVBÒ7F÷&RæÆöB‚¢&öÆRÒæW‡B†—FVÒf÷"—FVÒ–âÆöFVE²'6†&VEö6†&7FW'2%Ò–b—FVÕ²&æÖR%ÒÓÒ.ˆ¸şi™¢"¢66VæRÒæW‡B†—FVÒf÷"—FVÒ–âÆöFVE²&6öÖ–2%Õ²'66VæW2%Ò–b—FVÕ²&æÖR%ÒÓÒ.Zê.XèR"¢6VÆbæ76W'DWVÂ‡&öÆU²'7FGW2%ÒÂ.Zé®Zhn[{.zîŠêB"¢6VÆbæ76W'DWVÂ‡&öÆU²&Æö6Å÷F‚%ÒÂ7G"‡&öÆU÷&VfW&Væ6R’¢6VÆbæ76W'DWVÂ‡&öÆU²&6æF–FFU÷F‚%ÒÂ7G"‡&öÆUö6æF–FFR’¢6VÆbæ76W'DWVÂ‡66VæU²'7FGW2%ÒÂ.Zé®išş[{.zîŠêB"¢6VÆbæ76W'DWVÂ‡66VæU²&Æö6Å÷F‚%ÒÂ7G"‡66VæU÷&VfW&Væ6R’ ¢FVbFW7E÷7FFUö&6·W5öæE÷6–ævÆUö–ç7Fæ6UöÆö6²‡6VÆb’ÓâæöæS ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢&6RÒF‚‡FV×¢f—'7BÒ7FFU7F÷&R†&6R¢6V6öæBÒ7FFU7F÷&R†&6R¢6VÆbæ76W'EG'VR†f—'7Bæ7V—&Uö–ç7Fæ6UöÆö6²‚’¢6VÆbæ76W'DfÇ6R‡6V6öæBæ7V—&Uö–ç7Fæ6UöÆö6²‚’¢f—'7Bç&VÆV6Uö–ç7Fæ6UöÆö6²‚¢6VÆbæ76W'EG'VR‡6V6öæBæ7V—&Uö–ç7Fæ6UöÆö6²‚’¢6V6öæBç&VÆV6Uö–ç7Fæ6UöÆö6²‚¢7FFRÒ§6öâæÆöG2†§6öâæGV×2„DTdTÅEõ5DDR’¢f—'7Bç6fR‡7FFR¢f—'7Bç6fR‡7FFR¢&6·W5ögFW%÷6V6öæE÷6fRÒÆ—7B‚†&6Rò&&6·W2"’ævÆö"‚'7FFRÒ¢æ§6öâ"’¢6VÆbæ76W'EG'VR†&6·W5ögFW%÷6V6öæE÷6fR¢f—'7Bç6fR‡7FFR¢6VÆbæ76W'DWVÂ†ÆVâ†Æ—7B‚†&6Rò&&6·W2"’ævÆö"‚'7FFRÒ¢æ§6öâ"’’’ÂÆVâ†&6·W5ögFW%÷6V6öæE÷6fR’ ¢FVbFW7EöÆVv7•ö76WG5ö&Uö6÷–VEö–çFõ÷F†UöæWuöFFöF—&V7F÷'’‡6VÆb’ÓâæöæS ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢&ö÷BÒF‚‡FV×¢öÆEö&6RÒ&ö÷Bò%&VÆ„7&VF÷%7GVF–ò ¢æWuö&6RÒ&ö÷Bò$6öÖ–5÷7E7GVF–ò ¢öÆE÷&öÆRÒöÆEö&6Rò'6†&VEö76WG2"ò&6†&7FW'2"ò.ˆ¸şi™¥÷&VfW&Væ6Rçær ¢öÆE÷66VæRÒöÆEö&6Rò&6öÖ–5÷&ö¦V7G2"ò.iz~šyºâ"ò'66VæW2"ò.Zê.XèU÷&VfW&Væ6Rçær ¢öÆE÷&öÆRç&VçBæÖ¶F—"‡&VçG3ÕG'VR¢öÆE÷66VæRç&VçBæÖ¶F—"‡&VçG3ÕG'VR¢öÆE÷&öÆRçw&—FUö'—FW2†"'&öÆR"¢öÆE÷66VæRçw&—FUö'—FW2†"'66VæR"¢öÆE÷7FFRÒ°¢'6WGF–æw2#¢·ÒÀ¢&6öÖ–2#¢°¢'&ö¦V7EöæÖR#¢.iz~šyºâ"À¢&÷WGWEöF—"#¢7G"†öÆEö&6Rò&6öÖ–5÷&ö¦V7G2"ò.iz~šyºâ"’À¢&6†&7FW'2#¢µÒÀ¢'66VæW2#¢µÒÀ¢'6†÷G2#¢µÒÀ¢ÒÀ¢Ğ¢†öÆEö&6Rò'7FFRæ§6öâ"’çw&—FU÷FW‡B†§6öâæGV×2†öÆE÷7FFRÂVç7W&Uö66–“ÔfÇ6R’ÂVæ6öF–æsÒ'WFbÓ‚"¢7F÷&RÒ7FFU7F÷&R†æWuö&6R¢7F÷&RæÆVv7•ö&6UöF—"ÒöÆEö&6P¢ÆöFVBÒ7F÷&RæÆöB‚¢&öÆRÒæW‡B†—FVÒf÷"—FVÒ–âÆöFVE²'6†&VEö6†&7FW'2%Ò–b—FVÕ²&æÖR%ÒÓÒ.ˆ¸şi™¢"¢66VæRÒæW‡B†—FVÒf÷"—FVÒ–âÆöFVE²&6öÖ–2%Õ²'66VæW2%Ò–b—FVÕ²&æÖR%ÒÓÒ.Zê.XèR"¢6VÆbæ76W'EG'VR…F‚‡&öÆU²&Æö6Å÷F‚%Ò’æ—5÷&VÆF—fU÷Fò†æWuö&6R’¢6VÆbæ76W'EG'VR…F‚‡66VæU²&Æö6Å÷F‚%Ò’æ—5÷&VÆF—fU÷Fò†æWuö&6R’¢6VÆbæ76W'EG'VR…F‚‡&öÆU²&Æö6Å÷F‚%Ò’æ—5öf–ÆR‚’¢6VÆbæ76W'EG'VR…F‚‡66VæU²&Æö6Å÷F‚%Ò’æ—5öf–ÆR‚’  ¦6Æ726V7&WE7F÷&UFW7G2‡Væ—GFW7BåFW7D66R“ ¢FVbFW7E÷v–æF÷w5÷&÷WFW5÷Fõö7&VFVçF–ÅöÖævW"‡6VÆb’ÓâæöæS ¢v—F‚F6‚‚&6÷&Rç6V7&WE÷7F÷&Rç7—2çÆFf÷&Ò"Â'v–ã3""“ ¢v—F‚F6‚‚&6÷&Rç6V7&WE÷7F÷&Rå÷v–æF÷w5÷&VB"Â&WGW&å÷fÇVSÒ'6fVBÖ¶W’"’2&VC ¢6VÆbæ76W'DWVÂ†ÆöEö•ö¶W’‚&¶–Ö’"’Â'6fVBÖ¶W’"¢&VBæ76W'Eö6ÆÆVEööæ6U÷v—F‚‚&¶–Ö’"¢v—F‚F6‚‚&6÷&Rç6V7&WE÷7F÷&Rå÷v–æF÷w5÷w&—FR"’2w&—FS ¢6fUö•ö¶W’‚&¶–Ö’"Â"æWrÖ¶W’"¢w&—FRæ76W'Eö6ÆÆVEööæ6U÷v—F‚‚&¶–Ö’"Â&æWrÖ¶W’"¢v—F‚F6‚‚&6÷&Rç6V7&WE÷7F÷&Rå÷v–æF÷w5öFVÆWFR"’2FVÆWFS ¢FVÆWFUö•ö¶W’‚&¶–Ö’"¢FVÆWFRæ76W'Eö6ÆÆVEööæ6U÷v—F‚‚&¶–Ö’" ¢FVbFW7EöÖ6÷5ö¶W–6†–å÷&VE÷G&–×5ööæÇ•öÆ–æUö'&V²‡6VÆb’ÓâæöæS ¢&W7VÇBÒ7V'&ö6W72ä6ö×ÆWFVE&ö6W72…µÒÂÂ"¶W’×v—F‚×76W2Æâ"Â""¢v—F‚F6‚‚&6÷&Rç6V7&WE÷7F÷&Rç7—2çÆFf÷&Ò"Â&F'v–â"“ ¢v—F‚F6‚‚&6÷&Rç6V7&WE÷7F÷&Rå÷'Vå÷6V7W&—G’"Â&WGW&å÷fÇVS×&W7VÇB’26V7W&—G“ ¢6VÆbæ76W'DWVÂ†ÆöEö•ö¶W’‚&FVW6VV²"’Â"¶W’×v—F‚×76W2"¢6V7W&—G’æ76W'Eö6ÆÆVEööæ6U÷v—F‚€¢²&f–æBÖvVæW&–2×77v÷&B"Â"×2"Â%&VÆ„7&VF÷%7GVF–ò"Â"Ö"Â&FVW6VV²"Â"×r%Ğ¢ ¢FVbFW7Eö–çfÆ–E÷&÷f–FW%ö–Eö—5÷&V¦V7FVB‡6VÆb’ÓâæöæS ¢v—F‚6VÆbæ76W'E&—6W2…6V7&WE7F÷&TW'&÷"“ ¢ÆöEö•ö¶W’‚"ââ÷Vç6fR" ¢FVbFW7E÷Vç7W÷'FVE÷ÆFf÷&ÕöFöW5öæ÷Eöf¶U÷6V7W&U÷7F÷&vR‡6VÆb’ÓâæöæS ¢v—F‚F6‚‚&6÷&Rç6V7&WE÷7F÷&Rç7—2çÆFf÷&Ò"Â&Æ–çW‚"“ ¢6VÆbæ76W'DWVÂ†ÆöEö•ö¶W’‚'vVâ"’Â""¢v—F‚6VÆbæ76W'E&—6W2…6V7&WE7F÷&TW'&÷"“ ¢6fUö•ö¶W’‚'vVâ"Â'6V7&WB"  ¦6Æ72¦–ç––ætVæv–æUFW7G2‡Væ—GFW7BåFW7D66R“ ¢FVbFW7EöÆ–v‡GvV–v‡EöÆVæ6†W%÷76W5övVæW&FVE÷f–FVõ÷Fõö¦–ç––ær‡6VÆb’ÓâæöæS ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢&ö÷BÒF‚‡FV×¢W†V7WF&ÆRÒ&ö÷Bò$¦–ç––æu&òæW†R ¢f–FVòÒ&ö÷Bò.™ÙhkÊ²æ×B ¢W†V7WF&ÆRçw&—FUö'—FW2†"&W†R"¢f–FVòçw&—FUö'—FW2†"'f–FVò"¢6VÆbæ76W'DWVÂ†FWFV7Eö¦–ç––æuöÆVæ6†W"‡7G"†W†V7WF&ÆR’’Â7G"†W†V7WF&ÆR’¢v—F‚F6‚‚&6÷&Ræ¦–ç––æuöÆVæ6†W"ç7V'&ö6W72å÷Vâ"’2÷Vã ¢÷Våö¦–ç––æuöÆVæ6†W"‡7G"†W†V7WF&ÆR’Â7G"‡f–FVò’¢6öÖÖæBÒ÷Vâæ6ÆÅö&w2æ&w5³Ğ¢6VÆbæ76W'DWVÂ†6öÖÖæBÂ·7G"†W†V7WF&ÆR’Â7G"‡f–FVò•Ò ¢FVbFW7Eö–×÷'FVE÷f–FVõöVF–õö—5ö×WFVB‡6VÆb’ÓâæöæS ¢6VÆbæ76W'DWVÂ…4õU$4Uõd”DTõõdôÅTÔRÂã ¢FVbFW7Eö6öÖ–5öG&gEö¶VW5÷†÷F÷5öæE÷fW'F–6Åö¶W–g&ÖW5öVF—F&ÆR‡6VÆb’ÓâæöæS ¢ærÒ&6ScBæ#cFFV6öFR€¢&•d$õ's´vvôå5V„UVt”44”C“§¤dVÄUe#Fäu‡£„DtÔD„ÔDtÔtcD'45Udõ$³T5””“Ò ¢¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢&ö÷BÒF‚‡FV×¢–ÖvW2Ò·&ö÷Bò#çær"Â&ö÷Bò#"çær%Ğ¢f÷"–ÖvR–â–ÖvW3 ¢–ÖvRçw&—FUö'—FW2‡ær¢VF–òÒ&ö÷Bò'fö–6Rçvb ¢v—F‚vfRæ÷Vâ‡7G"†VF–ò’Â'v""’2†æFÆS ¢†æFÆRç6WFæ6†ææVÇ2ƒ¢†æFÆRç6WG6×v–GF‚ƒ"¢†æFÆRç6WFg&ÖW&FRƒƒ¢†æFÆRçw&—FVg&ÖW2†"%ÃÃ"¢ƒ¢7V'F—FÆW2Ò&ö÷Bò'fö–6Rç7'B ¢7V'F—FÆW2çw&—FU÷FW‡B‚#Æã££ÃÒÓâ££ÃÆîkÊ¾yK¾ZÙ~[™UÆâ"ÂVæ6öF–æsÒ'WFbÓ‚"¢&öw&W73¢Æ—7E¶fÆöEÒÒµĞ¢&W7VÇBÒ7&VFUö6öÖ–5ö¦–ç––æuöG&gB€¢·7G"‡F‚’f÷"F‚–â–ÖvW5ÒÀ¢³ãBÂãeÒÀ¢VF–õ÷Fƒ×7G"†VF–ò’À¢7V'F—FÆW5÷Fƒ×7G"‡7V'F—FÆW2’À¢G&gG5÷&ö÷C×7G"‡&ö÷B’À¢&WVW7FVEöæÖSÒ.™ÙhkÊ¾kX¾ŠùR"À¢Ö÷F–öåöÖöFSÒ.Kˆ®Kˆ¾KªNi»şX[>™Jî[Šr"À¢öå÷&öw&W73ÖÆÖ&FfÇVRÂöFWF–Ã¢&öw&W72æVæB‡fÇVR’À¢¢6öçFVçBÒ§6öâæÆöG2‚…F‚‡&W7VÇBçF‚’ò&G&gEö6öçFVçBæ§6öâ"’ç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"’¢f–FVõ÷G&6²ÒæW‡B‡G&6²f÷"G&6²–â6öçFVçE²'G&6·2%Ò–bG&6µ²&æÖR%ÒÓÒ.™ÙhkÊ¾yK²"¢6VÆbæ76W'DWVÂ†ÆVâ‡f–FVõ÷G&6µ²'6VvÖVçG2%Ò’Â"¢f÷"6VvÖVçB–âf–FVõ÷G&6µ²'6VvÖVçG2%Ó ¢÷6—F–öâÒæW‡B†—FVÒf÷"—FVÒ–â6VvÖVçE²&6öÖÖöåö¶W–g&ÖW2%Ò–b—FVÕ²'&÷W'G•÷G—R%ÒÓÒ$´eG—U÷6—F–öå’"¢6VÆbæ76W'DWVÂ†ÆVâ‡÷6—F–öå²&¶W–g&ÖUöÆ—7B%Ò’Â"¢7V'F—FÆUöÖFW&–ÂÒ6öçFVçE²&ÖFW&–Ç2%Õ²'FW‡G2%Õ³Ğ¢7V'F—FÆUö6öçFVçBÒ§6öâæÆöG2‡7V'F—FÆUöÖFW&–Å²&6öçFVçB%Ò¢7V'F—FÆU÷7G–ÆRÒ7V'F—FÆUö6öçFVçE²'7G–ÆW2%Õ³Ğ¢6VÆbæ76W'EG'VR‡7V'F—FÆU÷7G–ÆU²&&öÆB%Ò¢6VÆbæ76W'DWVÂ‡7V'F—FÆU÷7G–ÆU²&f–ÆÂ%Õ²&6öçFVçB%Õ²'6öÆ–B%Õ²&6öÆ÷"%ÒÂ³ãÂã‚Âã…Ò¢6VÆbæ76W'EG'VR‡7V'F—FÆU÷7G–ÆU²'7G&ö¶W2%Ò¢6VÆbæ76W'DWVÂ‡&öw&W75²ÓÒÂã¢6VÆbæ76W'DÆÖ÷7DWVÂ‡&W7VÇBæGW&F–öå÷6V6öæG2ÂãÂÆ6W3Ó" ¢FVbFW7E÷6æ—F—¦UöæE÷Væ—VUöæÖR‡6VÆb’ÓâæöæS ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢6VÆbæ76W'DWVÂ‡6æ—F—¦UöG&gEöæÖR‚~kX¾ŠùS¢şˆØz‹óòr’Â.kX¾ŠùUõşˆØz‹õò"¢…F‚‡FV×’ò.k{~Xš¢"’æÖ¶F—"‚¢6VÆbæ76W'DWVÂ‡Væ—VUöG&gEöæÖR‡FV×Â.k{~Xš¢"’Â.k{~Xš®ûÈƒ.ûÈ’" ¢FVbFW7Eö6öæf–wW&VEöG&gEöföÆFW%ö†5÷&–÷&—G’‡6VÆb’ÓâæöæS ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢6VÆbæ76W'DWVÂ†FWFV7Eö¦–ç––æuöG&gG5÷F‚‡FV×’ÂFV× ¢FVbFW7EöÖ6÷5öö'VæFÆUö—5öFWFV7FVEöæEö÷VæVB‡6VÆb’ÓâæöæS ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢ö'VæFÆRÒF‚‡FV×’ò.Xš®iŠK‰>K‰®x˜‚æ ¢ö'VæFÆRæÖ¶F—"‚¢v—F‚F6‚‚&6÷&Ræ¦–ç––æuöVæv–æRç7—2çÆFf÷&Ò"Â&F'v–â"“ ¢6VÆbæ76W'DWVÂ†FWFV7Eö¦–ç––æuöW†V7WF&ÆR‡7G"†ö'VæFÆR’’Â7G"†ö'VæFÆR’¢v—F‚F6‚‚&6÷&Ræ¦–ç––æuöVæv–æRç7V'&ö6W72å÷Vâ"’2÷Vã ¢÷Våö¦–ç––ær‡7G"†ö'VæFÆR’¢÷Vâæ76W'Eö6ÆÆVEööæ6U÷v—F‚…²"÷W7"ö&–âö÷Vâ"Â7G"†ö'VæFÆR•ÒÂ6Æ÷6UöfG3ÕG'VR ¢FVbFW7E÷7V'F—FÆW5ö&Uö6Æ×VE÷FõöVF–õöGW&F–öâ‡6VÆb’ÓâæöæS ¢6÷W&6RÒ€¢#Æã££ÃÒÓâ££ÃSÆîzÊÎKˆXúUÆåÆâ ¢#%Æã££"ÃÒÓâ££RÃÆîzÊÎK¨ÎXúUÆåÆâ ¢#5Æã££bÃÒÓâ££rÃÆî‹h^X{®ˆÈ>Y»EÆâ ¢¢&W7VÇBÒ6Æ×÷7'E÷FW‡B‡6÷W&6RÂ5ó#ó¢6VÆbæ76W'D–â‚#££2Ã#"Â&W7VÇB¢6VÆbæ76W'Dæ÷D–â‚.‹h^X{®ˆÈ>Y»B"Â&W7VÇB  ¦–bõöæÖUõòÓÒ%õöÖ–åõò# ¢Væ—GFW7BæÖ–â‚