from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tkinter import Tk, Toplevel

from app import StudioApp
from app import SEEDREAM_LITE_MODEL, SEEDREAM_PRO_MODEL, SHOT_IMAGE_MODEL_OPTIONS
from app import RoundedButton, RoundedCombobox
from core.storage import StateStore, new_asset_library, new_comic_project


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


class NewProjectDialogTests(unittest.TestCase):
    def test_confirm_button_stays_inside_dialog_at_scaled_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = Tk()
            root.tk.call("tk", "scaling", 2.0)
            root.geometry("1120x720-3000-3000")
            studio = None
            try:
                with patch("app.StateStore", lambda: StateStore(base)):
                    studio = StudioApp(root)
                    root.update()
                    existing_library = studio.state["asset_libraries"][0]
                    existing_library["characters"].append({"name": "其他项目角色"})
                    existing_library["scenes"].append({"name": "其他项目场景"})
                    studio.create_comic_project_dialog()
                    root.update()
                    dialogs = [widget for widget in root.winfo_children() if isinstance(widget, Toplevel)]
                    self.assertEqual(len(dialogs), 1)
                    dialog = dialogs[0]
                    buttons: list[RoundedButton] = []

                    def collect(widget) -> None:
                        for child in widget.winfo_children():
                            if isinstance(child, RoundedButton):
                                buttons.append(child)
                            collect(child)

                    collect(dialog)
                    confirm = next(button for button in buttons if button.label_text.startswith("创建并进入"))
                    self.assertTrue(confirm.winfo_ismapped())
                    self.assertLessEqual(confirm.winfo_rooty() + confirm.winfo_height(), dialog.winfo_rooty() + dialog.winfo_height())
                    self.assertTrue(dialog.bind("<Return>"))
                    self.assertTrue(dialog.bind("<Escape>"))
                    confirm._invoke()
                    root.update_idletasks()
                    created = studio.state["comic"]
                    self.assertIs(created["characters"], studio.state["shared_characters"])
                    self.assertIs(created["scenes"], studio.state["shared_scenes"])
                    self.assertEqual(created["characters"], [])
                    self.assertEqual(created["scenes"], [])
                    self.assertEqual(len(studio.state["asset_libraries"]), 2)
                    self.assertEqual(existing_library["characters"][0]["name"], "其他项目角色")
                    if dialog.winfo_exists():
                        dialog.destroy()
            finally:
                if studio is not None:
                    studio.store.release_instance_lock()
                root.destroy()


class AssetLibraryDeletionTests(unittest.TestCase):
    def test_dashboard_and_manager_keep_delete_actions_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = Tk()
            root.geometry("1180x760-3000-3000")
            studio = None
            try:
                with patch("app.StateStore", lambda: StateStore(base)):
                    studio = StudioApp(root)
                    root.update()

                    def button_labels(widget) -> list[str]:
                        labels: list[str] = []
                        for child in widget.winfo_children():
                            if isinstance(child, RoundedButton):
                                labels.append(child.label_text)
                            labels.extend(button_labels(child))
                        return labels

                    dashboard_labels = button_labels(studio.main)
                    self.assertIn("管理 / 删除人物场景项", dashboard_labels)
                    self.assertNotIn("删除人物场景项", dashboard_labels)
                    self.assertNotIn("建立新项目  →", dashboard_labels)

                    studio.open_asset_library_manager()
                    root.update()
                    dialogs = [widget for widget in root.winfo_children() if isinstance(widget, Toplevel)]
                    manager = next(dialog for dialog in dialogs if dialog.title() == "人物场景项管理")
                    manager_buttons: list[RoundedButton] = []

                    def collect_buttons(widget) -> None:
                        for child in widget.winfo_children():
                            if isinstance(child, RoundedButton):
                                manager_buttons.append(child)
                            collect_buttons(child)

                    collect_buttons(manager)
                    self.assertIn("永久删除所选项", [button.label_text for button in manager_buttons])
                    for button in manager_buttons:
                        self.assertTrue(button.winfo_ismapped())
                        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), manager.winfo_rooty() + manager.winfo_height())
                    manager.destroy()
            finally:
                if studio is not None:
                    studio.store.release_instance_lock()
                root.destroy()

    def test_delete_linked_library_removes_assets_and_preserves_project_story(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            character_dir = base / "shared_assets" / "libraries" / "library-old" / "characters"
            scene_dir = base / "shared_assets" / "libraries" / "library-old" / "scenes"
            character_dir.mkdir(parents=True)
            scene_dir.mkdir(parents=True)
            character_reference = character_dir / "林川_reference.png"
            character_candidate = character_dir / "林川_candidate.png"
            scene_reference = scene_dir / "雨夜街道_reference.png"
            for path in (character_reference, character_candidate, scene_reference):
                path.write_bytes(b"image")

            library = new_asset_library("都市系列")
            library["library_id"] = "library-old"
            library["characters"].append(
                {
                    "name": "林川",
                    "local_path": str(character_reference),
                    "candidate_path": str(character_candidate),
                }
            )
            library["scenes"].append(
                {
                    "name": "雨夜街道",
                    "local_path": str(scene_reference),
                    "candidate_path": "",
                }
            )
            project = new_comic_project("雨夜归来")
            project["asset_library_id"] = library["library_id"]
            project["characters"] = library["characters"]
            project["scenes"] = library["scenes"]
            project["cover"] = {"character": "林川", "scene": "雨夜街道"}
            project["shots"] = [
                {
                    "source_text": "林川在雨夜推开那扇门。",
                    "characters": ["林川"],
                    "scene": "雨夜街道",
                    "status": "已完成",
                    "local_path": str(base / "shot.png"),
                }
            ]

            studio = StudioApp.__new__(StudioApp)
            studio.state = {
                "asset_libraries": [library],
                "projects": [project],
                "active_project_id": project["project_id"],
                "comic": project,
                "shared_characters": library["characters"],
                "shared_scenes": library["scenes"],
            }
            studio.store = SimpleNamespace(base_dir=base, save=Mock())
            studio.current_comic_character_index = 0
            studio.current_comic_scene_index = 0

            with patch("app.messagebox.askyesno", return_value=True):
                deleted = studio._delete_asset_library(library)

            self.assertTrue(deleted)
            self.assertFalse(character_reference.exists())
            self.assertFalse(character_candidate.exists())
            self.assertFalse(scene_reference.exists())
            self.assertEqual(len(studio.state["asset_libraries"]), 1)
            replacement = studio.state["asset_libraries"][0]
            self.assertNotEqual(replacement["library_id"], "library-old")
            self.assertEqual(replacement["characters"], [])
            self.assertEqual(replacement["scenes"], [])
            self.assertEqual(project["asset_library_id"], replacement["library_id"])
            self.assertEqual(project["shots"][0]["source_text"], "林川在雨夜推开那扇门。")
            self.assertEqual(project["shots"][0]["characters"], [])
            self.assertEqual(project["shots"][0]["scene"], "")
            self.assertEqual(project["shots"][0]["status"], "待重新生成")
            self.assertEqual(project["cover"], {"character": "", "scene": ""})
            self.assertIs(project["characters"], replacement["characters"])
            self.assertIs(project["scenes"], replacement["scenes"])
            studio.store.save.assert_called_once_with(studio.state)

    def test_cancel_delete_keeps_library_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            library = new_asset_library("不能误删")
            studio = StudioApp.__new__(StudioApp)
            studio.state = {
                "asset_libraries": [library],
                "projects": [],
                "active_project_id": "",
                "comic": {"asset_library_id": library["library_id"]},
            }
            studio.store = SimpleNamespace(base_dir=base, save=Mock())
            with patch("app.messagebox.askyesno", return_value=False):
                deleted = studio._delete_asset_library(library)
            self.assertFalse(deleted)
            self.assertEqual(studio.state["asset_libraries"], [library])
            studio.store.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
