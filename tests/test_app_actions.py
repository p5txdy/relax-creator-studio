from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tkinter import Tk

from app import StudioApp
from app import SEEDREAM_LITE_MODEL, SEEDREAM_PRO_MODEL, SHOT_IMAGE_MODEL_OPTIONS
from app import RoundedButton, RoundedCombobox
from core.storage import StateStore


class BatchRedrawActionTests(unittest.TestCase):
    def _app(self, indices: list[int]) -> StudioApp:
        studio = StudioApp.__new__(StudioApp)
        studio.state = {
            "comic": {
                "shots": [
                    {"title": "镜头一", "local_path": "one.png", "image_url": ""},
                    {"title": "镜头二", "local_path": "", "image_url": ""},
                    {"title": "镜头三", "local_path": "three.png", "image_url": "https://example.com/three.png"},
                ]
            }
        }
        studio.save_comic_shot_prompt = Mock()
        studio._selected_comic_shot_indices = Mock(return_value=indices)
        studio._generate_comic_shots = Mock()
        studio.comic_shot_model_var = SimpleNamespace(get=lambda: "Lite · 省成本")
        studio.comic_resolution_var = SimpleNamespace(get=lambda: "2K")
        return studio

    def test_redraw_uses_exactly_the_multi_selected_rows(self) -> None:
        studio = self._app([0, 2])
        with patch("app.messagebox.askyesno", return_value=True) as confirm:
            studio.redraw_selected_comic_shots()
        studio._generate_comic_shots.assert_called_once_with([0, 2], confirm_batch=False)
        message = confirm.call_args.args[1]
        self.assertIn("选中的 2 个镜头", message)
        self.assertIn("其中 2 个镜头已有图片", message)
        self.assertIn("Lite · 省成本、2K", message)

    def test_redraw_requires_at_least_one_selected_row(self) -> None:
        studio = self._app([])
        with patch("app.messagebox.showinfo") as info:
            studio.redraw_selected_comic_shots()
        info.assert_called_once()
        studio._generate_comic_shots.assert_not_called()


class BatchResolutionActionTests(unittest.TestCase):
    def _app(self, model_label: str, resolution: str) -> StudioApp:
        studio = StudioApp.__new__(StudioApp)
        studio.state = {"comic": {"shot_image_model": SEEDREAM_LITE_MODEL, "resolution": resolution}}
        studio.store = SimpleNamespace(save=Mock())
        studio.comic_shot_model_var = SimpleNamespace(get=lambda: model_label)
        values = {"resolution": resolution}
        studio.comic_resolution_var = SimpleNamespace(
            get=lambda: values["resolution"],
            set=lambda value: values.__setitem__("resolution", value),
        )
        studio.comic_resolution_buttons = {}
        studio.comic_resolution_hint_var = None
        studio.comic_generation_detail_label = None
        return studio

    def test_resolution_button_saves_immediately(self) -> None:
        studio = self._app(SHOT_IMAGE_MODEL_OPTIONS[0], "2K")
        studio._select_comic_resolution("3K")
        self.assertEqual(studio.state["comic"]["resolution"], "3K")
        studio.store.save.assert_called_once_with(studio.state)

    def test_pro_supports_1k_and_saves_immediately(self) -> None:
        studio = self._app(SHOT_IMAGE_MODEL_OPTIONS[1], "2K")
        studio._select_comic_resolution("1K")
        self.assertEqual(studio.state["comic"]["resolution"], "1K")
        self.assertEqual(studio.comic_resolution_var.get(), "1K")
        studio.store.save.assert_called_once_with(studio.state)

    def test_lite_rejects_unsupported_4k_button(self) -> None:
        studio = self._app(SHOT_IMAGE_MODEL_OPTIONS[0], "2K")
        with patch("app.messagebox.showinfo") as info:
            studio._select_comic_resolution("4K")
        info.assert_called_once()
        self.assertEqual(studio.state["comic"]["resolution"], "2K")
        studio.store.save.assert_not_called()

    def test_switching_from_pro_4k_to_lite_resets_to_2k(self) -> None:
        studio = self._app(SHOT_IMAGE_MODEL_OPTIONS[1], "4K")
        labels = {"model": SHOT_IMAGE_MODEL_OPTIONS[1]}
        studio.comic_shot_model_var = SimpleNamespace(
            get=lambda: labels["model"],
            set=lambda value: labels.__setitem__("model", value),
        )
        studio._select_comic_shot_model(SHOT_IMAGE_MODEL_OPTIONS[0])
        self.assertEqual(studio.state["comic"]["shot_image_model"], SEEDREAM_LITE_MODEL)
        self.assertEqual(studio.state["comic"]["resolution"], "2K")
        self.assertEqual(studio.comic_resolution_var.get(), "2K")


class NovelComboboxTests(unittest.TestCase):
    def test_novel_comboboxes_can_post_their_native_dropdowns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = Tk()
            root.withdraw()
            try:
                with patch("app.StateStore", lambda: StateStore(base)):
                    studio = StudioApp(root)
                    studio.navigate("novel")
                    root.update_idletasks()
                    combos: list[RoundedCombobox] = []

                    def collect(widget) -> None:
                        for child in widget.winfo_children():
                            if isinstance(child, RoundedCombobox):
                                combos.append(child)
                            collect(child)

                    collect(studio.main)
                    self.assertEqual(len(combos), 4)
                    for combo in combos:
                        combo.combo.tk.call("ttk::combobox::Post", str(combo.combo))
                        combo.combo.tk.call("ttk::combobox::Unpost", str(combo.combo))
                    studio.store.release_instance_lock()
            finally:
                root.destroy()

    def test_novel_header_keeps_prompt_preview_visible(self) -> None:
        studio = StudioApp.__new__(StudioApp)
        studio._clear_main = Mock()
        studio.navigate_highlight = Mock()
        studio._page_header = Mock()
        studio.preview_prompt = Mock()
        studio.import_novel = Mock()
        studio.rewrite_current = Mock()
        with self.assertRaises(AttributeError):
            studio.show_novel()
        actions = studio._page_header.call_args.args[2]
        self.assertEqual([label for label, _command, _kind in actions], ["导入小说", "查看提示词", "改写当前章"])
        self.assertIs(actions[1][1], studio.preview_prompt)

    def test_prompt_preview_button_always_opens_for_empty_and_pasted_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = Tk()
            root.geometry("1200x760-3000-3000")
            try:
                with patch("app.StateStore", lambda: StateStore(base)):
                    studio = StudioApp(root)
                    studio.navigate("novel")
                    root.update_idletasks()
                    buttons: list[RoundedButton] = []

                    def collect(widget) -> None:
                        for child in widget.winfo_children():
                            if isinstance(child, RoundedButton) and child.label_text == "查看提示词":
                                buttons.append(child)
                            collect(child)

                    collect(studio.main)
                    self.assertEqual(len(buttons), 2)
                    original_windows = set(root.winfo_children())
                    buttons[0]._invoke()
                    root.update_idletasks()
                    empty_dialogs = [window for window in root.winfo_children() if window not in original_windows]
                    self.assertEqual(len(empty_dialogs), 1)
                    self.assertEqual(empty_dialogs[0].title(), "小说解说提示词预览")
                    empty_dialogs[0].destroy()

                    studio.source_editor.insert("1.0", "第一章 雨夜\n\n林川推开门，看见失踪三年的姐姐。")
                    buttons[0]._invoke()
                    root.update_idletasks()
                    self.assertIsNotNone(studio.current_chapter_index)
                    pasted_dialogs = [window for window in root.winfo_children() if window not in original_windows]
                    self.assertEqual(len(pasted_dialogs), 1)
                    pasted_dialogs[0].destroy()
                    studio.store.release_instance_lock()
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
