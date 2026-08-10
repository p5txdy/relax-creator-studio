from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import StudioApp


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


if __name__ == "__main__":
    unittest.main()
