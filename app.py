from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import zipfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    TOP,
    X,
    Y,
    BooleanVar,
    Canvas,
    DoubleVar,
    Entry,
    Frame,
    IntVar,
    Label,
    Listbox,
    Menu,
    PhotoImage,
    StringVar,
    Text,
    TclError,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    ttk,
)
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageOps, ImageTk

from core.ai_client import (
    PROVIDER_PRESETS,
    AIClientError,
    AIConfig,
    OpenAICompatibleClient,
    api_key_from_environment,
    infer_provider,
    provider_preset,
)
from core.comic_engine import (
    COMIC_COVER_OUTPUT_PLAN,
    ComicEngineError,
    build_ai_split_storyboard_prompt,
    build_character_prompt,
    build_cover_prompt,
    build_scene_prompt,
    character_reference_data,
    compose_shot_prompt,
    default_character,
    default_scene,
    enforce_character_reference_prompt,
    enforce_character_variant_prompt,
    enforce_scene_reference_prompt,
    export_comic_asset_pack,
    has_local_reference,
    import_comic_asset_pack,
    merge_storyboard_shots,
    parse_storyboard_response,
    replace_character_in_shots,
    replace_scene_in_shots,
    safe_filename,
    scene_reference_data,
    split_story_source_chunks,
    split_storyboard_shot,
    validate_ai_storyboard_split,
)
from core.seedream_client import (
    SEEDREAM_BASE_URL,
    SEEDREAM_LITE_MODEL,
    SEEDREAM_MODEL,
    SEEDREAM_PRO_1K_SIZES,
    SEEDREAM_PRO_MODEL,
    DoubaoSeedreamClient,
    SeedreamConfig,
)
from core.comic_video_engine import allocate_shot_durations, build_comic_video_command, load_srt, probe_audio_duration
from core.comic_presentation import DOUYIN_COMIC_MOTION, MOTION_MODE_OPTIONS, normalize_motion_mode
from core.jianying_engine import (
    JianyingEngineError,
    create_comic_jianying_draft,
    create_jianying_draft,
    detect_jianying_drafts_path,
    detect_jianying_executable,
    open_jianying,
)
from core.novel_engine import (
    NOVEL_COMMENTARY_MODE,
    NOVEL_COMMENTARY_STYLE,
    build_post_prompt,
    build_rewrite_prompt,
    chapter_records,
)
from core.secret_store import SecretStoreError, delete_api_key, load_api_key, save_api_key
from core.storage import StateStore, new_comic_project
from core.video_engine import (
    find_executable,
    probe_duration,
    run_export,
)


APP_NAME = "漫画推文"
APP_VERSION = "1.1"
BG = "#F3F5F7"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#EDF1F4"
INK = "#1D2935"
MUTED = "#697785"
SIDEBAR = "#18232D"
SIDEBAR_MUTED = "#AAB7C2"
ACCENT = "#45B8A4"
ACCENT_DARK = "#237D72"
WARM = "#E6A24F"
ERROR = "#D65B67"
BORDER = "#DCE3E8"
COMIC_CANVAS = "#F3F5F7"
COMIC_INSET = "#F8FAFB"
COMIC_MINT = "#E2F3EF"
COMIC_DARK_ALT = "#243743"
COMIC_STYLE_PRESETS = (
    "国风 3D 动漫，电影级光影，高细节",
    "日系 2D 动画，清晰线稿，细腻赛璐璐上色",
    "古风水墨漫画，东方美学，柔和光影",
    "现代都市条漫，写实动漫，高级电影调色",
    "韩系唯美二维漫画，漫画化精致五官，清晰线稿，柔和渐变上色，禁止真人照片与3D写实",
    "现代都市韩漫，二维韩系网络漫画插画，精致高颜值漫画人物，修长自然比例，清晰线稿，平滑赛璐璐与柔和渐变上色，干净漫画肤色块，戏剧化表情与氛围光，竖屏网漫构图，禁止真人照片、影视剧照与3D写实",
)
CHARACTER_BASE_NONE = "（不关联，创建独立人物）"
SHOT_IMAGE_MODEL_OPTIONS = (
    "Seedream 5.0 Lite（省钱推荐）",
    "Seedream 5.0 Pro（质量优先）",
)
SHOT_IMAGE_MODEL_IDS = {
    SHOT_IMAGE_MODEL_OPTIONS[0]: SEEDREAM_LITE_MODEL,
    SHOT_IMAGE_MODEL_OPTIONS[1]: SEEDREAM_PRO_MODEL,
}
SHOT_IMAGE_MODEL_LABELS = {model_id: label for label, model_id in SHOT_IMAGE_MODEL_IDS.items()}
SHOT_IMAGE_MODEL_RESOLUTIONS = {
    SEEDREAM_LITE_MODEL: ("2K", "3K"),
    SEEDREAM_PRO_MODEL: ("1K", "2K", "3K", "4K"),
}

_AA_ROUND_IMAGE_CACHE: OrderedDict[tuple[object, ...], ImageTk.PhotoImage] = OrderedDict()
_AA_ROUND_IMAGE_CACHE_LIMIT = 320


def _canvas_round_rect(canvas: Canvas, x1: float, y1: float, x2: float, y2: float, radius: float, **kwargs):
    """Draw an anti-aliased rounded rectangle by supersampling with Pillow."""
    width = max(1, int(round(x2 - x1)))
    height = max(1, int(round(y2 - y1)))
    radius = max(2.0, min(radius, width / 2, height / 2))
    scale = 4 if width * height <= 160_000 else 2
    fill = kwargs.pop("fill", None)
    outline = kwargs.pop("outline", None) or None
    line_width = max(1, int(kwargs.pop("width", 1) * scale))
    tags = kwargs.pop("tags", None)
    cache_key = (id(canvas.tk), width, height, round(radius, 2), fill, outline, line_width, scale)
    cacheable = width * height <= 160_000
    photo = _AA_ROUND_IMAGE_CACHE.get(cache_key) if cacheable else None
    if photo is None:
        image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (0, 0, width * scale - 1, height * scale - 1),
            radius=int(radius * scale),
            fill=fill,
            outline=outline,
            width=line_width,
        )
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image, master=canvas)
        if cacheable:
            _AA_ROUND_IMAGE_CACHE[cache_key] = photo
            if len(_AA_ROUND_IMAGE_CACHE) > _AA_ROUND_IMAGE_CACHE_LIMIT:
                _AA_ROUND_IMAGE_CACHE.popitem(last=False)
    elif cacheable:
        _AA_ROUND_IMAGE_CACHE.move_to_end(cache_key)
    cache = getattr(canvas, "_aa_round_images", None)
    if cache is None:
        cache = []
        canvas._aa_round_images = cache
    cache.append(photo)
    return canvas.create_image(x1, y1, image=photo, anchor="nw", tags=tags)


class RoundedCard(Canvas):
    """Canvas-backed card with a real rounded outline and a normal Frame interior."""

    def __init__(self, parent, *, surface: str, border: str, padx: int, pady: int, radius: int = 14) -> None:
        try:
            parent_bg = parent.cget("bg")
        except TclError:
            parent_bg = BG
        super().__init__(parent, bg=parent_bg, highlightthickness=0, borderwidth=0, takefocus=0)
        self.surface = surface
        self.border = border
        self.radius = radius
        self.inset = 6
        self.fixed_height: int | None = None
        self._last_request_size: tuple[int, int] | None = None
        self._last_draw_signature: tuple[object, ...] | None = None
        self._draw_after_id: str | None = None
        self.content = Frame(self, bg=surface, padx=padx, pady=pady)
        self.content_window = self.create_window(self.inset, self.inset, window=self.content, anchor="nw")
        self.bind("<Configure>", self._schedule_redraw, add="+")
        self.content.bind("<Configure>", self._sync_request, add="+")

    def _sync_request(self, _event=None) -> None:
        requested = (
            max(20, self.content.winfo_reqwidth() + self.inset * 2),
            self.fixed_height or max(20, self.content.winfo_reqheight() + self.inset * 2),
        )
        if requested != self._last_request_size:
            self._last_request_size = requested
            self.configure(width=requested[0], height=requested[1])

    def set_fixed_height(self, height: int) -> None:
        self.fixed_height = max(20, int(height))
        self._last_request_size = None
        self.configure(height=self.fixed_height)

    def _schedule_redraw(self, _event=None) -> None:
        if self._draw_after_id is not None:
            return
        try:
            self._draw_after_id = self.after_idle(self._redraw)
        except TclError:
            self._draw_after_id = None

    def _redraw(self, _event=None) -> None:
        self._draw_after_id = None
        try:
            if not self.winfo_exists():
                return
        except TclError:
            return
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        signature = (width, height, self.surface, self.border, self.radius)
        if signature == self._last_draw_signature:
            return
        self._last_draw_signature = signature
        self.delete("rounded_card")
        self._aa_round_images = []
        _canvas_round_rect(
            self,
            1,
            1,
            width - 1,
            height - 1,
            self.radius,
            fill=self.surface,
            outline=self.border,
            width=1,
            tags="rounded_card",
        )
        self.tag_lower("rounded_card")
        self.itemconfigure(
            self.content_window,
            width=max(1, width - self.inset * 2),
            height=max(1, height - self.inset * 2),
        )


class RoundedScrollbar(Canvas):
    """Compact vertical scrollbar with a rounded track and draggable thumb."""

    def __init__(self, parent, *, command) -> None:
        try:
            parent_bg = parent.cget("bg")
        except TclError:
            parent_bg = SURFACE
        super().__init__(parent, width=13, bg=parent_bg, highlightthickness=0, borderwidth=0, takefocus=0, cursor="hand2")
        self.command = command
        self.first = 0.0
        self.last = 1.0
        self.drag_offset: float | None = None
        self.hovered = False
        self._last_draw_signature: tuple[object, ...] | None = None
        self.bind("<Configure>", self._draw, add="+")
        self.bind("<Button-1>", self._press, add="+")
        self.bind("<B1-Motion>", self._drag, add="+")
        self.bind("<ButtonRelease-1>", self._release, add="+")
        self.bind("<Enter>", lambda _event: self._set_hover(True), add="+")
        self.bind("<Leave>", lambda _event: self._set_hover(False), add="+")

    def set(self, first, last) -> None:
        self.first = max(0.0, min(float(first), 1.0))
        self.last = max(self.first, min(float(last), 1.0))
        self._draw()

    def _thumb_bounds(self) -> tuple[float, float]:
        height = max(1, self.winfo_height())
        track_top, track_bottom = 3.0, max(4.0, height - 3.0)
        track_height = track_bottom - track_top
        visible = max(0.0, self.last - self.first)
        thumb_height = min(track_height, max(28.0, track_height * visible))
        travel = max(0.0, track_height - thumb_height)
        denominator = max(0.0001, 1.0 - visible)
        top = track_top + travel * min(1.0, self.first / denominator)
        return top, top + thumb_height

    def _draw(self, _event=None) -> None:
        width = max(8, self.winfo_width())
        height = max(8, self.winfo_height())
        signature = (width, height, round(self.first, 5), round(self.last, 5), self.hovered)
        if signature == self._last_draw_signature:
            return
        self._last_draw_signature = signature
        self.delete("all")
        center = width / 2
        self.create_line(center, 5, center, max(5, height - 5), width=6, fill=SURFACE_ALT, capstyle="round")
        if self.last - self.first < 0.999:
            top, bottom = self._thumb_bounds()
            color = ACCENT_DARK if self.hovered else "#86AAA4"
            self.create_line(center, top, center, bottom, width=6, fill=color, capstyle="round")

    def _set_hover(self, value: bool) -> None:
        self.hovered = value
        self._draw()

    def _press(self, event) -> None:
        top, bottom = self._thumb_bounds()
        if top <= event.y <= bottom:
            self.drag_offset = event.y - top
            return
        visible = max(0.01, self.last - self.first)
        self.command("moveto", max(0.0, min(1.0 - visible, event.y / max(1, self.winfo_height()) - visible / 2)))

    def _drag(self, event) -> None:
        if self.drag_offset is None:
            return
        visible = max(0.01, self.last - self.first)
        top, bottom = self._thumb_bounds()
        thumb_height = bottom - top
        travel = max(1.0, self.winfo_height() - 6.0 - thumb_height)
        target = (event.y - self.drag_offset - 3.0) / travel
        self.command("moveto", max(0.0, min(1.0 - visible, target * (1.0 - visible))))

    def _release(self, _event=None) -> None:
        self.drag_offset = None


class RoundedCombobox(Canvas):
    """Rounded shell around a themed ttk Combobox, preserving its familiar API."""

    def __init__(self, parent, *, textvariable=None, values=(), state="normal", width=None, style=None, **kwargs) -> None:
        try:
            parent_bg = parent.cget("bg")
        except TclError:
            parent_bg = SURFACE
        pixel_width = max(92, (int(width) * 8 + 38) if width else 168)
        super().__init__(parent, width=pixel_width, height=39, bg=parent_bg, highlightthickness=0, borderwidth=0, takefocus=0)
        self.combo = ttk.Combobox(
            self,
            textvariable=textvariable,
            values=values,
            state=state,
            width=width,
            style="Studio.Inner.TCombobox",
            **kwargs,
        )
        self.combo_window = self.create_window(3, 3, window=self.combo, anchor="nw")
        self._last_draw_signature: tuple[int, int] | None = None
        super().bind("<Configure>", self._redraw, add="+")

    def _redraw(self, _event=None) -> None:
        width = max(10, self.winfo_width())
        height = max(10, self.winfo_height())
        signature = (width, height)
        if signature == self._last_draw_signature:
            return
        self._last_draw_signature = signature
        self.delete("combo_shell")
        self._aa_round_images = []
        _canvas_round_rect(self, 1, 1, width - 1, height - 1, 10, fill=COMIC_INSET, outline=BORDER, width=1, tags="combo_shell")
        self.tag_lower("combo_shell")
        self.itemconfigure(self.combo_window, width=max(1, width - 6), height=max(1, height - 6))

    def bind(self, sequence=None, func=None, add=None):
        if sequence == "<Configure>":
            return super().bind(sequence, func, add)
        return self.combo.bind(sequence, func, add)

    def configure(self, cnf=None, **kwargs):
        combo_keys = {"values", "state", "textvariable", "width", "height", "font", "justify"}
        combo_options = {key: kwargs.pop(key) for key in list(kwargs) if key in combo_keys}
        if cnf:
            combo_options.update(cnf)
        if combo_options:
            self.combo.configure(**combo_options)
        if kwargs:
            return super().configure(**kwargs)
        return None

    config = configure

    def get(self):
        return self.combo.get()

    def set(self, value) -> None:
        self.combo.set(value)

    def current(self, index=None):
        return self.combo.current(index) if index is not None else self.combo.current()


class RoundedEntry(Canvas):
    """Single-line input with a rounded border and a borderless native editor."""

    def __init__(self, parent, *, textvariable, width=None) -> None:
        try:
            parent_bg = parent.cget("bg")
        except TclError:
            parent_bg = SURFACE
        pixel_width = max(96, int(width) * 9 + 28) if width else 180
        super().__init__(parent, width=pixel_width, height=38, bg=parent_bg, highlightthickness=0, borderwidth=0, takefocus=0)
        self.entry = Entry(
            self,
            textvariable=textvariable,
            width=width,
            bg=COMIC_INSET,
            fg=INK,
            insertbackground=INK,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 10),
        )
        self.entry_window = self.create_window(10, 3, window=self.entry, anchor="nw")
        self._last_draw_signature: tuple[int, int] | None = None
        super().bind("<Configure>", self._redraw, add="+")

    def _redraw(self, _event=None) -> None:
        width = max(10, self.winfo_width())
        height = max(10, self.winfo_height())
        signature = (width, height)
        if signature == self._last_draw_signature:
            return
        self._last_draw_signature = signature
        self.delete("entry_shell")
        self._aa_round_images = []
        _canvas_round_rect(self, 1, 1, width - 1, height - 1, 10, fill=COMIC_INSET, outline=BORDER, width=1, tags="entry_shell")
        self.tag_lower("entry_shell")
        self.itemconfigure(self.entry_window, width=max(1, width - 20), height=max(1, height - 6))

    def bind(self, sequence=None, func=None, add=None):
        if sequence == "<Configure>":
            return super().bind(sequence, func, add)
        return self.entry.bind(sequence, func, add)

    def configure(self, cnf=None, **kwargs):
        entry_keys = {"show", "state", "font", "justify", "validate", "validatecommand"}
        entry_options = {key: kwargs.pop(key) for key in list(kwargs) if key in entry_keys}
        if cnf:
            entry_options.update(cnf)
        if entry_options:
            self.entry.configure(**entry_options)
        if kwargs:
            return super().configure(**kwargs)
        return None

    config = configure

    def focus_set(self):
        return self.entry.focus_set()

    def selection_range(self, start, end):
        return self.entry.selection_range(start, end)

    def get(self):
        return self.entry.get()


class RoundedButton(Canvas):
    """Small dependency-free rounded action button."""

    def __init__(self, parent, *, text: str, command, bg: str, fg: str, active: str, width: int | None = None) -> None:
        try:
            parent_bg = parent.cget("bg")
        except TclError:
            parent_bg = SURFACE
        text_width = sum(14 if ord(character) > 127 else 8 for character in text)
        pixel_width = width * 13 + 28 if width else max(74, text_width + 30)
        super().__init__(parent, width=pixel_width, height=38, bg=parent_bg, highlightthickness=0, borderwidth=0, cursor="hand2", takefocus=1)
        self.label_text = text
        self.command = command
        self.normal_bg = bg
        self.active_bg = active
        self.fg = fg
        self.hovered = False
        self._last_draw_signature: tuple[object, ...] | None = None
        self.bind("<Configure>", self._draw, add="+")
        self.bind("<Enter>", lambda _event: self._set_hover(True), add="+")
        self.bind("<Leave>", lambda _event: self._set_hover(False), add="+")
        self.bind("<ButtonRelease-1>", self._invoke, add="+")
        self.bind("<Return>", self._invoke, add="+")
        self.bind("<space>", self._invoke, add="+")

    def _draw(self, _event=None) -> None:
        width = max(10, self.winfo_width())
        height = max(10, self.winfo_height())
        color = self.active_bg if self.hovered else self.normal_bg
        signature = (width, height, color, self.fg, self.label_text)
        if signature == self._last_draw_signature:
            return
        self._last_draw_signature = signature
        self.delete("all")
        self._aa_round_images = []
        _canvas_round_rect(self, 1, 1, width - 1, height - 1, 10, fill=color, outline="")
        self.create_text(width / 2, height / 2, text=self.label_text, fill=self.fg, font=("Microsoft YaHei UI", 9, "bold"))

    def _set_hover(self, value: bool) -> None:
        self.hovered = value
        self._draw()

    def _invoke(self, _event=None) -> None:
        if callable(self.command):
            self.command()


def read_document(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        paragraphs: list[str] = []
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        for paragraph in root.iter(namespace + "p"):
            text = "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
            if text.strip():
                paragraphs.append(text.strip())
        return "\n\n".join(paragraphs)
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class StudioInstanceRunningError(RuntimeError):
    pass


class StudioApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.store = StateStore()
        if not self.store.acquire_instance_lock():
            raise StudioInstanceRunningError("漫画推文已经在运行。请先切换到已打开的窗口，避免两个实例互相覆盖项目数据。")
        self.state = self.store.load()
        settings = self.state["settings"]
        initial_provider = settings.get("provider") or infer_provider(settings.get("base_url", ""), settings.get("model", ""))
        self.active_api_provider = initial_provider if initial_provider in {item.id for item in PROVIDER_PRESETS} else "custom"
        self.api_keys: dict[str, str] = {}
        self.remember_api_key = BooleanVar(value=bool(settings.get("remember_api_key", True)))
        self.api_key = StringVar(value=self._load_provider_api_key(self.active_api_provider))
        self.remember_ark_api_key = BooleanVar(value=bool(settings.get("remember_ark_api_key", True)))
        try:
            saved_ark_key = load_api_key("ark") if self.remember_ark_api_key.get() else ""
        except SecretStoreError:
            saved_ark_key = ""
        self.ark_api_key = StringVar(value=saved_ark_key or os.getenv("ARK_API_KEY", ""))
        self.current_page = "dashboard"
        self.nav_buttons: dict[str, object] = {}
        self.project_tree: ttk.Treeview | None = None
        self.bus: queue.Queue[tuple[str, object]] = queue.Queue()
        self.bus_handler = None
        self.video_tree: ttk.Treeview | None = None
        self.novel_list: Listbox | None = None
        self.source_editor: Text | None = None
        self.result_editor: Text | None = None
        self.post_editor: Text | None = None
        self.current_chapter_index: int | None = None
        self.comic_source_editor: Text | None = None
        self.comic_character_list: Listbox | None = None
        self.comic_scene_list: Listbox | None = None
        self.comic_shot_tree: ttk.Treeview | None = None
        self.comic_shot_prompt_editor: Text | None = None
        self.current_comic_character_index: int | None = None
        self.current_comic_scene_index: int | None = None
        self.current_comic_shot_index: int | None = None
        self.comic_preview_image: PhotoImage | None = None
        self.comic_character_preview_canvas: Canvas | None = None
        self.comic_scene_preview_canvas: Canvas | None = None
        self.comic_character_preview_title: Label | None = None
        self.comic_scene_preview_title: Label | None = None
        self.comic_character_base_var: StringVar | None = None
        self.comic_character_base_combo: RoundedCombobox | None = None
        self.comic_shot_tree_with_previews = False
        self.comic_shot_preview_images: dict[int, ImageTk.PhotoImage] = {}
        self.comic_shot_loaded_preview_signatures: dict[int, tuple[object, ...]] = {}
        self.comic_shot_preview_after_id: str | None = None
        self.comic_storyboard_canvas: Canvas | None = None
        self.comic_storyboard_body: Frame | None = None
        self.comic_storyboard_window: int | None = None
        self.comic_storyboard_selected_indices: set[int] = set()
        self.comic_storyboard_selection_vars: dict[int, StringVar] = {}
        self.comic_storyboard_prompt_editors: dict[int, Text] = {}
        self.comic_storyboard_row_widgets: dict[int, dict[str, object]] = {}
        self.comic_storyboard_page = 0
        self.comic_storyboard_page_size = 12
        self.comic_storyboard_page_label: Label | None = None
        self.comic_asset_autosave_after_id: str | None = None
        self.state_save_after_id: str | None = None
        self.thumbnail_photo_cache: OrderedDict[tuple[object, ...], ImageTk.PhotoImage] = OrderedDict()
        self.thumbnail_photo_cache_limit = 240
        self._loading_comic_asset_editor = False
        self._loading_comic_shot_editor = False
        self.is_busy = False

        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("1440x900")
        self.root.minsize(1120, 720)
        self.root.configure(bg=BG)
        self._configure_styles()
        self._build_shell()
        self.show_dashboard()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(120, self._drain_bus)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Studio.Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=INK, rowheight=38, borderwidth=0, relief="flat", font=("Microsoft YaHei UI", 9))
        style.configure("Studio.Treeview.Heading", background=SURFACE_ALT, foreground=MUTED, relief="flat", padding=(8, 9), font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Studio.Treeview", background=[("selected", "#D7EEE9")], foreground=[("selected", INK)])
        style.configure("Studio.Preview.Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=INK, rowheight=154, borderwidth=0, relief="flat", font=("Microsoft YaHei UI", 9))
        style.configure("Studio.Preview.Treeview.Heading", background=SURFACE_ALT, foreground=MUTED, relief="flat", padding=(8, 9), font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Studio.Preview.Treeview", background=[("selected", "#D7EEE9")], foreground=[("selected", INK)])
        style.configure(
            "Studio.Inner.TCombobox",
            padding=(9, 7),
            fieldbackground=COMIC_INSET,
            background=COMIC_INSET,
            foreground=INK,
            bordercolor=COMIC_INSET,
            lightcolor=COMIC_INSET,
            darkcolor=COMIC_INSET,
            arrowcolor=ACCENT_DARK,
            arrowsize=16,
            relief="flat",
        )
        style.map(
            "Studio.Inner.TCombobox",
            fieldbackground=[("readonly", COMIC_INSET), ("focus", SURFACE)],
            background=[("readonly", COMIC_INSET), ("active", COMIC_MINT)],
            arrowcolor=[("active", ACCENT_DARK), ("pressed", SIDEBAR)],
            foreground=[("disabled", MUTED), ("readonly", INK)],
        )
        self.root.option_add("*TCombobox*Listbox.background", SURFACE)
        self.root.option_add("*TCombobox*Listbox.foreground", INK)
        self.root.option_add("*TCombobox*Listbox.selectBackground", COMIC_MINT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", ACCENT_DARK)
        # Tcl parses a font with spaces as a list. Braces keep the complete
        # Windows font family together when the native combobox posts its menu.
        self.root.option_add("*TCombobox*Listbox.font", "{Microsoft YaHei UI} 9")
        style.configure("Studio.Horizontal.TProgressbar", background=ACCENT, troughcolor=SURFACE_ALT, borderwidth=0)
        style.configure("Studio.TPanedwindow", background=BG)

    def _build_shell(self) -> None:
        self.sidebar = Frame(self.root, bg=SIDEBAR, width=236)
        self.sidebar.pack(side=LEFT, fill=Y)
        self.sidebar.pack_propagate(False)

        brand = Frame(self.sidebar, bg=SIDEBAR)
        brand.pack(fill=X, padx=24, pady=(28, 34))
        Label(brand, text="◆", bg=SIDEBAR, fg=ACCENT, font=("Segoe UI Symbol", 18, "bold")).pack(anchor="w")
        Label(brand, text=APP_NAME, bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", pady=(8, 2))
        Label(brand, text="AI COMIC STUDIO", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")

        nav_items = [
            ("dashboard", "⌂  项目主页"),
            ("comic", "▣  漫画工作台"),
            ("novel", "✎  AI 小说改文"),
            ("settings", "⚙  模型与 API"),
        ]
        for key, label in nav_items:
            button = Label(
                self.sidebar,
                text=label,
                bg=SIDEBAR,
                fg=SIDEBAR_MUTED,
                font=("Microsoft YaHei UI", 11),
                padx=24,
                pady=13,
                anchor="w",
                cursor="hand2",
            )
            button.pack(fill=X, padx=10, pady=2)
            button.bind("<Button-1>", lambda _event, page=key: self.navigate(page))
            self.nav_buttons[key] = button

        footer = Frame(self.sidebar, bg=SIDEBAR)
        footer.pack(side="bottom", fill=X, padx=24, pady=24)
        Label(footer, text=f"版本 {APP_VERSION}", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 6))
        Label(footer, text="多项目 · 共享角色库", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        self.tool_status = Label(footer, text="正在检查工具…", bg=SIDEBAR, fg=WARM, font=("Microsoft YaHei UI", 9))
        self.tool_status.pack(anchor="w", pady=(6, 0))
        self._refresh_tool_status()

        self.main = Frame(self.root, bg=BG)
        self.main.pack(side=LEFT, fill=BOTH, expand=True)

    def _refresh_tool_status(self) -> None:
        ready = bool(self.api_key.get().strip() and self.ark_api_key.get().strip())
        self.tool_status.configure(text="● AI 服务已配置" if ready else "● AI Key 待配置", fg=ACCENT if ready else WARM)

    def navigate(self, page: str) -> None:
        if self.is_busy and page != self.current_page:
            messagebox.showinfo("任务进行中", "当前任务完成后再切换工作台。")
            return
        self._save_current_editors()
        self.current_page = page
        for key, button in self.nav_buttons.items():
            button.configure(bg=COMIC_DARK_ALT if key == page else SIDEBAR, fg="white" if key == page else SIDEBAR_MUTED)
        if page == "dashboard":
            self.show_dashboard()
        elif page == "comic":
            if not self.state.get("active_project_id"):
                messagebox.showinfo("请先建立项目", "制作新推文前需要先在项目主页建立一个项目。")
                self.current_page = "dashboard"
                self.show_dashboard()
            else:
                self.show_comic()
        elif page == "novel":
            self.show_novel()
        else:
            self.show_settings()

    def _clear_main(self) -> None:
        cover_dialog = getattr(self, "comic_cover_dialog", None)
        if cover_dialog is not None:
            try:
                if cover_dialog.winfo_exists():
                    cover_dialog.destroy()
            except TclError:
                pass
        for child in self.main.winfo_children():
            child.destroy()
        self.video_tree = None
        self.novel_list = None
        self.source_editor = None
        self.result_editor = None
        self.post_editor = None
        self.project_tree = None
        self.comic_source_editor = None
        self.comic_character_list = None
        self.comic_shot_tree = None
        self.comic_shot_prompt_editor = None
        self.comic_character_description_editor = None
        self.comic_character_prompt_editor = None
        self.comic_character_base_var = None
        self.comic_character_base_combo = None
        self.comic_shot_source_label = None
        self.comic_count_label = None
        self.comic_generation_detail_label = None
        self.comic_generation_count_label = None
        self.comic_resolution_buttons: dict[str, RoundedButton] = {}
        self.comic_resolution_hint_var = StringVar()
        self.comic_cover_dialog = None
        self.comic_cover_preview_canvas = None
        self.comic_cover_preview_canvases = {}
        self.comic_cover_prompt_editor = None
        self.comic_cover_status_label = None
        self.comic_cover_dialog_status_label = None
        self.bus_handler = None

    def _page_header(self, title: str, subtitle: str, actions: list[tuple[str, object, str]] | None = None) -> Frame:
        header = Frame(self.main, bg=BG)
        header.pack(fill=X, padx=34, pady=(28, 18))
        text_area = Frame(header, bg=BG)
        text_area.pack(side=LEFT)
        Label(text_area, text="COMIC CREATOR · WORKSPACE", bg=BG, fg=ACCENT_DARK, font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 3))
        Label(text_area, text=title, bg=BG, fg=INK, font=("Microsoft YaHei UI", 23, "bold")).pack(anchor="w")
        Label(text_area, text=subtitle, bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(5, 0))
        if actions:
            action_area = Frame(header, bg=BG)
            action_area.pack(side=RIGHT, pady=5)
            for label, command, kind in actions:
                self._button(action_area, label, command, kind=kind).pack(side=LEFT, padx=(8, 0))
        return header

    def _card(self, parent, *, bg: str = SURFACE, padx: int = 20, pady: int = 18) -> RoundedCard:
        return RoundedCard(parent, surface=bg, border=BORDER, padx=padx, pady=pady)

    def _button(self, parent, text: str, command, *, kind: str = "primary", width: int | None = None):
        palette = {
            "primary": ("#256F8F", "white", "#1B5B78"),
            "accent": (ACCENT, SIDEBAR, "#379B8B"),
            "ghost": (SURFACE_ALT, INK, "#DDE5EA"),
            "danger": ("#FBEAEC", ERROR, "#F4DADD"),
            "dark": (SIDEBAR, "white", COMIC_DARK_ALT),
            "glass": (COMIC_DARK_ALT, "white", "#314C5B"),
        }
        bg, fg, active = palette[kind]
        return RoundedButton(parent, text=text, command=command, bg=bg, fg=fg, active=active, width=width)

    def _entry(self, parent, variable: StringVar | DoubleVar | IntVar, width: int | None = None):
        return RoundedEntry(parent, textvariable=variable, width=width)

    def _field_label(self, parent, text: str) -> Label:
        return Label(parent, text=text, bg=parent.cget("bg"), fg=MUTED, font=("Microsoft YaHei UI", 9))

    def _rounded_widget_shell(self, parent, *, bg: str = COMIC_INSET, fixed_height: int | None = None) -> tuple[RoundedCard, Frame]:
        outer = RoundedCard(parent, surface=bg, border=BORDER, padx=0, pady=0, radius=11)
        if fixed_height:
            outer.set_fixed_height(fixed_height)
        return outer, outer.content

    def _cached_thumbnail_photo(self, key: tuple[object, ...]) -> ImageTk.PhotoImage | None:
        photo = self.thumbnail_photo_cache.get(key)
        if photo is not None:
            self.thumbnail_photo_cache.move_to_end(key)
        return photo

    def _remember_thumbnail_photo(self, key: tuple[object, ...], photo: ImageTk.PhotoImage) -> ImageTk.PhotoImage:
        self.thumbnail_photo_cache[key] = photo
        self.thumbnail_photo_cache.move_to_end(key)
        while len(self.thumbnail_photo_cache) > self.thumbnail_photo_cache_limit:
            self.thumbnail_photo_cache.popitem(last=False)
        return photo

    def _render_local_image(self, canvas: Canvas | None, path: str, *, placeholder: str, max_size: tuple[int, int] | None = None) -> bool:
        if not canvas:
            return False
        width = max_size[0] if max_size else max(80, canvas.winfo_width())
        height = max_size[1] if max_size else max(80, canvas.winfo_height())
        image_path = Path(path) if path else None
        if not image_path or not image_path.is_file():
            render_key = ("placeholder", placeholder, width, height)
            if getattr(canvas, "_preview_cache_key", None) == render_key:
                return False
            canvas.delete("all")
            canvas.create_text(width / 2, height / 2, text=placeholder, fill=MUTED, width=max(80, width - 28), justify="center", font=("Microsoft YaHei UI", 9))
            canvas._preview_photo = None
            canvas._preview_cache_key = render_key
            return False
        try:
            stat = image_path.stat()
            render_key = ("local", str(image_path.absolute()), stat.st_mtime_ns, stat.st_size, width, height)
            if getattr(canvas, "_preview_cache_key", None) == render_key:
                return True
            photo = self._cached_thumbnail_photo(render_key)
            if photo is None:
                with Image.open(image_path) as source:
                    image = ImageOps.exif_transpose(source).convert("RGBA")
                    image.thumbnail((max(20, width - 16), max(20, height - 16)), Image.Resampling.LANCZOS)
                photo = self._remember_thumbnail_photo(render_key, ImageTk.PhotoImage(image, master=canvas))
            canvas.delete("all")
            canvas.create_image(width / 2, height / 2, image=photo, anchor="center")
            canvas._preview_photo = photo
            canvas._preview_cache_key = render_key
            return True
        except (OSError, ValueError):
            canvas.delete("all")
            canvas.create_text(width / 2, height / 2, text=f"图片读取失败\n{image_path.name}", fill=ERROR, width=max(80, width - 28), justify="center", font=("Microsoft YaHei UI", 9))
            canvas._preview_photo = None
            canvas._preview_cache_key = ("error", str(image_path), width, height)
            return False

    def _asset_preview_panel(self, parent, *, title: str, command) -> tuple[Canvas, Label]:
        outer = self._card(parent, bg=COMIC_INSET, padx=12, pady=12)
        outer.set_fixed_height(220)
        outer.pack(fill=X, pady=(12, 10))
        host = outer.winfo_children()[0]
        preview = Canvas(host, width=250, height=190, bg=SURFACE_ALT, highlightthickness=0, borderwidth=0, cursor="hand2")
        preview.pack(side=LEFT)
        preview.bind("<Button-1>", lambda _event: command())
        info = Frame(host, bg=COMIC_INSET)
        info.pack(side=LEFT, fill=BOTH, expand=True, padx=(16, 0))
        title_label = Label(info, text=title, bg=COMIC_INSET, fg=INK, anchor="w", justify=LEFT, font=("Microsoft YaHei UI", 11, "bold"))
        title_label.pack(fill=X, pady=(6, 5))
        Label(info, text="确认后的参考图会直接显示在这里；有新候选时优先展示候选图。点击图片可放大查看。", bg=COMIC_INSET, fg=MUTED, wraplength=360, justify=LEFT, font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        self._button(info, "放大查看", command, kind="ghost").pack(anchor="w", pady=(14, 0))
        return preview, title_label

    def _pack_vertical_scroller(self, shell: Frame, widget, *, fill=BOTH, expand: bool = True) -> RoundedScrollbar:
        """Pack a long-form widget with a visible vertical scrollbar."""
        scrollbar = RoundedScrollbar(shell, command=widget.yview)
        widget.configure(yscrollcommand=scrollbar.set)
        try:
            widget.configure(highlightthickness=0, borderwidth=0)
        except TclError:
            pass
        widget.pack(side=LEFT, fill=fill, expand=expand)
        scrollbar.pack(side=RIGHT, fill=Y)
        return scrollbar

    def _scrollable_content(self, parent, *, bg: str = BG) -> tuple[Frame, Canvas]:
        """Create a vertically scrollable page that follows the available width."""
        shell = Frame(parent, bg=bg)
        shell.pack(fill=BOTH, expand=True)
        canvas = Canvas(shell, bg=bg, highlightthickness=0, borderwidth=0)
        scrollbar = RoundedScrollbar(shell, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
        content = Frame(canvas, bg=bg)
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        return content, canvas

    def _bind_page_mousewheel(self, root_widget, canvas: Canvas) -> None:
        def scroll(event):
            if getattr(event, "delta", 0):
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            elif getattr(event, "num", 0) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", 0) == 5:
                canvas.yview_scroll(1, "units")
            return "break"

        def bind_tree(widget) -> None:
            if not isinstance(widget, (Text, Listbox, ttk.Treeview, RoundedCombobox)):
                widget.bind("<MouseWheel>", scroll, add="+")
                widget.bind("<Button-4>", scroll, add="+")
                widget.bind("<Button-5>", scroll, add="+")
            for child in widget.winfo_children():
                bind_tree(child)

        bind_tree(root_widget)

    def show_dashboard(self) -> None:
        self._clear_main()
        self.current_page = "dashboard"
        self.navigate_highlight("dashboard")
        self._page_header(
            "漫画推文项目",
            "每条推文使用独立项目保存小说、场景、分镜、图片和成片；已定妆角色在所有项目间共享。",
            [("AI 小说改文", lambda: self.navigate("novel"), "ghost"), ("+ 新建推文项目", self.create_comic_project_dialog, "accent")],
        )

        body = Frame(self.main, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=34, pady=(2, 28))
        projects = [item for item in self.state.get("projects", []) if isinstance(item, dict)]
        shared_characters = [item for item in self.state.get("shared_characters", []) if isinstance(item, dict)]

        summary_outer = self._card(body, bg=SIDEBAR, padx=26, pady=20)
        summary_outer.pack(fill=X)
        summary = summary_outer.winfo_children()[0]
        summary_text = Frame(summary, bg=SIDEBAR)
        summary_text.pack(side=LEFT, fill=X, expand=True)
        Label(summary_text, text="PROJECT LIBRARY · LOCAL WORKSPACE", bg=SIDEBAR, fg=ACCENT, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        Label(summary_text, text=f"{len(projects)} 个推文项目  ·  {len(shared_characters)} 个共享角色", bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w", pady=(5, 0))
        Label(summary_text, text="创建项目后再导入小说；切换项目不会清空其他项目，也不会复制角色定妆。", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(5, 0))
        self._button(summary, "建立新项目  →", self.create_comic_project_dialog, kind="accent").pack(side=RIGHT, padx=(18, 0))

        list_outer = self._card(body, padx=22, pady=18)
        list_outer.pack(fill=BOTH, expand=True, pady=(16, 0))
        listing = list_outer.winfo_children()[0]
        list_header = Frame(listing, bg=SURFACE)
        list_header.pack(fill=X, pady=(0, 12))
        Label(list_header, text="本地项目", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 13, "bold")).pack(side=LEFT)
        Label(list_header, text="双击项目可继续制作", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side=LEFT, padx=(10, 0))

        if projects:
            tree_outer, tree_shell = self._rounded_widget_shell(listing, bg=SURFACE)
            tree_outer.pack(fill=BOTH, expand=True)
            self.project_tree = ttk.Treeview(tree_shell, columns=("name", "progress", "assets", "updated"), show="headings", style="Studio.Treeview", selectmode="browse")
            self.project_tree.heading("name", text="项目名称")
            self.project_tree.heading("progress", text="制作进度")
            self.project_tree.heading("assets", text="项目资产")
            self.project_tree.heading("updated", text="最近保存")
            self.project_tree.column("name", width=290, anchor="w")
            self.project_tree.column("progress", width=190, anchor="w")
            self.project_tree.column("assets", width=230, anchor="w")
            self.project_tree.column("updated", width=170, anchor="center")
            for project in sorted(projects, key=lambda item: str(item.get("updated_at", "")), reverse=True):
                shots = list(project.get("shots", []))
                images = sum(1 for shot in shots if Path(str(shot.get("local_path", ""))).is_file())
                draft_ready = Path(str(project.get("jianying_draft_path", ""))).is_dir()
                progress = "尚未导入小说" if not str(project.get("source_text", "")).strip() else ("剪映草稿已完成" if draft_ready else f"分镜图片 {images}/{len(shots)}")
                assets = f"{len(project.get('scenes', []))} 场景  ·  {len(shots)} 分镜"
                updated = str(project.get("updated_at", "")).replace("T", " ")[:16] or "—"
                project_id = str(project.get("project_id", ""))
                self.project_tree.insert("", END, iid=project_id, values=(project.get("project_name", "未命名项目"), progress, assets, updated))
            self._pack_vertical_scroller(tree_shell, self.project_tree)
            self.project_tree.bind("<Double-1>", lambda _event: self.open_selected_comic_project())
            active_id = str(self.state.get("active_project_id", ""))
            if active_id and self.project_tree.exists(active_id):
                self.project_tree.selection_set(active_id)
                self.project_tree.focus(active_id)
            actions = Frame(listing, bg=SURFACE)
            actions.pack(fill=X, pady=(12, 0))
            self._button(actions, "删除所选项目", self.delete_selected_comic_project, kind="danger").pack(side=LEFT)
            self._button(actions, "打开所选项目  →", self.open_selected_comic_project, kind="primary").pack(side=RIGHT)
        else:
            empty = Frame(listing, bg=COMIC_INSET, padx=28, pady=48)
            empty.pack(fill=BOTH, expand=True)
            Label(empty, text="还没有漫画推文项目", bg=COMIC_INSET, fg=INK, font=("Microsoft YaHei UI", 17, "bold")).pack()
            Label(empty, text="先建立一个项目，再导入小说并生成角色、场景和分镜。", bg=COMIC_INSET, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(pady=(8, 16))
            self._button(empty, "+ 建立第一个项目", self.create_comic_project_dialog, kind="accent").pack()

    def _metric_row(self, parent, metrics: list[tuple[str, object]]) -> None:
        row = Frame(parent, bg=SURFACE)
        row.pack(fill=X, pady=(24, 0))
        for label, value in metrics:
            block = Frame(row, bg=SURFACE)
            block.pack(side=LEFT, padx=(0, 38))
            Label(block, text=str(value), bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
            Label(block, text=label, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w")

    def create_comic_project_dialog(self) -> None:
        if self.is_busy:
            return
        dialog = Toplevel(self.root)
        dialog.title("新建漫画推文项目")
        dialog.geometry("560x410")
        dialog.resizable(False, False)
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.grab_set()
        Label(dialog, text="建立新推文项目", bg=BG, fg=INK, font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w", padx=28, pady=(26, 5))
        Label(dialog, text="小说、场景、分镜和成片按项目隔离；共享角色可以在所有项目中直接调用。", bg=BG, fg=MUTED, wraplength=500, justify=LEFT, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=28)
        card_outer = self._card(dialog, padx=20, pady=18)
        card_outer.pack(fill=X, padx=28, pady=20)
        card = card_outer.winfo_children()[0]
        name_var = StringVar(value=f"漫画推文 {len(self.state.get('projects', [])) + 1}")
        style_var = StringVar(value=COMIC_STYLE_PRESETS[0])
        aspect_var = StringVar(value="9:16")
        self._field_label(card, "项目名称").pack(anchor="w", pady=(0, 5))
        name_entry = self._entry(card, name_var)
        name_entry.pack(fill=X, ipady=7)
        self._field_label(card, "统一画风").pack(anchor="w", pady=(13, 5))
        RoundedCombobox(card, textvariable=style_var, values=COMIC_STYLE_PRESETS).pack(fill=X)
        self._field_label(card, "画幅").pack(anchor="w", pady=(13, 5))
        RoundedCombobox(card, textvariable=aspect_var, values=["9:16", "4:5", "1:1", "16:9"], state="readonly", width=10).pack(anchor="w")

        def create() -> None:
            name = name_var.get().strip()
            if not name:
                messagebox.showinfo("需要项目名称", "请先填写项目名称。", parent=dialog)
                return
            project = new_comic_project(name)
            project["art_style"] = style_var.get().strip() or COMIC_STYLE_PRESETS[0]
            project["aspect"] = aspect_var.get().strip() or "9:16"
            project["output_dir"] = str(self.store.base_dir / "comic_projects" / f"{project['project_id']}_{safe_filename(name)}")
            project["characters"] = self.state.setdefault("shared_characters", [])
            self.state.setdefault("projects", []).append(project)
            self.state["active_project_id"] = project["project_id"]
            self.state["comic"] = project
            self.store.save(self.state)
            dialog.destroy()
            self.current_page = "comic"
            self.show_comic()

        actions = Frame(dialog, bg=BG)
        actions.pack(fill=X, padx=28)
        self._button(actions, "取消", dialog.destroy, kind="ghost").pack(side=LEFT)
        self._button(actions, "创建并进入  →", create, kind="accent").pack(side=RIGHT)
        name_entry.focus_set()
        name_entry.selection_range(0, END)

    def _activate_comic_project(self, project_id: str) -> bool:
        projects = self.state.get("projects", [])
        project = next((item for item in projects if str(item.get("project_id", "")) == project_id), None)
        if not isinstance(project, dict):
            return False
        shared = self.state.setdefault("shared_characters", [])
        known_names = {str(item.get("name", "")).strip() for item in shared if isinstance(item, dict)}
        for character in list(project.get("characters", [])):
            name = str(character.get("name", "")).strip() if isinstance(character, dict) else ""
            if name and name not in known_names:
                shared.append(dict(character))
                known_names.add(name)
        for item in projects:
            if isinstance(item, dict):
                item["characters"] = shared
        project["characters"] = shared
        self.state["active_project_id"] = project_id
        self.state["comic"] = project
        self.current_comic_character_index = None
        self.current_comic_scene_index = None
        self.current_comic_shot_index = None
        self.store.save(self.state)
        return True

    def open_selected_comic_project(self) -> None:
        if not self.project_tree or not self.project_tree.selection():
            messagebox.showinfo("请选择项目", "请先在列表中选择一个漫画推文项目。")
            return
        project_id = str(self.project_tree.selection()[0])
        if self._activate_comic_project(project_id):
            self.current_page = "comic"
            self.show_comic()

    def delete_selected_comic_project(self) -> None:
        if not self.project_tree or not self.project_tree.selection():
            messagebox.showinfo("请选择项目", "请先选择要删除的项目。")
            return
        project_id = str(self.project_tree.selection()[0])
        projects = self.state.get("projects", [])
        project = next((item for item in projects if str(item.get("project_id", "")) == project_id), None)
        if not isinstance(project, dict):
            return
        name = str(project.get("project_name", "未命名项目"))
        if not messagebox.askyesno(
            "删除推文项目",
            f"确定从主页删除“{name}”吗？\n\n项目记录会删除，但共享角色和已经保存到本地的素材文件不会自动删除。",
        ):
            return
        self.state["projects"] = [item for item in projects if str(item.get("project_id", "")) != project_id]
        remaining = self.state["projects"]
        if str(self.state.get("active_project_id", "")) == project_id:
            if remaining:
                next_project = remaining[0]
                self.state["active_project_id"] = str(next_project.get("project_id", ""))
                next_project["characters"] = self.state.setdefault("shared_characters", [])
                self.state["comic"] = next_project
            else:
                self.state["active_project_id"] = ""
                empty = new_comic_project("未命名漫画推文")
                empty["project_id"] = ""
                empty["characters"] = self.state.setdefault("shared_characters", [])
                self.state["comic"] = empty
        self.store.save(self.state)
        self.show_dashboard()

    def navigate_highlight(self, page: str) -> None:
        for key, button in self.nav_buttons.items():
            button.configure(bg=COMIC_DARK_ALT if key == page else SIDEBAR, fg="white" if key == page else SIDEBAR_MUTED)

    # ---------------------------- Video workbench ----------------------------
    def show_video(self) -> None:
        self._clear_main()
        self.navigate_highlight("video")
        self._page_header(
            "视频混剪",
            "调整素材顺序和截取区间，生成剪映可继续编辑的时间线草稿。",
            [
                ("＋ 添加素材", self.add_video_clips, "ghost"),
                ("导出 MP4", self.export_video, "ghost"),
                ("生成并打开剪映", self.export_jianying_draft, "primary"),
            ],
        )
        self._upgrade_video_clip_metadata()
        body = Frame(self.main, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=34, pady=(0, 28))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left_outer = self._card(body, padx=0, pady=0)
        left_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        left = left_outer.winfo_children()[0]
        title_row = Frame(left, bg=SURFACE, padx=20, pady=16)
        title_row.pack(fill=X)
        self.video_project_var = StringVar(value=self.state["video"]["project_name"])
        Label(title_row, text="项目", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side=LEFT)
        project_entry = self._entry(title_row, self.video_project_var, 32)
        project_entry.pack(side=LEFT, padx=(10, 0), ipady=6)
        project_entry.bind("<FocusOut>", lambda _e: self._sync_video_state())
        self.video_summary = Label(title_row, text="", bg=SURFACE, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 9, "bold"))
        self.video_summary.pack(side=RIGHT)

        columns = ("order", "name", "start", "duration")
        video_tree_outer, video_tree_shell = self._rounded_widget_shell(left, bg=SURFACE)
        video_tree_outer.pack(fill=BOTH, expand=True, padx=1)
        self.video_tree = ttk.Treeview(video_tree_shell, columns=columns, show="headings", style="Studio.Treeview", selectmode="browse")
        self.video_tree.heading("order", text="#")
        self.video_tree.heading("name", text="素材文件")
        self.video_tree.heading("start", text="起点")
        self.video_tree.heading("duration", text="取用 / 原片")
        self.video_tree.column("order", width=50, anchor="center", stretch=False)
        self.video_tree.column("name", width=430, anchor="w")
        self.video_tree.column("start", width=90, anchor="center", stretch=False)
        self.video_tree.column("duration", width=145, anchor="center", stretch=False)
        self._pack_vertical_scroller(video_tree_shell, self.video_tree)
        self.video_tree.bind("<<TreeviewSelect>>", self.on_video_select)

        edit_bar = Frame(left, bg=SURFACE_ALT, padx=16, pady=12)
        edit_bar.pack(fill=X)
        self.clip_start_var = DoubleVar(value=0.0)
        self.clip_duration_var = DoubleVar(value=0.0)
        self._field_label(edit_bar, "起点(秒)").pack(side=LEFT)
        self._entry(edit_bar, self.clip_start_var, 7).pack(side=LEFT, padx=(7, 16), ipady=5)
        self._field_label(edit_bar, "时长(秒)").pack(side=LEFT)
        self._entry(edit_bar, self.clip_duration_var, 7).pack(side=LEFT, padx=(7, 16), ipady=5)
        self._button(edit_bar, "应用", self.update_selected_clip, kind="dark").pack(side=LEFT)
        self._button(edit_bar, "恢复完整时长", self.restore_selected_clip_duration, kind="ghost").pack(side=LEFT, padx=(7, 0))
        self._button(edit_bar, "删除", self.remove_selected_clip, kind="danger").pack(side=RIGHT)
        self._button(edit_bar, "下移", lambda: self.move_clip(1), kind="ghost").pack(side=RIGHT, padx=(0, 6))
        self._button(edit_bar, "上移", lambda: self.move_clip(-1), kind="ghost").pack(side=RIGHT, padx=(0, 6))

        post = Frame(left, bg=SURFACE, padx=20, pady=16)
        post.pack(fill=X)
        post_header = Frame(post, bg=SURFACE)
        post_header.pack(fill=X)
        Label(post_header, text="发布文案", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 11, "bold")).pack(side=LEFT)
        self._button(post_header, "复制", self.copy_post, kind="ghost").pack(side=RIGHT)
        self._button(post_header, "AI 生成", self.generate_post_copy, kind="accent").pack(side=RIGHT, padx=(0, 7))
        post_outer, post_shell = self._rounded_widget_shell(post)
        post_outer.pack(fill=X, pady=(10, 0))
        self.post_editor = Text(post_shell, height=5, wrap="word", bg=COMIC_INSET, fg=INK, insertbackground=INK, relief="flat", padx=12, pady=10, font=("Microsoft YaHei UI", 10))
        self._pack_vertical_scroller(post_shell, self.post_editor, fill=X, expand=True)
        self.post_editor.insert("1.0", self.state["video"].get("post_copy", ""))

        settings_outer = self._card(body, padx=20, pady=18)
        settings_outer.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
        settings = settings_outer.winfo_children()[0]
        Label(settings, text="成片参数", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        Label(settings, text="素材画面固定 1.75 倍速，视频原声始终静音", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 18))

        video = self.state["video"]
        self.aspect_var = StringVar(value=video["aspect"])
        self.fps_var = StringVar(value=str(video["fps"]))
        self.transition_var = StringVar(value=video["transition"])
        self.transition_duration_var = DoubleVar(value=video["transition_duration"])
        strategy_id = video.get("mix_strategy", "balanced")
        strategy_label = next((label for label, value in MIX_STRATEGIES.items() if value == strategy_id), "均衡混剪（推荐）")
        self.mix_strategy_var = StringVar(value=strategy_label)
        self.voice_var = StringVar(value=video.get("voice_path", ""))
        self.subtitles_var = StringVar(value=video.get("subtitles_path", ""))
        self.music_var = StringVar(value=video["music_path"])
        self.music_volume_var = DoubleVar(value=video["music_volume"])
        self.mood_var = StringVar(value=video["mood"])
        self.platform_var = StringVar(value=video["platform"])

        self._combo_field(settings, "画幅比例", self.aspect_var, ["9:16", "16:9", "1:1", "4:5"])
        self._combo_field(settings, "帧率", self.fps_var, ["24", "25", "30", "60"])
        self._combo_field(settings, "转场", self.transition_var, ["fade", "wipeleft", "slideright", "circleopen", "smoothleft", "none"])
        self._number_field(settings, "转场时长（秒）", self.transition_duration_var)
        self._combo_field(settings, "素材使用方式", self.mix_strategy_var, list(MIX_STRATEGIES))
        Label(
            settings,
            text="均衡混剪会让所有素材尽量平均出现在主音频时间线中；顺序完整播放则优先播完前一段。",
            bg=SURFACE,
            fg=MUTED,
            wraplength=270,
            justify=LEFT,
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(4, 0))
        self._combo_field(settings, "文案氛围", self.mood_var, ["治愈", "沉浸", "爽感", "轻松", "高级感"])
        self._combo_field(settings, "发布平台", self.platform_var, ["小红书", "抖音", "视频号", "B站", "微博"])

        self._field_label(settings, "主音频（决定最终时长）").pack(anchor="w", pady=(13, 5))
        voice_row = Frame(settings, bg=SURFACE)
        voice_row.pack(fill=X)
        voice_entry = self._entry(voice_row, self.voice_var)
        voice_entry.pack(side=LEFT, fill=X, expand=True, ipady=6)
        self._button(voice_row, "导入", self.choose_voice_audio, kind="accent").pack(side=RIGHT, padx=(7, 0))
        voice_duration = float(video.get("voice_duration", 0.0))
        self.voice_duration_label = Label(
            settings,
            text=(f"音频时长 {voice_duration:.2f} 秒；视频将自动循环/截断到相同时长" if voice_duration > 0 else "未导入时，成片时长按视频片段总长计算"),
            bg=SURFACE,
            fg=ACCENT_DARK if voice_duration > 0 else MUTED,
            wraplength=270,
            justify=LEFT,
            font=("Microsoft YaHei UI", 8),
        )
        self.voice_duration_label.pack(anchor="w", pady=(4, 0))

        self._field_label(settings, "字幕文件（SRT）").pack(anchor="w", pady=(13, 5))
        subtitle_row = Frame(settings, bg=SURFACE)
        subtitle_row.pack(fill=X)
        subtitle_entry = self._entry(subtitle_row, self.subtitles_var)
        subtitle_entry.pack(side=LEFT, fill=X, expand=True, ipady=6)
        self._button(subtitle_row, "导入", self.choose_subtitles, kind="ghost").pack(side=RIGHT, padx=(7, 0))

        self._field_label(settings, "背景音乐").pack(anchor="w", pady=(13, 5))
        music_row = Frame(settings, bg=SURFACE)
        music_row.pack(fill=X)
        music_entry = self._entry(music_row, self.music_var)
        music_entry.pack(side=LEFT, fill=X, expand=True, ipady=6)
        self._button(music_row, "选择", self.choose_music, kind="ghost").pack(side=RIGHT, padx=(7, 0))
        self._number_field(settings, "音乐音量（0—1）", self.music_volume_var)

        self.export_progress = ttk.Progressbar(settings, style="Studio.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.export_progress.pack(fill=X, pady=(24, 8))
        self.export_status = Label(settings, text="等待导出", bg=SURFACE, fg=MUTED, wraplength=260, justify=LEFT, font=("Microsoft YaHei UI", 9))
        self.export_status.pack(anchor="w")
        self._button(settings, "导出 MP4", self.export_video, kind="primary").pack(fill=X, pady=(14, 0))
        self._button(settings, "生成并打开剪映", self.export_jianying_draft, kind="accent").pack(fill=X, pady=(8, 0))
        self._refresh_video_tree()
        self.bus_handler = self._handle_video_bus

    def _combo_field(self, parent, label: str, variable: StringVar, values: list[str]) -> None:
        self._field_label(parent, label).pack(anchor="w", pady=(10, 5))
        combo = RoundedCombobox(parent, textvariable=variable, values=values, state="readonly")
        combo.pack(fill=X)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_video_state())

    def _number_field(self, parent, label: str, variable: DoubleVar) -> None:
        self._field_label(parent, label).pack(anchor="w", pady=(10, 5))
        entry = self._entry(parent, variable)
        entry.pack(fill=X, ipady=6)
        entry.bind("<FocusOut>", lambda _e: self._sync_video_state())

    def _video_duration(self) -> float:
        video = self.state["video"]
        if video.get("voice_path") and float(video.get("voice_duration", 0.0)) > 0:
            return float(video["voice_duration"])
        clips = video.get("clips", [])
        speed = float(video.get("playback_speed", DEFAULT_PLAYBACK_SPEED))
        total = sum(float(clip.get("duration", 0)) / speed for clip in clips)
        if len(clips) > 1 and video.get("transition") != "none":
            total -= float(video.get("transition_duration", 0.35)) * (len(clips) - 1)
        return max(0.0, total)

    def _sync_video_state(self) -> None:
        if not hasattr(self, "video_project_var"):
            return
        video = self.state["video"]
        video["project_name"] = self.video_project_var.get().strip() or "未命名视频"
        video["aspect"] = self.aspect_var.get()
        video["fps"] = int(self.fps_var.get() or 30)
        video["transition"] = self.transition_var.get()
        video["transition_duration"] = max(0.1, min(float(self.transition_duration_var.get()), 2.0))
        video["mix_strategy"] = MIX_STRATEGIES.get(self.mix_strategy_var.get(), "balanced")
        video["playback_speed"] = DEFAULT_PLAYBACK_SPEED
        video["voice_path"] = self.voice_var.get().strip()
        video["subtitles_path"] = self.subtitles_var.get().strip()
        video["music_path"] = self.music_var.get().strip()
        video["music_volume"] = max(0.0, min(float(self.music_volume_var.get()), 1.0))
        video["mood"] = self.mood_var.get()
        video["platform"] = self.platform_var.get()
        if self.post_editor:
            video["post_copy"] = self.post_editor.get("1.0", "end-1c")
        self.store.save(self.state)
        if self.video_summary:
            self.video_summary.configure(text=f"{len(video['clips'])} 个素材  ·  约 {self._video_duration():.1f} 秒")

    def _refresh_video_tree(self, selected: int | None = None) -> None:
        if not self.video_tree:
            return
        self.video_tree.delete(*self.video_tree.get_children())
        for index, clip in enumerate(self.state["video"]["clips"]):
            source_duration = float(clip.get("source_duration", 0.0))
            duration_text = f"{float(clip['duration']):.1f}s / {source_duration:.1f}s" if source_duration > 0 else f"{float(clip['duration']):.1f}s / 未知"
            self.video_tree.insert("", END, iid=str(index), values=(index + 1, Path(clip["path"]).name, f"{clip['start']:.1f}s", duration_text))
        if selected is not None and str(selected) in self.video_tree.get_children():
            self.video_tree.selection_set(str(selected))
            self.video_tree.focus(str(selected))
        self._sync_video_state()

    def add_video_clips(self) -> None:
        paths = filedialog.askopenfilenames(title="选择视频素材", filetypes=[("视频文件", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"), ("所有文件", "*.*")])
        if not paths:
            return
        settings = self.state["settings"]
        ffprobe = find_executable(settings.get("ffprobe_path", ""), "ffprobe")
        failed: list[str] = []
        for path in paths:
            duration = probe_duration(path, ffprobe) or probe_video_duration(path)
            if not duration or duration < 0.2:
                failed.append(Path(path).name)
                continue
            rounded = round(float(duration), 3)
            self.state["video"]["clips"].append(
                {"path": path, "start": 0.0, "duration": rounded, "source_duration": rounded}
            )
        if self.state["video"]["clips"]:
            self._refresh_video_tree(len(self.state["video"]["clips"]) - 1)
        if failed:
            messagebox.showwarning(
                "部分素材未添加",
                "无法读取以下素材的真实时长，因此没有用错误的 5 秒默认值代替：\n"
                + "\n".join(failed[:8])
                + ("\n……" if len(failed) > 8 else "")
                + "\n\n请配置 FFprobe，或将素材转换为常见的 MP4/MOV 格式后重试。",
            )

    def _upgrade_video_clip_metadata(self) -> None:
        """Migrate old 5/8-second imports to their real full source duration."""
        clips = self.state["video"].get("clips", [])
        if not clips:
            return
        settings = self.state["settings"]
        ffprobe = find_executable(settings.get("ffprobe_path", ""), "ffprobe")
        changed = False
        for clip in clips:
            if float(clip.get("source_duration", 0.0)) > 0 or not Path(clip.get("path", "")).is_file():
                continue
            duration = probe_duration(clip["path"], ffprobe) or probe_video_duration(clip["path"])
            if not duration:
                continue
            source_duration = round(float(duration), 3)
            start = min(max(0.0, float(clip.get("start", 0.0))), max(0.0, source_duration - 0.2))
            clip.update(
                start=start,
                duration=round(max(0.2, source_duration - start), 3),
                source_duration=source_duration,
            )
            changed = True
        if changed:
            self.store.save(self.state)

    def on_video_select(self, _event=None) -> None:
        if not self.video_tree or not self.video_tree.selection():
            return
        clip = self.state["video"]["clips"][int(self.video_tree.selection()[0])]
        self.clip_start_var.set(float(clip["start"]))
        self.clip_duration_var.set(float(clip["duration"]))

    def update_selected_clip(self) -> None:
        if not self.video_tree or not self.video_tree.selection():
            messagebox.showinfo("选择素材", "请先选择一个视频素材。")
            return
        index = int(self.video_tree.selection()[0])
        try:
            start = max(0.0, float(self.clip_start_var.get()))
            duration = max(0.2, float(self.clip_duration_var.get()))
        except (ValueError, TypeError):
            messagebox.showerror("参数错误", "起点和时长必须是数字。")
            return
        clip = self.state["video"]["clips"][index]
        source_duration = float(clip.get("source_duration", 0.0))
        was_clamped = False
        if source_duration > 0:
            if start >= source_duration:
                messagebox.showerror("参数错误", f"截取起点必须小于原片时长 {source_duration:.2f} 秒。")
                return
            available = source_duration - start
            if duration > available:
                duration = available
                was_clamped = True
        clip.update(start=start, duration=round(duration, 3))
        self._refresh_video_tree(index)
        if was_clamped:
            messagebox.showinfo("已自动修正", "取用时长超过素材结尾，已自动调整为剩余的完整时长。")

    def restore_selected_clip_duration(self) -> None:
        if not self.video_tree or not self.video_tree.selection():
            messagebox.showinfo("选择素材", "请先选择一个视频素材。")
            return
        index = int(self.video_tree.selection()[0])
        clip = self.state["video"]["clips"][index]
        source_duration = float(clip.get("source_duration", 0.0))
        if source_duration <= 0:
            messagebox.showwarning("无法恢复", "尚未读取到这个素材的原始时长，请先配置 FFprobe 后重新添加。")
            return
        clip["duration"] = round(max(0.2, source_duration - float(clip.get("start", 0.0))), 3)
        self._refresh_video_tree(index)

    def remove_selected_clip(self) -> None:
        if not self.video_tree or not self.video_tree.selection():
            return
        index = int(self.video_tree.selection()[0])
        del self.state["video"]["clips"][index]
        self._refresh_video_tree(min(index, len(self.state["video"]["clips"]) - 1) if self.state["video"]["clips"] else None)

    def move_clip(self, direction: int) -> None:
        if not self.video_tree or not self.video_tree.selection():
            return
        index = int(self.video_tree.selection()[0])
        target = index + direction
        clips = self.state["video"]["clips"]
        if target < 0 or target >= len(clips):
            return
        clips[index], clips[target] = clips[target], clips[index]
        self._refresh_video_tree(target)

    def choose_music(self) -> None:
        path = filedialog.askopenfilename(title="选择背景音乐", filetypes=[("音频文件", "*.mp3 *.wav *.m4a *.aac *.flac"), ("所有文件", "*.*")])
        if path:
            self.music_var.set(path)
            self._sync_video_state()

    def choose_voice_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="选择主音频",
            filetypes=[("音频文件", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"), ("所有文件", "*.*")],
        )
        if not path:
            return
        duration = probe_audio_duration(path)
        if not duration:
            messagebox.showerror("无法读取音频", "无法识别这个音频文件的时长，请换一个常见格式的音频。")
            return
        self.voice_var.set(path)
        self.state["video"]["voice_duration"] = float(duration)
        self.voice_duration_label.configure(
            text=f"音频时长 {duration:.2f} 秒；视频将自动循环/截断到相同时长",
            fg=ACCENT_DARK,
        )
        self._sync_video_state()
        self._refresh_video_tree()

    def choose_subtitles(self) -> None:
        path = filedialog.askopenfilename(title="选择 SRT 字幕", filetypes=[("SRT 字幕", "*.srt"), ("所有文件", "*.*")])
        if path:
            self.subtitles_var.set(path)
            self._sync_video_state()

    def _video_project(self) -> VideoProject:
        self._sync_video_state()
        video = self.state["video"]
        return VideoProject(
            clips=[
                VideoClip(
                    item["path"],
                    float(item["start"]),
                    float(item["duration"]),
                    float(item.get("source_duration", 0.0)),
                )
                for item in video["clips"]
            ],
            aspect=video["aspect"],
            fps=int(video["fps"]),
            transition=video["transition"],
            transition_duration=float(video["transition_duration"]),
            voice_path=video.get("voice_path", ""),
            subtitles_path=video.get("subtitles_path", ""),
            target_duration=float(video.get("voice_duration", 0.0)) if video.get("voice_path") else 0.0,
            mix_strategy=video.get("mix_strategy", "balanced"),
            playback_speed=float(video.get("playback_speed", DEFAULT_PLAYBACK_SPEED)),
            music_path=video["music_path"],
            music_volume=float(video["music_volume"]),
        )

    def export_video(self) -> None:
        if self.is_busy:
            return
        project = self._video_project()
        if not project.clips:
            messagebox.showinfo("还没有素材", "请先添加至少一个视频素材。")
            return
        configured = self.state["settings"].get("ffmpeg_path", "")
        ffmpeg = find_executable(configured, "ffmpeg")
        if not ffmpeg:
            tool_name = "ffmpeg" if sys.platform == "darwin" else "ffmpeg.exe"
            messagebox.showwarning("需要 FFmpeg", f"尚未找到 FFmpeg。请在“模型与工具”中指定 {tool_name}，之后即可导出视频。")
            self.navigate("settings")
            return
        default_name = (self.state["video"]["project_name"].strip() or "解压混剪") + ".mp4"
        output = filedialog.asksaveasfilename(title="导出成片", defaultextension=".mp4", initialfile=default_name, filetypes=[("MP4 视频", "*.mp4")])
        if not output:
            return
        try:
            command = build_export_command(project, ffmpeg, output)
        except ValueError as exc:
            messagebox.showerror("无法导出", str(exc))
            return
        self.is_busy = True
        self.export_progress["value"] = 0
        self.export_status.configure(text="正在启动 FFmpeg…", fg=ACCENT_DARK)

        def worker() -> None:
            try:
                run_export(command, project.output_duration, lambda value, detail: self.bus.put(("video_progress", (value, detail))))
                self.bus.put(("video_done", output))
            except Exception as exc:  # worker boundary
                self.bus.put(("video_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def export_jianying_draft(self) -> None:
        if self.is_busy:
            return
        project = self._video_project()
        if not project.clips:
            messagebox.showinfo("还没有素材", "请先添加至少一个视频素材。")
            return
        settings = self.state["settings"]
        drafts_path = detect_jianying_drafts_path(settings.get("jianying_drafts_path", ""))
        jianying_exe = detect_jianying_executable(settings.get("jianying_exe", ""))
        if not drafts_path or not jianying_exe:
            messagebox.showwarning("需要剪映设置", "没有找到剪映程序或草稿目录，请在“模型与工具”中确认路径。")
            self.navigate("settings")
            return
        settings["jianying_drafts_path"] = drafts_path
        settings["jianying_exe"] = jianying_exe
        self.store.save(self.state)
        self.is_busy = True
        self.export_progress["value"] = 8
        self.export_status.configure(text="正在生成剪映时间线草稿…", fg=ACCENT_DARK)
        project_name = self.state["video"]["project_name"]

        def worker() -> None:
            try:
                result = create_jianying_draft(project, drafts_path, project_name)
                self.bus.put(("jianying_done", (result, jianying_exe)))
            except Exception as exc:
                self.bus.put(("jianying_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_video_bus(self, event: str, payload: object) -> None:
        if event == "video_progress" and self.export_progress:
            value, _detail = payload
            self.export_progress["value"] = float(value) * 100
            self.export_status.configure(text=f"正在导出… {float(value) * 100:.0f}%")
        elif event == "video_done":
            self.is_busy = False
            self.export_status.configure(text="导出完成", fg=ACCENT_DARK)
            messagebox.showinfo("导出完成", f"视频已保存到：\n{payload}")
        elif event == "video_error":
            self.is_busy = False
            self.export_status.configure(text="导出失败", fg=ERROR)
            messagebox.showerror("导出失败", str(payload))
        elif event == "post_done":
            self.is_busy = False
            if self.post_editor:
                self.post_editor.delete("1.0", END)
                self.post_editor.insert("1.0", str(payload))
                self._sync_video_state()
        elif event == "post_error":
            self.is_busy = False
            messagebox.showerror("文案生成失败", str(payload))
        elif event == "jianying_done":
            self.is_busy = False
            result, executable = payload
            self.export_progress["value"] = 100
            self.export_status.configure(text=f"剪映草稿已生成：{result.name}", fg=ACCENT_DARK)
            try:
                open_jianying(executable)
            except JianyingEngineError as exc:
                messagebox.showwarning("草稿已生成", f"草稿已经生成，但剪映未能自动启动：\n{exc}\n\n草稿位置：\n{result.path}")
                return
            messagebox.showinfo(
                "已打开剪映",
                f"草稿“{result.name}”已经生成。\n\n请在剪映首页的“本地草稿”中打开；若列表未刷新，重新进入剪映首页即可。",
            )
        elif event == "jianying_error":
            self.is_busy = False
            self.export_progress["value"] = 0
            self.export_status.configure(text="剪映草稿生成失败", fg=ERROR)
            messagebox.showerror("剪映草稿生成失败", str(payload))

    def generate_post_copy(self) -> None:
        if self.is_busy:
            return
        try:
            client = self._ai_client()
        except AIClientError as exc:
            messagebox.showwarning("需要模型设置", str(exc))
            self.navigate("settings")
            return
        self._sync_video_state()
        video = self.state["video"]
        system, user = build_post_prompt(video["project_name"], video["mood"], video["platform"], [Path(item["path"]).stem for item in video["clips"]], self._video_duration())
        self.is_busy = True
        if self.post_editor:
            self.post_editor.delete("1.0", END)
            self.post_editor.insert("1.0", "正在生成文案…")

        def worker() -> None:
            try:
                self.bus.put(("post_done", client.complete(system, user, temperature=0.8)))
            except Exception as exc:
                self.bus.put(("post_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def copy_post(self) -> None:
        if not self.post_editor:
            return
        text = self.post_editor.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    # ---------------------------- Novel workbench ----------------------------
    def show_novel(self) -> None:
        self._clear_main()
        self.navigate_highlight("novel")
        self._page_header(
            "小说改文",
            "自动拆章并保留原文对照；建议先完善设定库，再逐章生成。",
            [
                ("导入小说", self.import_novel, "ghost"),
                ("查看提示词", self.preview_prompt, "ghost"),
                ("改写当前章", self.rewrite_current, "primary"),
            ],
        )
        body = Frame(self.main, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=34, pady=(0, 28))
        body.grid_columnconfigure(0, weight=0, minsize=300)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        control_outer = self._card(body, padx=18, pady=17)
        control_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        control = control_outer.winfo_children()[0]
        novel = self.state["novel"]
        self.novel_project_var = StringVar(value=novel["project_name"])
        self.mode_var = StringVar(value=novel["mode"])
        self.style_var = StringVar(value=novel["style"])
        self.perspective_var = StringVar(value=novel["perspective"])
        self.length_var = StringVar(value=novel["target_length"])

        Label(control, text="改写规则", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        self._field_label(control, "项目名").pack(anchor="w", pady=(12, 5))
        self._entry(control, self.novel_project_var).pack(fill=X, ipady=6)
        self._novel_combo(control, "改写模式", self.mode_var, [NOVEL_COMMENTARY_MODE, "轻度润色", "深度改写", "扩写细节", "精简提速", "影视化改写"])
        self._novel_combo(control, "目标风格", self.style_var, [NOVEL_COMMENTARY_STYLE, "节奏紧凑、画面感强", "自然细腻、情绪充足", "简洁爽快、对白突出", "悬念强、章节钩子明显", "轻松幽默"])
        Label(
            control,
            text="推荐模式会生成可直接配音的小说解说稿：开头抛冲突、全程持续递进、结尾保留剧情钩子。",
            bg=SURFACE,
            fg=ACCENT_DARK,
            wraplength=360,
            justify=LEFT,
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(5, 0))
        self._novel_combo(control, "叙事视角", self.perspective_var, ["保持原视角", "第一人称", "第三人称限知", "第三人称全知"])
        self._novel_combo(control, "目标篇幅", self.length_var, ["与原文接近", "缩短约20%", "扩写约30%", "只保留主线"])
        self._field_label(control, "自定义规则").pack(anchor="w", pady=(10, 5))
        rules_outer, rules_shell = self._rounded_widget_shell(control)
        rules_outer.pack(fill=X)
        self.rules_editor = Text(rules_shell, height=4, wrap="word", bg=COMIC_INSET, fg=INK, relief="flat", padx=8, pady=8, font=("Microsoft YaHei UI", 9))
        self._pack_vertical_scroller(rules_shell, self.rules_editor, fill=X, expand=True)
        self.rules_editor.insert("1.0", novel["custom_rules"])
        button_row = Frame(control, bg=SURFACE)
        button_row.pack(fill=X, pady=(10, 13))
        self._button(button_row, "设定库", self.edit_story_bible, kind="ghost").pack(side=LEFT)
        self._button(button_row, "查看提示词", self.preview_prompt, kind="ghost").pack(side=RIGHT)

        chapter_header = Frame(control, bg=SURFACE)
        chapter_header.pack(fill=X)
        Label(chapter_header, text="章节", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 11, "bold")).pack(side=LEFT)
        self.chapter_progress_label = Label(chapter_header, text="", bg=SURFACE, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 9))
        self.chapter_progress_label.pack(side=RIGHT)
        novel_list_outer, novel_list_shell = self._rounded_widget_shell(control)
        novel_list_outer.pack(fill=BOTH, expand=True, pady=(8, 10))
        self.novel_list = Listbox(novel_list_shell, exportselection=False, bg=COMIC_INSET, fg=INK, selectbackground=COMIC_MINT, selectforeground=INK, relief="flat", highlightthickness=0, font=("Microsoft YaHei UI", 9), activestyle="none")
        self._pack_vertical_scroller(novel_list_shell, self.novel_list)
        self.novel_list.bind("<<ListboxSelect>>", self.on_chapter_select)
        actions = Frame(control, bg=SURFACE)
        actions.pack(fill=X)
        self._button(actions, "批量改写", self.rewrite_all, kind="dark").pack(side=LEFT, fill=X, expand=True)
        self._button(actions, "导出结果", self.export_novel, kind="accent").pack(side=RIGHT, fill=X, expand=True, padx=(7, 0))

        editors_outer = self._card(body, padx=0, pady=0)
        editors_outer.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
        editors = editors_outer.winfo_children()[0]
        pane = ttk.Panedwindow(editors, orient="horizontal", style="Studio.TPanedwindow")
        pane.pack(fill=BOTH, expand=True)
        source_panel = self._editor_panel(pane, "原文章节", "可直接粘贴章节正文，也可以先导入小说文件。")
        result_panel = self._editor_panel(pane, "改写结果", "AI 结果会显示在这里，你仍可人工编辑。", result=True)
        pane.add(source_panel, weight=1)
        pane.add(result_panel, weight=1)
        self.novel_status = Label(editors, text="就绪", bg=SURFACE_ALT, fg=MUTED, anchor="w", padx=16, pady=10, font=("Microsoft YaHei UI", 9))
        self.novel_status.pack(fill=X)
        chapter_count = len(novel["chapters"])
        if chapter_count:
            selected = self.current_chapter_index if self.current_chapter_index is not None and self.current_chapter_index < chapter_count else 0
            self._refresh_novel_list(selected)
        else:
            self.current_chapter_index = None
            self._refresh_novel_list()
        self.bus_handler = self._handle_novel_bus

    def _novel_combo(self, parent, label: str, variable: StringVar, values: list[str]) -> None:
        self._field_label(parent, label).pack(anchor="w", pady=(10, 5))
        RoundedCombobox(parent, textvariable=variable, values=values, state="readonly").pack(fill=X)

    def _editor_panel(self, parent, title: str, subtitle: str, result: bool = False) -> Frame:
        panel = Frame(parent, bg=SURFACE)
        header = Frame(panel, bg=SURFACE_ALT, padx=16, pady=12)
        header.pack(fill=X)
        Label(header, text=title, bg=SURFACE_ALT, fg=INK, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        Label(header, text=subtitle, bg=SURFACE_ALT, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(2, 0))
        editor_outer, editor_shell = self._rounded_widget_shell(panel, bg=SURFACE)
        editor_outer.pack(fill=BOTH, expand=True)
        editor = Text(editor_shell, wrap="word", undo=True, bg=SURFACE, fg=INK, insertbackground=INK, relief="flat", padx=18, pady=16, spacing1=2, spacing3=5, font=("Microsoft YaHei UI", 11))
        self._pack_vertical_scroller(editor_shell, editor)
        if result:
            self.result_editor = editor
        else:
            self.source_editor = editor
        return panel

    def import_novel(self) -> None:
        path = filedialog.askopenfilename(title="导入小说", filetypes=[("文本文档", "*.txt *.md *.docx"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            content = read_document(path)
        except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
            messagebox.showerror("导入失败", f"无法读取文件：{exc}")
            return
        chapters = chapter_records(content)
        if not chapters:
            messagebox.showwarning("没有正文", "文档中没有可用文字。")
            return
        novel = self.state["novel"]
        novel["source_path"] = path
        novel["source_text"] = content
        novel["project_name"] = Path(path).stem
        novel["chapters"] = chapters
        novel["results"] = {}
        self.novel_project_var.set(novel["project_name"])
        self.current_chapter_index = None
        self._refresh_novel_list(0)
        self.store.save(self.state)

    def _refresh_novel_list(self, selected: int | None = None) -> None:
        if not self.novel_list:
            return
        self.novel_list.delete(0, END)
        novel = self.state["novel"]
        for index, chapter in enumerate(novel["chapters"]):
            done = "✓" if str(index) in novel["results"] and novel["results"][str(index)].strip() else "·"
            self.novel_list.insert(END, f" {done}  {chapter['title']}")
        self.chapter_progress_label.configure(text=f"{len(novel['results'])}/{len(novel['chapters'])}")
        if selected is not None and novel["chapters"]:
            self.novel_list.selection_set(selected)
            self.novel_list.activate(selected)
            self._load_chapter(selected)

    def on_chapter_select(self, _event=None) -> None:
        if not self.novel_list or not self.novel_list.curselection():
            return
        next_index = int(self.novel_list.curselection()[0])
        if self.current_chapter_index == next_index:
            return
        self._save_chapter_editors()
        self._load_chapter(next_index)

    def _load_chapter(self, index: int) -> None:
        chapters = self.state["novel"]["chapters"]
        if not (0 <= index < len(chapters)) or not self.source_editor or not self.result_editor:
            return
        self.current_chapter_index = index
        self.source_editor.delete("1.0", END)
        self.source_editor.insert("1.0", chapters[index]["content"])
        self.result_editor.delete("1.0", END)
        self.result_editor.insert("1.0", self.state["novel"]["results"].get(str(index), ""))
        self.novel_status.configure(text=f"正在编辑：{chapters[index]['title']}")

    def _sync_novel_rules(self) -> None:
        if not hasattr(self, "novel_project_var"):
            return
        novel = self.state["novel"]
        novel["project_name"] = self.novel_project_var.get().strip() or "未命名小说"
        novel["mode"] = self.mode_var.get()
        novel["style"] = self.style_var.get()
        novel["perspective"] = self.perspective_var.get()
        novel["target_length"] = self.length_var.get()
        novel["custom_rules"] = self.rules_editor.get("1.0", "end-1c").strip()

    def _save_chapter_editors(self) -> None:
        if self.current_chapter_index is None or not self.source_editor or not self.result_editor:
            return
        novel = self.state["novel"]
        if self.current_chapter_index >= len(novel["chapters"]):
            return
        novel["chapters"][self.current_chapter_index]["content"] = self.source_editor.get("1.0", "end-1c")
        result = self.result_editor.get("1.0", "end-1c").strip()
        key = str(self.current_chapter_index)
        if result:
            novel["results"][key] = result
        else:
            novel["results"].pop(key, None)

    def _accept_pasted_source(self) -> bool:
        """Turn text pasted into an otherwise empty source editor into chapters."""
        if not self.source_editor:
            return False
        content = self.source_editor.get("1.0", "end-1c").strip()
        chapters = chapter_records(content)
        if not chapters:
            return False
        self._sync_novel_rules()
        novel = self.state["novel"]
        novel["source_path"] = ""
        novel["source_text"] = content
        novel["chapters"] = chapters
        novel["results"] = {}
        self.current_chapter_index = None
        self._refresh_novel_list(0)
        self.store.save(self.state)
        self.novel_status.configure(text=f"已识别粘贴内容：{len(chapters)} 章", fg=ACCENT_DARK)
        return True

    def _chapter_prompt(self, index: int) -> tuple[str, str]:
        self._sync_novel_rules()
        novel = self.state["novel"]
        chapter = novel["chapters"][index]
        return build_rewrite_prompt(
            chapter["title"],
            chapter["content"],
            mode=novel["mode"],
            style=novel["style"],
            perspective=novel["perspective"],
            target_length=novel["target_length"],
            custom_rules=novel["custom_rules"],
            story_bible=novel["story_bible"],
        )

    def rewrite_current(self) -> None:
        if self.is_busy:
            return
        self._save_chapter_editors()
        if self.current_chapter_index is None and not self._accept_pasted_source():
            messagebox.showinfo("没有章节", "请在“原文章节”中粘贴正文，或导入小说文件。")
            return
        assert self.current_chapter_index is not None
        if not self.state["novel"]["chapters"][self.current_chapter_index]["content"].strip():
            messagebox.showinfo("没有正文", "当前章节没有正文，请先粘贴或输入内容。")
            return
        try:
            client = self._ai_client()
        except AIClientError as exc:
            messagebox.showwarning("需要模型设置", str(exc))
            self.navigate("settings")
            return
        index = self.current_chapter_index
        system, user = self._chapter_prompt(index)
        self.is_busy = True
        self.novel_status.configure(text="AI 正在改写当前章节…", fg=ACCENT_DARK)

        def worker() -> None:
            try:
                result = client.complete(system, user, temperature=0.72)
                self.bus.put(("novel_chapter_done", (index, result)))
            except Exception as exc:
                self.bus.put(("novel_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def rewrite_all(self) -> None:
        if self.is_busy:
            return
        self._save_chapter_editors()
        chapters = self.state["novel"]["chapters"]
        if not chapters and self._accept_pasted_source():
            chapters = self.state["novel"]["chapters"]
        if not chapters:
            messagebox.showinfo("没有章节", "请在“原文章节”中粘贴正文，或导入小说文件。")
            return
        if not messagebox.askyesno("批量改写", f"将依次请求模型改写 {len(chapters)} 个章节。此操作可能产生 API 费用，是否继续？"):
            return
        try:
            client = self._ai_client()
        except AIClientError as exc:
            messagebox.showwarning("需要模型设置", str(exc))
            self.navigate("settings")
            return
        prompts = [self._chapter_prompt(i) for i in range(len(chapters))]
        self.is_busy = True

        def worker() -> None:
            try:
                for index, (system, user) in enumerate(prompts):
                    self.bus.put(("novel_batch_progress", (index, len(prompts))))
                    result = client.complete(system, user, temperature=0.72)
                    self.bus.put(("novel_batch_item", (index, result)))
                self.bus.put(("novel_batch_done", len(prompts)))
            except Exception as exc:
                self.bus.put(("novel_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_novel_bus(self, event: str, payload: object) -> None:
        if event in {"novel_chapter_done", "novel_batch_item"}:
            index, result = payload
            self.state["novel"]["results"][str(index)] = result
            self.store.save(self.state)
            self._refresh_novel_list(index if event == "novel_chapter_done" else self.current_chapter_index)
            if event == "novel_chapter_done":
                self.is_busy = False
                self.novel_status.configure(text="当前章节改写完成，可继续人工编辑。", fg=ACCENT_DARK)
        elif event == "novel_batch_progress":
            index, total = payload
            self.novel_status.configure(text=f"批量改写中：{index + 1}/{total}", fg=ACCENT_DARK)
        elif event == "novel_batch_done":
            self.is_busy = False
            self._refresh_novel_list(self.current_chapter_index)
            self.novel_status.configure(text=f"批量改写完成：共 {payload} 章", fg=ACCENT_DARK)
            messagebox.showinfo("批量改写完成", "所有章节已处理并自动保存。")
        elif event == "novel_error":
            self.is_busy = False
            self.novel_status.configure(text="改写失败，已保留完成部分。", fg=ERROR)
            messagebox.showerror("改写失败", str(payload))

    def _novel_prompt_preview_payload(self) -> tuple[str, str, str]:
        """Return the live chapter prompt or a useful template for an empty project."""
        self._save_chapter_editors()
        if self.current_chapter_index is None:
            self._accept_pasted_source()
        if self.current_chapter_index is not None:
            system, user = self._chapter_prompt(self.current_chapter_index)
            chapter = self.state["novel"]["chapters"][self.current_chapter_index]
            return system, user, f"当前章节：{chapter.get('title', f'章节 {self.current_chapter_index + 1}')}"

        self._sync_novel_rules()
        novel = self.state["novel"]
        system, user = build_rewrite_prompt(
            "提示词模板",
            "（这里会自动替换为当前章节的小说正文。请先导入小说，或在“原文章节”中粘贴正文。）",
            mode=novel["mode"],
            style=novel["style"],
            perspective=novel["perspective"],
            target_length=novel["target_length"],
            custom_rules=novel["custom_rules"],
            story_bible=novel["story_bible"],
        )
        return system, user, "当前尚无章节，以下显示完整提示词模板"

    def preview_prompt(self) -> Toplevel:
        system, user, description = self._novel_prompt_preview_payload()
        dialog = Toplevel(self.root)
        dialog.title("小说解说提示词预览")
        dialog.geometry("820x660")
        dialog.minsize(640, 500)
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        header = Frame(dialog, bg=SIDEBAR, padx=20, pady=14)
        header.pack(fill=X)
        Label(header, text="AI 小说解说 · 实际发送提示词", bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        Label(header, text=description, bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 0))
        editor_outer, editor_shell = self._rounded_widget_shell(dialog, bg=SURFACE)
        editor_outer.pack(fill=BOTH, expand=True, padx=18, pady=(18, 8))
        editor = Text(editor_shell, wrap="word", bg=SURFACE, fg=INK, padx=18, pady=18, font=("Microsoft YaHei UI", 10), undo=False)
        self._pack_vertical_scroller(editor_shell, editor)
        editor.insert("1.0", f"【系统提示】\n{system}\n\n【用户提示】\n{user}")
        editor.configure(state="disabled")
        row = Frame(dialog, bg=BG)
        row.pack(fill=X, padx=18, pady=(0, 18))
        self._button(row, "关闭", dialog.destroy, kind="ghost").pack(side=LEFT)
        self._button(row, "复制全部提示词", lambda: self._copy_text(editor.get("1.0", "end-1c")), kind="primary").pack(side=RIGHT)
        dialog.after_idle(dialog.lift)
        dialog.after_idle(dialog.focus_force)
        return dialog

    def edit_story_bible(self) -> None:
        dialog = Toplevel(self.root)
        dialog.title("人物与世界观设定库")
        dialog.geometry("760x620")
        dialog.configure(bg=BG)
        Label(dialog, text="人物与世界观设定库", bg=BG, fg=INK, font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w", padx=22, pady=(20, 4))
        Label(dialog, text="记录人物称谓、关系、能力、禁改项和剧情时间线，模型每章都会参考。", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=22)
        editor_outer, editor_shell = self._rounded_widget_shell(dialog, bg=SURFACE)
        editor_outer.pack(fill=BOTH, expand=True, padx=22, pady=16)
        editor = Text(editor_shell, wrap="word", bg=SURFACE, fg=INK, padx=18, pady=18, relief="flat", font=("Microsoft YaHei UI", 10))
        self._pack_vertical_scroller(editor_shell, editor)
        editor.insert("1.0", self.state["novel"].get("story_bible", ""))

        def save() -> None:
            self.state["novel"]["story_bible"] = editor.get("1.0", "end-1c").strip()
            self.store.save(self.state)
            dialog.destroy()

        self._button(dialog, "保存设定", save, kind="primary").pack(anchor="e", padx=22, pady=(0, 20))

    def export_novel(self) -> None:
        self._save_chapter_editors()
        novel = self.state["novel"]
        if not novel["chapters"]:
            messagebox.showinfo("没有内容", "请先导入并改写小说。")
            return
        output = filedialog.asksaveasfilename(title="导出改写结果", defaultextension=".txt", initialfile=(novel["project_name"] or "小说改文") + "_改写稿.txt", filetypes=[("TXT 文本", "*.txt")])
        if not output:
            return
        parts: list[str] = []
        for index, chapter in enumerate(novel["chapters"]):
            content = novel["results"].get(str(index), chapter["content"])
            parts.append(f"{chapter['title']}\n\n{content.strip()}")
        try:
            Path(output).write_text("\n\n\n".join(parts) + "\n", encoding="utf-8-sig")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        messagebox.showinfo("导出完成", f"改写稿已保存到：\n{output}")

    # ----------------------------- Comic workbench -----------------------------
    def show_comic(self) -> None:
        self._clear_main()
        self.navigate_highlight("comic")
        comic = self.state["comic"]
        self.comic_project_var = StringVar(value=comic["project_name"])
        self.comic_style_var = StringVar(value=comic["art_style"])
        self.comic_aspect_var = StringVar(value=comic["aspect"])
        self.comic_output_var = StringVar(value=comic.get("output_dir", ""))
        self.comic_resolution_var = StringVar(value=str(comic.get("resolution", "2K")))
        self.comic_optimize_var = StringVar(value="标准质量" if comic.get("optimize_mode", "standard") == "standard" else "极速模式")
        shot_model_id = str(comic.get("shot_image_model", SEEDREAM_LITE_MODEL)).strip()
        self.comic_shot_model_var = StringVar(value=SHOT_IMAGE_MODEL_LABELS.get(shot_model_id, SHOT_IMAGE_MODEL_OPTIONS[0]))
        self.comic_audio_var = StringVar(value=str(comic.get("audio_path", "")))
        self.comic_subtitles_var = StringVar(value=str(comic.get("subtitles_path", "")))
        self.comic_motion_var = StringVar(value=normalize_motion_mode(comic.get("motion_mode")))
        self.comic_video_output_var = StringVar(value=str(comic.get("video_output_path", "")))
        self.comic_draft_output_var = StringVar(value=str(comic.get("jianying_draft_path", "")))
        cover = comic.get("cover", {}) if isinstance(comic.get("cover"), dict) else {}
        self.comic_cover_title_var = StringVar(value=str(cover.get("title", "")) or str(comic.get("project_name", "漫画推文")))
        self.comic_cover_character_var = StringVar(value=str(cover.get("character", "")) or "（不使用人物参考）")
        self.comic_cover_scene_var = StringVar(value=str(cover.get("scene", "")) or "（不使用场景参考）")
        self.open_jianying_after_video = False
        self.open_jianying_after_draft = False
        self.comic_step = min(max(int(comic.get("workspace_step", 0)), 0), 5)
        self.comic_step_widgets: list[tuple[Frame, Frame, Label, Label, Label]] = []
        self.comic_character_description_editor = None
        self.comic_character_prompt_editor = None
        self.comic_scene_description_editor = None
        self.comic_scene_prompt_editor = None
        self.comic_shot_source_label = None
        self.comic_count_label = None
        self.comic_generation_detail_label = None
        self.comic_generation_count_label = None
        self.comic_shot_character_list = None
        self.comic_shot_scene_var = StringVar()
        self.comic_batch_scope_var = StringVar(value="全部分镜")
        self.comic_batch_character_from_var = StringVar()
        self.comic_batch_character_to_var = StringVar()
        self.comic_batch_scene_from_var = StringVar()
        self.comic_batch_scene_to_var = StringVar()
        self.comic_video_audio_label = None
        self.comic_video_subtitle_label = None
        self.comic_video_result_label = None
        self.comic_draft_progress = None
        self.comic_draft_status_label = None
        self.comic_character_preview_canvas = None
        self.comic_scene_preview_canvas = None
        self.comic_character_preview_title = None
        self.comic_scene_preview_title = None
        self.comic_shot_tree_with_previews = False
        self.comic_shot_preview_images = {}
        self.comic_shot_loaded_preview_signatures = {}
        if self.comic_shot_preview_after_id:
            try:
                self.root.after_cancel(self.comic_shot_preview_after_id)
            except TclError:
                pass
        self.comic_shot_preview_after_id = None
        self.comic_storyboard_canvas = None
        self.comic_storyboard_body = None
        self.comic_storyboard_window = None
        self.comic_storyboard_selected_indices = set()
        self.comic_storyboard_selection_vars = {}
        self.comic_storyboard_prompt_editors = {}
        self.comic_storyboard_row_widgets = {}
        self.comic_storyboard_page = 0
        self.comic_storyboard_page_label = None
        self.comic_cover_dialog = None
        self.comic_cover_preview_canvas = None
        self.comic_cover_preview_canvases = {}
        self.comic_cover_prompt_editor = None
        self.comic_cover_status_label = None
        self.comic_cover_dialog_status_label = None

        hero_outer = Frame(self.main, bg=SIDEBAR)
        hero_outer.pack(fill=X, padx=34, pady=(24, 12))
        hero = Frame(hero_outer, bg=SIDEBAR, padx=24, pady=16)
        hero.pack(fill=X)
        hero_text = Frame(hero, bg=SIDEBAR)
        hero_text.pack(side=LEFT, fill=X, expand=True)
        Label(hero_text, text="AI COMIC STUDIO · STATIC STORYBOARD", bg=SIDEBAR, fg=ACCENT, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        title_row = Frame(hero_text, bg=SIDEBAR)
        title_row.pack(fill=X, pady=(4, 0))
        Label(title_row, text="AI 漫画工作台", bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 22, "bold")).pack(side=LEFT)
        self.comic_hero_status = Label(title_row, text="本地自动保存", bg=COMIC_DARK_ALT, fg="#D4EDEA", padx=10, pady=4, font=("Microsoft YaHei UI", 8, "bold"))
        self.comic_hero_status.pack(side=LEFT, padx=(14, 0))
        Label(hero_text, text="从小说拆解、角色定妆、场景定景到可编辑剪映草稿，让每一步都清楚、可控、可回退。", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(5, 0))
        hero_actions = Frame(hero, bg=SIDEBAR)
        hero_actions.pack(side=RIGHT, padx=(18, 0))
        self._button(hero_actions, "Seedream API", self.edit_seedream_settings, kind="glass").pack(side=LEFT)
        self._button(hero_actions, "素材目录", self.open_comic_output_dir, kind="glass").pack(side=LEFT, padx=(8, 0))
        self._button(hero_actions, "保存项目", self.save_comic_settings, kind="accent").pack(side=LEFT, padx=(8, 0))

        status_strip = Frame(self.main, bg=BG)
        status_strip.pack(fill=X, padx=34, pady=(0, 10))
        self.comic_project_summary = Label(status_strip, text="", bg=BG, fg=INK, anchor="w", font=("Microsoft YaHei UI", 9, "bold"))
        self.comic_project_summary.pack(side=LEFT)
        self.comic_api_status = Label(status_strip, text="", bg=BG, fg=MUTED, anchor="e", font=("Microsoft YaHei UI", 8))
        self.comic_api_status.pack(side=RIGHT, padx=(12, 0))
        self.comic_progress = ttk.Progressbar(status_strip, mode="determinate", length=180, style="Studio.Horizontal.TProgressbar")
        self.comic_progress.pack(side=RIGHT, padx=(12, 0))
        self.comic_status = Label(status_strip, text="就绪", bg=BG, fg=MUTED, anchor="e", font=("Microsoft YaHei UI", 8))
        self.comic_status.pack(side=RIGHT)

        pipeline = Frame(self.main, bg=BG)
        pipeline.pack(fill=X, padx=34, pady=(0, 12))
        step_titles = ["小说与项目", "角色定妆", "场景定景", "静态分镜", "批量出图", "剪映草稿"]
        step_subtitles = ["导入与拆解", "确认人物参考", "确认环境参考", "角色与场景", "生成与复查", "静态图关键帧"]
        for index, title in enumerate(step_titles):
            cell = Frame(pipeline, bg=BORDER, padx=1, pady=1, cursor="hand2")
            cell.pack(side=LEFT, fill=X, expand=True, padx=(0, 7 if index < 5 else 0))
            cell_body = Frame(cell, bg=SURFACE, padx=13, pady=10, cursor="hand2")
            cell_body.pack(fill=BOTH, expand=True)
            badge = Label(cell_body, text=str(index + 1), bg=SURFACE_ALT, fg=MUTED, width=3, pady=5, font=("Segoe UI", 9, "bold"), cursor="hand2")
            badge.pack(side=LEFT)
            step_text = Frame(cell_body, bg=SURFACE, cursor="hand2")
            step_text.pack(side=LEFT, padx=(10, 0))
            title_label = Label(step_text, text=title, bg=SURFACE, fg=INK, anchor="w", font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2")
            title_label.pack(anchor="w")
            subtitle_label = Label(step_text, text=step_subtitles[index], bg=SURFACE, fg=MUTED, anchor="w", font=("Microsoft YaHei UI", 7), cursor="hand2")
            subtitle_label.pack(anchor="w", pady=(1, 0))
            for widget in (cell, cell_body, badge, step_text, title_label, subtitle_label):
                widget.bind("<Button-1>", lambda _event, step=index: self._switch_comic_step(step))
            self.comic_step_widgets.append((cell, cell_body, badge, title_label, subtitle_label))

        self.comic_readiness_labels: list[Label] = []
        self.comic_next_hint = None
        self.comic_next_button = None

        body = Frame(self.main, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=34, pady=(0, 24))
        self.comic_workspace = Frame(body, bg=BG)
        self.comic_workspace.pack(fill=BOTH, expand=True)

        self.bus_handler = self._handle_comic_bus
        self._switch_comic_step(self.comic_step, save=False)

    @staticmethod
    def _comic_progress_snapshot(comic: dict[str, object]) -> dict[str, int | bool]:
        source_ready = bool(str(comic.get("source_text", "")).strip())
        characters = list(comic.get("characters", []))
        scenes = list(comic.get("scenes", []))
        shots = list(comic.get("shots", []))
        character_ready = sum(1 for item in characters if has_local_reference(item))
        scene_ready = sum(1 for item in scenes if has_local_reference(item))
        images_ready = sum(1 for item in shots if Path(str(item.get("local_path", ""))).is_file())
        video_ready = bool(str(comic.get("jianying_draft_path", "")).strip() and Path(str(comic.get("jianying_draft_path", ""))).is_dir())
        return {
            "source_ready": source_ready,
            "character_count": len(characters),
            "character_ready": character_ready,
            "scene_count": len(scenes),
            "scene_ready": scene_ready,
            "shot_count": len(shots),
            "images_ready": images_ready,
            "video_ready": video_ready,
        }

    def _recommended_comic_step(self) -> int:
        progress = self._comic_progress_snapshot(self.state["comic"])
        if not progress["source_ready"]:
            return 0
        if progress["character_count"] and progress["character_ready"] < progress["character_count"]:
            return 1
        if not progress["character_count"] and not progress["scene_count"] and not progress["shot_count"]:
            return 1
        if not progress["scene_count"] or progress["scene_ready"] < progress["scene_count"]:
            return 2
        if not progress["shot_count"]:
            return 3
        if progress["images_ready"] < progress["shot_count"]:
            return 4
        return 5

    def _reset_comic_step_widgets(self) -> None:
        cover_dialog = getattr(self, "comic_cover_dialog", None)
        if cover_dialog is not None:
            try:
                if cover_dialog.winfo_exists():
                    cover_dialog.destroy()
            except TclError:
                pass
        self.comic_cover_dialog = None
        if self.comic_asset_autosave_after_id:
            try:
                self.root.after_cancel(self.comic_asset_autosave_after_id)
            except TclError:
                pass
            self.comic_asset_autosave_after_id = None
        self.comic_source_editor = None
        self.comic_character_list = None
        self.comic_character_description_editor = None
        self.comic_character_prompt_editor = None
        self.comic_scene_list = None
        self.comic_scene_description_editor = None
        self.comic_scene_prompt_editor = None
        self.comic_shot_tree = None
        self.comic_shot_prompt_editor = None
        self.comic_shot_source_label = None
        self.comic_count_label = None
        self.comic_generation_detail_label = None
        self.comic_generation_count_label = None
        self.comic_shot_character_list = None
        self.comic_shot_scene_combo = None
        self.comic_video_audio_label = None
        self.comic_video_subtitle_label = None
        self.comic_video_result_label = None
        self.comic_draft_progress = None
        self.comic_draft_status_label = None
        self.comic_character_preview_canvas = None
        self.comic_scene_preview_canvas = None
        self.comic_character_preview_title = None
        self.comic_scene_preview_title = None
        self.comic_shot_tree_with_previews = False
        self.comic_shot_preview_images = {}
        self.comic_storyboard_canvas = None
        self.comic_storyboard_body = None
        self.comic_storyboard_window = None
        self.comic_storyboard_selected_indices = set()
        self.comic_storyboard_selection_vars = {}
        self.comic_storyboard_prompt_editors = {}
        self.comic_storyboard_row_widgets = {}
        self.comic_storyboard_page = 0
        self.comic_storyboard_page_label = None
        self.comic_cover_preview_canvas = None
        self.comic_cover_preview_canvases = {}
        self.comic_cover_prompt_editor = None
        self.comic_cover_status_label = None
        self.comic_cover_dialog_status_label = None

    def _switch_comic_step(self, step: int, *, save: bool = True) -> None:
        step = min(max(int(step), 0), 5)
        if self.is_busy and step != getattr(self, "comic_step", step):
            messagebox.showinfo("任务进行中", "当前任务完成后再切换制作步骤。")
            return
        if save:
            self._sync_comic_state()
            self._cancel_scheduled_state_save()
            self.store.save(self.state)
        for child in self.comic_workspace.winfo_children():
            child.destroy()
        self._reset_comic_step_widgets()
        self.comic_step = step
        self.state["comic"]["workspace_step"] = step
        builders = [self._build_comic_source_step, self._build_comic_character_step, self._build_comic_scene_step, self._build_comic_storyboard_step, self._build_comic_generation_step, self._build_comic_video_step]
        builders[step](self.comic_workspace)
        self._refresh_comic_overview()

    def _refresh_comic_overview(self) -> None:
        if not hasattr(self, "comic_step_widgets"):
            return
        comic = self.state["comic"]
        progress = self._comic_progress_snapshot(comic)
        character_count = int(progress["character_count"])
        character_ready = int(progress["character_ready"])
        scene_count = int(progress["scene_count"])
        scene_ready = int(progress["scene_ready"])
        shot_count = int(progress["shot_count"])
        images_ready = int(progress["images_ready"])
        video_ready = bool(progress["video_ready"])
        complete = [
            bool(progress["source_ready"]),
            (character_count > 0 and character_ready == character_count) or (character_count == 0 and shot_count > 0),
            scene_count > 0 and scene_ready == scene_count,
            shot_count > 0,
            shot_count > 0 and images_ready == shot_count,
            video_ready,
        ]
        values = [
            ("小说正文", "已导入" if complete[0] else "待导入"),
            ("角色定妆", f"{character_ready}/{character_count}" if character_count else ("无需角色" if shot_count else "待识别")),
            ("场景定景", f"{scene_ready}/{scene_count}" if scene_count else "待识别"),
            ("分镜脚本", f"{shot_count} 个"),
            ("成品图片", f"{images_ready}/{shot_count}"),
            ("剪映草稿", "已生成" if video_ready else "可选"),
        ]
        for index, ((title, value), label) in enumerate(zip(values, self.comic_readiness_labels)):
            row_bg = COMIC_MINT if complete[index] else COMIC_INSET
            label.master.configure(bg=row_bg)
            label.configure(text=f"{'✓' if complete[index] else '○'}  {title}    {value}", bg=row_bg, fg=ACCENT_DARK if complete[index] else MUTED)
        self.comic_project_summary.configure(text=f"{comic.get('project_name', '未命名项目')}  ·  {comic.get('aspect', '9:16')}  ·  {character_count} 角色  ·  {scene_count} 场景  ·  {shot_count} 分镜")
        self.comic_hero_status.configure(text="剪映草稿已完成" if video_ready else (f"{images_ready}/{shot_count} 张已完成" if shot_count else "本地自动保存"))
        for index, (outer, body, badge, title_label, subtitle_label) in enumerate(self.comic_step_widgets):
            active = index == self.comic_step
            body_bg = SIDEBAR if active else (COMIC_MINT if complete[index] else SURFACE)
            border_bg = ACCENT if active else ("#9DD4C9" if complete[index] else BORDER)
            badge_bg = ACCENT if active else (ACCENT_DARK if complete[index] else SURFACE_ALT)
            badge_fg = SIDEBAR if active else ("white" if complete[index] else MUTED)
            title_fg = "white" if active else (ACCENT_DARK if complete[index] else INK)
            subtitle_fg = "#C7E6E0" if active else (ACCENT_DARK if complete[index] else MUTED)
            outer.configure(bg=border_bg)
            body.configure(bg=body_bg)
            badge.configure(text="✓" if complete[index] and not active else str(index + 1), bg=badge_bg, fg=badge_fg)
            title_label.master.configure(bg=body_bg)
            title_label.configure(bg=body_bg, fg=title_fg)
            subtitle_label.configure(bg=body_bg, fg=subtitle_fg)
        self.comic_api_status.configure(text="● Key 已配置" if self.ark_api_key.get().strip() else "○ 尚未配置 Key", fg=ACCENT_DARK if self.ark_api_key.get().strip() else WARM)
        if getattr(self, "comic_generation_count_label", None):
            self.comic_generation_count_label.configure(text=f"{images_ready} / {shot_count}")

    def _comic_section_title(self, parent, number: str, title: str, description: str) -> None:
        header = Frame(parent, bg=parent.cget("bg"))
        header.pack(fill=X, pady=(0, 16))
        Label(header, text=f"STEP {number}", bg=COMIC_MINT, fg=ACCENT_DARK, padx=10, pady=6, font=("Segoe UI", 8, "bold")).pack(side=LEFT)
        text = Frame(header, bg=parent.cget("bg"))
        text.pack(side=LEFT, padx=(12, 0))
        Label(text, text=title, bg=parent.cget("bg"), fg=INK, font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        Label(text, text=description, bg=parent.cget("bg"), fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(2, 0))

    def _build_comic_source_step(self, parent) -> None:
        page, canvas = self._scrollable_content(parent)
        outer = self._card(page, padx=24, pady=20)
        outer.pack(fill=X, padx=(0, 8), pady=(0, 2))
        content = outer.winfo_children()[0]
        self._comic_section_title(content, "01", "小说与项目", "确定统一画风后，由 AI 根据剧情节奏拆分分镜并识别角色与固定场景。")

        settings = Frame(content, bg=SURFACE)
        settings.pack(fill=X)
        settings.grid_columnconfigure(0, weight=1)
        settings.grid_columnconfigure(1, weight=2)
        project = Frame(settings, bg=SURFACE)
        project.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self._field_label(project, "项目名").pack(anchor="w", pady=(0, 4))
        self._entry(project, self.comic_project_var).pack(fill=X, ipady=6)
        style = Frame(settings, bg=SURFACE)
        style.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self._field_label(style, "统一画风（会写入所有人物与分镜提示词）").pack(anchor="w", pady=(0, 4))
        RoundedCombobox(style, textvariable=self.comic_style_var, values=COMIC_STYLE_PRESETS).pack(fill=X)

        options_outer = self._card(content, bg=SURFACE_ALT, padx=14, pady=10)
        options_outer.pack(fill=X, pady=(14, 14))
        options = options_outer.winfo_children()[0]
        Label(options, text="画幅", bg=SURFACE_ALT, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side=LEFT)
        RoundedCombobox(options, textvariable=self.comic_aspect_var, values=["9:16", "4:5", "1:1", "16:9"], state="readonly", width=7).pack(side=LEFT, padx=(6, 18))
        Label(options, text="分镜由 AI 按场景、动作、对白与情绪转折自动拆分", bg=SURFACE_ALT, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 9, "bold")).pack(side=LEFT)
        self._button(options, "选择保存目录", self.choose_comic_output_dir, kind="ghost").pack(side=RIGHT)
        self.comic_output_hint = Label(options, text="", bg=SURFACE_ALT, fg=MUTED, font=("Microsoft YaHei UI", 8))
        self.comic_output_hint.pack(side=RIGHT, padx=(0, 10))
        output_text = self.comic_output_var.get().strip() or "将自动创建项目目录"
        self.comic_output_hint.configure(text=Path(output_text).name if self.comic_output_var.get().strip() else output_text)

        ai_hint_outer = self._card(content, bg=COMIC_MINT, padx=12, pady=8)
        ai_hint_outer.pack(fill=X, pady=(0, 12))
        ai_hint = ai_hint_outer.winfo_children()[0]
        Label(ai_hint, text="AI 分批保护", bg=COMIC_MINT, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 8, "bold")).pack(side=LEFT)
        Label(ai_hint, text="程序只按技术上限分批发送长文本；每批内部由 AI 自主决定分镜数量，并校验原文是否完整覆盖。失败时会自动缩小批次重试。", bg=COMIC_MINT, fg=INK, wraplength=680, justify=LEFT, font=("Microsoft YaHei UI", 8)).pack(side=LEFT, padx=(10, 0))

        editor_header = Frame(content, bg=SURFACE)
        editor_header.pack(fill=X)
        Label(editor_header, text="小说正文", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 11, "bold")).pack(side=LEFT)
        Label(editor_header, text="支持 TXT / Markdown / DOCX，也可以直接粘贴", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side=LEFT, padx=(10, 0))
        source_outer, source_shell = self._rounded_widget_shell(content)
        source_outer.pack(fill=X, pady=(8, 14))
        self.comic_source_editor = Text(source_shell, width=1, height=18, wrap="word", undo=True, bg=COMIC_INSET, fg=INK, insertbackground=INK, relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT_DARK, padx=16, pady=14, font=("Microsoft YaHei UI", 10))
        self._pack_vertical_scroller(source_shell, self.comic_source_editor, fill=X, expand=True)
        self.comic_source_editor.insert("1.0", self.state["comic"].get("source_text", ""))
        actions = Frame(content, bg=SURFACE)
        actions.pack(fill=X)
        self._button(actions, "导入小说", self.import_comic_novel, kind="ghost").pack(side=LEFT)
        self._button(actions, "角色 + 场景 + 分镜", lambda: self.analyze_comic_story("all"), kind="accent").pack(side=RIGHT)
        self._button(actions, "只生成静态分镜", lambda: self.analyze_comic_story("shots"), kind="primary").pack(side=RIGHT, padx=(0, 8))
        self._button(actions, "只识别场景", lambda: self.analyze_comic_story("scenes"), kind="ghost").pack(side=RIGHT, padx=(0, 8))
        self._button(actions, "只识别角色", lambda: self.analyze_comic_story("characters"), kind="ghost").pack(side=RIGHT, padx=(0, 8))
        self._bind_page_mousewheel(page, canvas)

    def _build_comic_character_step(self, parent) -> None:
        page, canvas = self._scrollable_content(parent)
        shell = Frame(page, bg=BG)
        shell.pack(fill=X, padx=(0, 8), pady=(0, 2))
        shell.grid_columnconfigure(0, weight=0, minsize=245)
        shell.grid_columnconfigure(1, weight=1)
        shell.grid_rowconfigure(0, weight=1)
        library_outer = self._card(shell, padx=16, pady=16)
        library_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        library = library_outer.winfo_children()[0]
        Label(library, text="共享角色资产", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        Label(library, text="角色定妆可被所有推文项目调用。", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(3, 10))
        character_list_outer, character_list_shell = self._rounded_widget_shell(library)
        character_list_outer.pack(fill=BOTH, expand=True)
        self.comic_character_list = Listbox(character_list_shell, exportselection=False, bg=COMIC_INSET, fg=INK, selectbackground=COMIC_MINT, selectforeground=ACCENT_DARK, relief="flat", highlightthickness=1, highlightbackground=BORDER, font=("Microsoft YaHei UI", 9), activestyle="none")
        self._pack_vertical_scroller(character_list_shell, self.comic_character_list)
        self.comic_character_list.bind("<<ListboxSelect>>", self.on_comic_character_select)
        library_actions = Frame(library, bg=SURFACE)
        library_actions.pack(fill=X, pady=(10, 0))
        self._button(library_actions, "+ 添加", self.add_comic_character, kind="primary").pack(side=LEFT)
        self._button(library_actions, "删除", self.delete_comic_character, kind="danger").pack(side=RIGHT)
        transfer_actions = Frame(library, bg=SURFACE)
        transfer_actions.pack(fill=X, pady=(7, 0))
        self._button(transfer_actions, "导入资产包", self.import_comic_assets, kind="ghost").pack(side=LEFT)
        self._button(transfer_actions, "导出资产包", self.export_comic_assets, kind="ghost").pack(side=RIGHT)

        editor_outer = self._card(shell, padx=24, pady=20)
        editor_outer.grid(row=0, column=1, sticky="nsew")
        editor = editor_outer.winfo_children()[0]
        self._comic_section_title(editor, "02", "共享角色定妆", "人物参考图只保留角色本身和纯色背景，不生成场景；确认后会保存到公共角色库供所有项目使用。")
        self.comic_character_preview_canvas, self.comic_character_preview_title = self._asset_preview_panel(
            editor,
            title="角色参考图",
            command=self.preview_comic_character,
        )
        self.comic_character_name_var = StringVar()
        self._field_label(editor, "角色名").pack(anchor="w", pady=(0, 4))
        self._entry(editor, self.comic_character_name_var).pack(fill=X, ipady=6)
        self.comic_character_base_var = StringVar(value=CHARACTER_BASE_NONE)
        self._field_label(editor, "关联本体角色（可选 · 用于同一人物换装）").pack(anchor="w", pady=(13, 5))
        self.comic_character_base_combo = RoundedCombobox(
            editor,
            textvariable=self.comic_character_base_var,
            values=[CHARACTER_BASE_NONE] + [str(item.get("name", "")).strip() for item in self.state["comic"].get("characters", []) if str(item.get("name", "")).strip()],
            state="readonly",
        )
        self.comic_character_base_combo.pack(fill=X)
        self.comic_character_base_combo.bind("<<ComboboxSelected>>", self._on_comic_character_base_selected)
        Label(
            editor,
            text="关联后生成候选图会自动使用本体的已确认参考图，只更换服装，不改变脸、发型和体态。",
            bg=SURFACE,
            fg=MUTED,
            wraplength=660,
            justify=LEFT,
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(5, 0))
        self._field_label(editor, "固定外貌，或换装服饰要求").pack(anchor="w", pady=(13, 5))
        description_outer, description_shell = self._rounded_widget_shell(editor, fixed_height=92)
        description_outer.pack(fill=X)
        self.comic_character_description_editor = Text(description_shell, height=3, wrap="word", undo=True, bg=COMIC_INSET, fg=INK, relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT_DARK, padx=12, pady=9, font=("Microsoft YaHei UI", 9))
        self._pack_vertical_scroller(description_shell, self.comic_character_description_editor, fill=X, expand=True)
        self._field_label(editor, "定妆提示词（可手动细化）").pack(anchor="w", pady=(13, 5))
        prompt_outer, prompt_shell = self._rounded_widget_shell(editor, fixed_height=96)
        prompt_outer.pack(fill=X)
        self.comic_character_prompt_editor = Text(prompt_shell, height=3, wrap="word", undo=True, bg=COMIC_INSET, fg=INK, relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT_DARK, padx=12, pady=9, font=("Microsoft YaHei UI", 9))
        self._pack_vertical_scroller(prompt_shell, self.comic_character_prompt_editor, fill=X, expand=True)
        prompt_actions = Frame(editor, bg=SURFACE)
        prompt_actions.pack(fill=X, pady=(7, 0))
        self._button(prompt_actions, "导入提示词", self.import_comic_character_prompt, kind="ghost").pack(side=LEFT)
        self._button(prompt_actions, "导出当前提示词", self.export_comic_character_prompt, kind="ghost").pack(side=LEFT, padx=(7, 0))
        Label(prompt_actions, text="修改后自动保存", bg=SURFACE, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 8, "bold")).pack(side=RIGHT)
        reference_outer = self._card(editor, bg=SURFACE_ALT, padx=12, pady=9)
        reference_outer.pack(fill=X, pady=(12, 10))
        reference = reference_outer.winfo_children()[0]
        Label(reference, text="参考图", bg=SURFACE_ALT, fg=MUTED, font=("Microsoft YaHei UI", 8, "bold")).pack(side=LEFT)
        self.comic_character_status = Label(reference, text="尚未选择角色", bg=SURFACE_ALT, fg=MUTED, wraplength=480, justify=LEFT, font=("Microsoft YaHei UI", 8))
        self.comic_character_status.pack(side=LEFT, padx=(10, 0))
        asset_actions = Frame(editor, bg=SURFACE)
        asset_actions.pack(fill=X, pady=(0, 9))
        self._button(asset_actions, "生成候选定妆", self.generate_comic_character, kind="accent").pack(side=LEFT)
        self._button(asset_actions, "确认候选为参考图", self.confirm_comic_character_candidate, kind="primary").pack(side=LEFT, padx=(8, 0))
        self._button(asset_actions, "导入并确认参考图", self.choose_comic_character_image, kind="ghost").pack(side=RIGHT)
        self._button(asset_actions, "预览候选/参考", self.preview_comic_character, kind="ghost").pack(side=RIGHT, padx=(0, 8))
        actions = Frame(editor, bg=SURFACE)
        actions.pack(fill=X)
        self._button(actions, "← 小说", lambda: self._switch_comic_step(0), kind="ghost").pack(side=LEFT)
        self._button(actions, "场景定景  →", lambda: self._switch_comic_step(2), kind="primary").pack(side=RIGHT, padx=(0, 8))
        self.comic_character_name_var.trace_add("write", lambda *_args: self._schedule_comic_asset_autosave("character"))
        self.comic_character_base_var.trace_add("write", lambda *_args: self._schedule_comic_asset_autosave("character"))
        for text_editor in (self.comic_character_description_editor, self.comic_character_prompt_editor):
            text_editor.bind("<KeyRelease>", lambda _event: self._schedule_comic_asset_autosave("character"), add="+")
            text_editor.bind("<<Paste>>", lambda _event: self.root.after_idle(lambda: self._schedule_comic_asset_autosave("character")), add="+")
        selected = self.current_comic_character_index if self.current_comic_character_index is not None else (0 if self.state["comic"]["characters"] else None)
        self._refresh_comic_character_list(selected)
        self._bind_page_mousewheel(page, canvas)

    def _build_comic_scene_step(self, parent) -> None:
        page, canvas = self._scrollable_content(parent)
        shell = Frame(page, bg=BG)
        shell.pack(fill=X, padx=(0, 8), pady=(0, 2))
        shell.grid_columnconfigure(0, weight=0, minsize=245)
        shell.grid_columnconfigure(1, weight=1)
        shell.grid_rowconfigure(0, weight=1)
        library_outer = self._card(shell, padx=16, pady=16)
        library_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        library = library_outer.winfo_children()[0]
        Label(library, text="场景资产", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        Label(library, text="先固定环境，再进入分镜。", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(3, 10))
        scene_list_outer, scene_list_shell = self._rounded_widget_shell(library)
        scene_list_outer.pack(fill=BOTH, expand=True)
        self.comic_scene_list = Listbox(scene_list_shell, exportselection=False, bg=COMIC_INSET, fg=INK, selectbackground=COMIC_MINT, selectforeground=ACCENT_DARK, relief="flat", highlightthickness=1, highlightbackground=BORDER, font=("Microsoft YaHei UI", 9), activestyle="none")
        self._pack_vertical_scroller(scene_list_shell, self.comic_scene_list)
        self.comic_scene_list.bind("<<ListboxSelect>>", self.on_comic_scene_select)
        library_actions = Frame(library, bg=SURFACE)
        library_actions.pack(fill=X, pady=(10, 0))
        self._button(library_actions, "+ 添加", self.add_comic_scene, kind="primary").pack(side=LEFT)
        self._button(library_actions, "删除", self.delete_comic_scene, kind="danger").pack(side=RIGHT)
        transfer_actions = Frame(library, bg=SURFACE)
        transfer_actions.pack(fill=X, pady=(7, 0))
        self._button(transfer_actions, "导入资产包", self.import_comic_assets, kind="ghost").pack(side=LEFT)
        self._button(transfer_actions, "导出资产包", self.export_comic_assets, kind="ghost").pack(side=RIGHT)

        editor_outer = self._card(shell, padx=24, pady=20)
        editor_outer.grid(row=0, column=1, sticky="nsew")
        editor = editor_outer.winfo_children()[0]
        self._comic_section_title(editor, "03", "场景定景", "固定空间布局、建筑结构、家具和光线；场景自动沿用角色定妆画风，确认后会绑定到对应分镜。")
        self.comic_scene_preview_canvas, self.comic_scene_preview_title = self._asset_preview_panel(
            editor,
            title="场景参考图",
            command=self.preview_comic_scene,
        )
        self.comic_scene_name_var = StringVar()
        self._field_label(editor, "场景名").pack(anchor="w", pady=(0, 4))
        self._entry(editor, self.comic_scene_name_var).pack(fill=X, ipady=6)
        self._field_label(editor, "固定空间、陈设、标志物与基础光线").pack(anchor="w", pady=(13, 5))
        scene_description_outer, scene_description_shell = self._rounded_widget_shell(editor, fixed_height=92)
        scene_description_outer.pack(fill=X)
        self.comic_scene_description_editor = Text(scene_description_shell, height=3, wrap="word", undo=True, bg=COMIC_INSET, fg=INK, relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT_DARK, padx=12, pady=9, font=("Microsoft YaHei UI", 9))
        self._pack_vertical_scroller(scene_description_shell, self.comic_scene_description_editor, fill=X, expand=True)
        self._field_label(editor, f"定景提示词（跟随角色画风：{self.state['comic']['art_style']}；保持无人物）").pack(anchor="w", pady=(13, 5))
        scene_prompt_outer, scene_prompt_shell = self._rounded_widget_shell(editor, fixed_height=96)
        scene_prompt_outer.pack(fill=X)
        self.comic_scene_prompt_editor = Text(scene_prompt_shell, height=3, wrap="word", undo=True, bg=COMIC_INSET, fg=INK, relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT_DARK, padx=12, pady=9, font=("Microsoft YaHei UI", 9))
        self._pack_vertical_scroller(scene_prompt_shell, self.comic_scene_prompt_editor, fill=X, expand=True)
        Label(editor, text="场景名称与提示词修改后自动保存并同步到分镜", bg=SURFACE, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 8, "bold")).pack(anchor="e", pady=(7, 0))
        reference_outer = self._card(editor, bg=SURFACE_ALT, padx=12, pady=9)
        reference_outer.pack(fill=X, pady=(12, 10))
        reference = reference_outer.winfo_children()[0]
        Label(reference, text="场景参考图", bg=SURFACE_ALT, fg=MUTED, font=("Microsoft YaHei UI", 8, "bold")).pack(side=LEFT)
        self.comic_scene_status = Label(reference, text="尚未选择场景", bg=SURFACE_ALT, fg=MUTED, wraplength=480, justify=LEFT, font=("Microsoft YaHei UI", 8))
        self.comic_scene_status.pack(side=LEFT, padx=(10, 0))
        asset_actions = Frame(editor, bg=SURFACE)
        asset_actions.pack(fill=X, pady=(0, 9))
        self._button(asset_actions, "生成候选场景", self.generate_comic_scene, kind="accent").pack(side=LEFT)
        self._button(asset_actions, "确认候选为参考图", self.confirm_comic_scene_candidate, kind="primary").pack(side=LEFT, padx=(8, 0))
        self._button(asset_actions, "导入并确认参考图", self.choose_comic_scene_image, kind="ghost").pack(side=RIGHT)
        self._button(asset_actions, "预览候选/参考", self.preview_comic_scene, kind="ghost").pack(side=RIGHT, padx=(0, 8))
        actions = Frame(editor, bg=SURFACE)
        actions.pack(fill=X)
        self._button(actions, "← 角色定妆", lambda: self._switch_comic_step(1), kind="ghost").pack(side=LEFT)
        self._button(actions, "静态分镜  →", lambda: self._switch_comic_step(3), kind="primary").pack(side=RIGHT, padx=(0, 8))
        self.comic_scene_name_var.trace_add("write", lambda *_args: self._schedule_comic_asset_autosave("scene"))
        for text_editor in (self.comic_scene_description_editor, self.comic_scene_prompt_editor):
            text_editor.bind("<KeyRelease>", lambda _event: self._schedule_comic_asset_autosave("scene"), add="+")
            text_editor.bind("<<Paste>>", lambda _event: self.root.after_idle(lambda: self._schedule_comic_asset_autosave("scene")), add="+")
        selected = self.current_comic_scene_index if self.current_comic_scene_index is not None else (0 if self.state["comic"]["scenes"] else None)
        self._refresh_comic_scene_list(selected)
        self._bind_page_mousewheel(page, canvas)

    def _build_comic_storyboard_step(self, parent) -> None:
        page, canvas = self._scrollable_content(parent)
        outer = self._card(page, padx=22, pady=18)
        outer.pack(fill=X, padx=(0, 8), pady=(0, 2))
        content = outer.winfo_children()[0]
        self._comic_section_title(content, "04", "静态分镜", "每个镜头都在对应行中直接修改画面提示词；人物与场景可在列表上方批量替换。")

        character_names = [str(item.get("name", "")).strip() for item in self.state["comic"].get("characters", []) if str(item.get("name", "")).strip()]
        scene_names = [str(item.get("name", "")).strip() for item in self.state["comic"].get("scenes", []) if str(item.get("name", "")).strip()]
        self.comic_batch_character_from_var.set(character_names[0] if character_names else "")
        self.comic_batch_character_to_var.set(character_names[1] if len(character_names) > 1 else "（移除角色）")
        self.comic_batch_scene_from_var.set(scene_names[0] if scene_names else "")
        self.comic_batch_scene_to_var.set(scene_names[1] if len(scene_names) > 1 else "（清空场景）")

        list_area = content
        list_header = Frame(list_area, bg=SURFACE_ALT, padx=12, pady=9)
        list_header.pack(fill=X)
        Label(list_header, text="分镜与对应画面", bg=SURFACE_ALT, fg=INK, font=("Microsoft YaHei UI", 10, "bold")).pack(side=LEFT)
        Label(list_header, text="提示词就在对应镜头行内，可随时修改并保存", bg=SURFACE_ALT, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side=LEFT, padx=(10, 0))
        self.comic_count_label = Label(list_header, text="", bg=SURFACE_ALT, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 9))
        self.comic_count_label.pack(side=RIGHT)

        batch = Frame(list_area, bg=COMIC_MINT, padx=12, pady=10)
        batch.pack(fill=X, padx=10, pady=(10, 4))
        batch.grid_columnconfigure(1, weight=1)
        batch.grid_columnconfigure(3, weight=1)
        batch.grid_columnconfigure(6, weight=1)
        batch.grid_columnconfigure(8, weight=1)
        Label(batch, text="人物 / 场景替换", bg=COMIC_MINT, fg=INK, font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 7))
        RoundedCombobox(batch, textvariable=self.comic_batch_scope_var, values=["全部分镜", "选中分镜"], state="readonly", width=10).grid(row=0, column=1, sticky="w", pady=(0, 7))
        Label(batch, text="无需先点开镜头；可替换全部分镜或当前选中的分镜", bg=COMIC_MINT, fg=MUTED, font=("Microsoft YaHei UI", 8)).grid(row=0, column=2, columnspan=7, sticky="w", padx=(12, 0), pady=(0, 7))
        Label(batch, text="角色", bg=COMIC_MINT, fg=MUTED, font=("Microsoft YaHei UI", 8, "bold")).grid(row=1, column=0, sticky="e", padx=(0, 5))
        RoundedCombobox(batch, textvariable=self.comic_batch_character_from_var, values=character_names, state="readonly", width=11).grid(row=1, column=1, sticky="ew")
        Label(batch, text="→", bg=COMIC_MINT, fg=ACCENT_DARK, font=("Segoe UI", 9, "bold")).grid(row=1, column=2, padx=5)
        RoundedCombobox(batch, textvariable=self.comic_batch_character_to_var, values=["（移除角色）"] + character_names, state="readonly", width=11).grid(row=1, column=3, sticky="ew")
        self._button(batch, "替换角色", self.batch_replace_comic_character, kind="primary").grid(row=1, column=4, sticky="w", padx=(6, 18))
        Label(batch, text="场景", bg=COMIC_MINT, fg=MUTED, font=("Microsoft YaHei UI", 8, "bold")).grid(row=1, column=5, sticky="e", padx=(0, 5))
        RoundedCombobox(batch, textvariable=self.comic_batch_scene_from_var, values=scene_names, state="readonly", width=11).grid(row=1, column=6, sticky="ew")
        Label(batch, text="→", bg=COMIC_MINT, fg=ACCENT_DARK, font=("Segoe UI", 9, "bold")).grid(row=1, column=7, padx=5)
        RoundedCombobox(batch, textvariable=self.comic_batch_scene_to_var, values=["（清空场景）"] + scene_names, state="readonly", width=11).grid(row=1, column=8, sticky="ew")
        self._button(batch, "替换场景", self.batch_replace_comic_scene, kind="primary").grid(row=1, column=9, sticky="w", padx=(6, 0))

        selection_bar = Frame(list_area, bg=SURFACE, padx=10, pady=7)
        selection_bar.pack(fill=X)
        self._button(selection_bar, "全选镜头", self.select_all_comic_shots, kind="ghost").pack(side=LEFT)
        self._button(selection_bar, "清空选择", self.clear_comic_shot_selection, kind="ghost").pack(side=LEFT, padx=(6, 0))
        Label(selection_bar, text="直接点击“选择”即可多选，无需按 Ctrl", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side=LEFT, padx=(12, 0))
        self._button(selection_bar, "拆分选中镜头", self.split_current_comic_shot, kind="primary").pack(side=RIGHT)
        self._button(selection_bar, "合并选中相邻镜头", self.merge_selected_comic_shots, kind="accent").pack(side=RIGHT, padx=(0, 7))
        page_bar = Frame(list_area, bg=SURFACE, padx=10, pady=3)
        page_bar.pack(fill=X)
        Label(page_bar, text="镜头较多时自动分页，已选镜头会跨页保留", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 7)).pack(side=LEFT)
        self._button(page_bar, "下一页  →", lambda: self._change_comic_storyboard_page(1), kind="ghost").pack(side=RIGHT)
        self.comic_storyboard_page_label = Label(page_bar, text="", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 8, "bold"))
        self.comic_storyboard_page_label.pack(side=RIGHT, padx=9)
        self._button(page_bar, "←  上一页", lambda: self._change_comic_storyboard_page(-1), kind="ghost").pack(side=RIGHT)
        self._create_comic_storyboard_list(list_area, height=548)

        footer = Frame(content, bg=SURFACE)
        footer.pack(fill=X, pady=(14, 0))
        self._button(footer, "← 场景定景", lambda: self._switch_comic_step(2), kind="ghost").pack(side=LEFT)
        self._button(footer, "进入批量出图  →", lambda: self._switch_comic_step(4), kind="accent").pack(side=RIGHT)
        selected = self.current_comic_shot_index if self.current_comic_shot_index is not None else (0 if self.state["comic"]["shots"] else None)
        self._refresh_comic_shot_tree(selected)
        self._bind_page_mousewheel(page, canvas)

    def _create_comic_storyboard_list(self, parent, *, height: int = 548) -> None:
        """Build the static-storyboard-only list whose prompts are edited in place."""
        shell_outer, shell = self._rounded_widget_shell(parent, bg=SURFACE, fixed_height=height)
        shell_outer.pack(fill=X, padx=10, pady=(0, 10))
        headers = Frame(shell, bg=SURFACE_ALT, padx=17, pady=8)
        headers.pack(fill=X, padx=(0, 13))
        self._configure_comic_storyboard_columns(headers)
        for column, text in enumerate(("选择", "对应画面", "片段 / 原文", "画面提示词（直接编辑）", "角色（点击多选）", "场景（点击单选）", "状态")):
            Label(headers, text=text, bg=SURFACE_ALT, fg=MUTED, anchor="w", font=("Microsoft YaHei UI", 8, "bold")).grid(row=0, column=column, sticky="ew", padx=(0, 8) if column < 6 else 0)

        canvas_host = Frame(shell, bg=SURFACE)
        canvas_host.pack(fill=BOTH, expand=True)
        self.comic_storyboard_canvas = Canvas(canvas_host, bg=SURFACE, highlightthickness=0, borderwidth=0)
        scrollbar = RoundedScrollbar(canvas_host, command=self.comic_storyboard_canvas.yview)
        self.comic_storyboard_canvas.configure(yscrollcommand=scrollbar.set)
        self.comic_storyboard_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.comic_storyboard_body = Frame(self.comic_storyboard_canvas, bg=SURFACE, padx=6, pady=4)
        self.comic_storyboard_window = self.comic_storyboard_canvas.create_window((0, 0), window=self.comic_storyboard_body, anchor="nw")
        self.comic_storyboard_body.bind("<Configure>", lambda _event: self.comic_storyboard_canvas and self.comic_storyboard_canvas.configure(scrollregion=self.comic_storyboard_canvas.bbox("all")))
        self.comic_storyboard_canvas.bind("<Configure>", lambda event: self.comic_storyboard_canvas and self.comic_storyboard_window is not None and self.comic_storyboard_canvas.itemconfigure(self.comic_storyboard_window, width=event.width))

    @staticmethod
    def _configure_comic_storyboard_columns(container: Frame) -> None:
        """Keep header cells and every editable storyboard row on one shared grid."""
        specifications = (
            (0, 0, 71),
            (1, 0, 114),
            (2, 2, 160),
            (3, 3, 230),
            (4, 1, 105),
            (5, 1, 120),
            (6, 0, 80),
        )
        for column, weight, minsize in specifications:
            container.grid_columnconfigure(column, weight=weight, minsize=minsize)

    def _bind_comic_storyboard_wheel(self, widget) -> None:
        if not self.comic_storyboard_canvas:
            return

        def scroll(event):
            if not self.comic_storyboard_canvas:
                return "break"
            if getattr(event, "delta", 0):
                self.comic_storyboard_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            elif getattr(event, "num", 0) == 4:
                self.comic_storyboard_canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", 0) == 5:
                self.comic_storyboard_canvas.yview_scroll(1, "units")
            return "break"

        if not isinstance(widget, (Text, Listbox, RoundedCombobox)):
            widget.bind("<MouseWheel>", scroll, add="+")
            widget.bind("<Button-4>", scroll, add="+")
            widget.bind("<Button-5>", scroll, add="+")
        for child in widget.winfo_children():
            self._bind_comic_storyboard_wheel(child)

    def _change_comic_storyboard_page(self, delta: int) -> None:
        shots = self.state["comic"].get("shots", [])
        page_count = max(1, (len(shots) + self.comic_storyboard_page_size - 1) // self.comic_storyboard_page_size)
        target = min(max(self.comic_storyboard_page + int(delta), 0), page_count - 1)
        if target == self.comic_storyboard_page:
            return
        self._save_all_inline_comic_shot_prompts()
        self.comic_storyboard_page = target
        self._refresh_comic_storyboard_rows()
        if self.comic_storyboard_canvas:
            self.comic_storyboard_canvas.yview_moveto(0)

    def _refresh_comic_storyboard_rows(self, selected: int | None = None) -> None:
        if not self.comic_storyboard_body:
            return
        for child in self.comic_storyboard_body.winfo_children():
            child.destroy()
        self.comic_storyboard_selection_vars = {}
        self.comic_storyboard_prompt_editors = {}
        self.comic_storyboard_row_widgets = {}
        self.comic_shot_preview_images = {}
        shots = self.state["comic"].get("shots", [])
        character_names = [str(item.get("name", "")).strip() for item in self.state["comic"].get("characters", []) if str(item.get("name", "")).strip()]
        scene_names = [str(item.get("name", "")).strip() for item in self.state["comic"].get("scenes", []) if str(item.get("name", "")).strip()]
        self.comic_storyboard_selected_indices = {index for index in self.comic_storyboard_selected_indices if 0 <= index < len(shots)}
        if selected is not None and shots:
            selected = min(max(selected, 0), len(shots) - 1)
            if not self.comic_storyboard_selected_indices:
                self.comic_storyboard_selected_indices.add(selected)
            self.comic_storyboard_page = selected // self.comic_storyboard_page_size

        page_count = max(1, (len(shots) + self.comic_storyboard_page_size - 1) // self.comic_storyboard_page_size)
        self.comic_storyboard_page = min(max(self.comic_storyboard_page, 0), page_count - 1)
        start_index = self.comic_storyboard_page * self.comic_storyboard_page_size
        end_index = min(start_index + self.comic_storyboard_page_size, len(shots))

        if not shots:
            Label(self.comic_storyboard_body, text="暂无分镜。请返回“小说与项目”使用 AI 拆分，或导入已有项目。", bg=SURFACE, fg=MUTED, pady=70, font=("Microsoft YaHei UI", 10)).pack(fill=X)
            self.current_comic_shot_index = None
        for index in range(start_index, end_index):
            shot = shots[index]
            selected_row = index in self.comic_storyboard_selected_indices
            row_bg = COMIC_INSET if index % 2 else SURFACE
            row_outer = Frame(self.comic_storyboard_body, bg=row_bg, height=160, highlightthickness=1, highlightbackground=ACCENT if selected_row else row_bg)
            row_outer.pack(fill=X, pady=(0, 2))
            row_outer.pack_propagate(False)
            row = Frame(row_outer, bg=row_bg, padx=10, pady=8)
            row.pack(fill=BOTH, expand=True)
            self._configure_comic_storyboard_columns(row)

            selection_var = StringVar(value="✓ 已选" if selected_row else "○ 选择")
            selection = Label(row, textvariable=selection_var, width=7, bg=ACCENT if selected_row else SURFACE_ALT, fg=SIDEBAR if selected_row else MUTED, cursor="hand2", padx=5, pady=8, font=("Microsoft YaHei UI", 8, "bold"))
            selection.grid(row=0, column=0, sticky="n", padx=(0, 8), pady=(4, 0))
            selection.bind("<Button-1>", lambda _event, item=index: self._toggle_comic_storyboard_row(item))

            preview = Canvas(row, width=104, height=124, bg=SURFACE_ALT, highlightthickness=0, borderwidth=0, cursor="hand2")
            preview.grid(row=0, column=1, sticky="n", padx=(0, 10))
            self._render_local_image(preview, str(shot.get("local_path", "")), placeholder="等待出图", max_size=(104, 124))
            preview.bind("<Button-1>", lambda _event, item=index: self._open_comic_storyboard_image(item))

            source_panel = Frame(row, bg=row_bg)
            source_panel.grid(row=0, column=2, sticky="nsew", padx=(0, 10))
            source_panel.pack_propagate(False)
            Label(source_panel, text=str(shot.get("title", f"分镜 {index + 1:03d}")), bg=row_bg, fg=INK, anchor="w", justify=LEFT, font=("Microsoft YaHei UI", 9, "bold")).pack(fill=X, pady=(3, 5))
            source = str(shot.get("source", "")).strip().replace("\n", " ") or "暂无对应原文"
            Label(source_panel, text=source, bg=row_bg, fg=MUTED, anchor="nw", justify=LEFT, wraplength=220, font=("Microsoft YaHei UI", 8)).pack(fill=BOTH, expand=True)

            prompt_panel = Frame(row, bg=row_bg)
            prompt_panel.grid(row=0, column=3, sticky="nsew", padx=(0, 10))
            prompt_panel.pack_propagate(False)
            prompt_header = Frame(prompt_panel, bg=row_bg)
            prompt_header.pack(fill=X, pady=(0, 4))
            Label(prompt_header, text="表情 / 动作", bg=row_bg, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 8, "bold")).pack(side=LEFT)
            self._button(prompt_header, "保存", lambda item=index: self._save_inline_comic_shot_prompt(item), kind="accent").pack(side=RIGHT)
            prompt_editor_host = Frame(prompt_panel, bg=row_bg)
            prompt_editor_host.pack(fill=BOTH, expand=True)
            prompt_editor = Text(prompt_editor_host, width=1, height=4, wrap="word", undo=True, bg="#F3F8F7", fg=INK, relief="flat", highlightthickness=0, padx=8, pady=6, font=("Microsoft YaHei UI", 8))
            self._pack_vertical_scroller(prompt_editor_host, prompt_editor, fill=X, expand=True)
            prompt_editor.insert("1.0", str(shot.get("prompt", "")))
            prompt_editor.bind("<FocusOut>", lambda _event, item=index: self._save_inline_comic_shot_prompt(item, silent=True), add="+")
            prompt_editor.bind("<Control-s>", lambda _event, item=index: (self._save_inline_comic_shot_prompt(item), "break")[1], add="+")

            role_panel = Frame(row, bg=row_bg)
            role_panel.grid(row=0, column=4, sticky="nsew", padx=(0, 8))
            role_panel.pack_propagate(False)
            role_values = list(character_names)
            for name in (str(item).strip() for item in shot.get("characters", [])):
                if name and name not in role_values:
                    role_values.append(name)
            character_list = None
            if role_values:
                character_list = Listbox(
                    role_panel,
                    selectmode="multiple",
                    exportselection=False,
                    bg=row_bg,
                    fg=INK,
                    selectbackground=COMIC_MINT,
                    selectforeground=ACCENT_DARK,
                    relief="flat",
                    highlightthickness=0,
                    activestyle="none",
                    font=("Microsoft YaHei UI", 8),
                )
                selected_characters = {str(name) for name in shot.get("characters", [])}
                for character_index, name in enumerate(role_values):
                    character_list.insert(END, name)
                    if name in selected_characters:
                        character_list.selection_set(character_index)
                self._pack_vertical_scroller(role_panel, character_list)
                character_list.bind("<<ListboxSelect>>", lambda _event, item=index: self._save_inline_comic_shot_characters(item), add="+")
            else:
                Label(role_panel, text="暂无角色\n请先定妆", bg=row_bg, fg=MUTED, justify="center", font=("Microsoft YaHei UI", 8)).pack(fill=BOTH, expand=True)

            scene_panel = Frame(row, bg=row_bg)
            scene_panel.grid(row=0, column=5, sticky="nsew", padx=(0, 8))
            scene_panel.pack_propagate(False)
            current_scene = str(shot.get("scene", "")).strip()
            scene_values = ["（无场景）"] + list(scene_names)
            if current_scene and current_scene not in scene_values:
                scene_values.append(current_scene)
            scene_list = Listbox(
                scene_panel,
                selectmode="browse",
                exportselection=False,
                bg=row_bg,
                fg=INK,
                selectbackground=COMIC_MINT,
                selectforeground=ACCENT_DARK,
                relief="flat",
                highlightthickness=0,
                activestyle="none",
                font=("Microsoft YaHei UI", 8),
            )
            selected_scene_value = current_scene or "（无场景）"
            for scene_index, name in enumerate(scene_values):
                scene_list.insert(END, name)
                if name == selected_scene_value:
                    scene_list.selection_set(scene_index)
                    scene_list.activate(scene_index)
            self._pack_vertical_scroller(scene_panel, scene_list)
            scene_list.bind("<<ListboxSelect>>", lambda _event, item=index: self._save_inline_comic_shot_scene(item), add="+")
            status_label = Label(row, text=self._comic_shot_status_text(shot), bg=row_bg, fg=ACCENT_DARK if shot.get("local_path") else MUTED, anchor="n", justify="center", wraplength=90, font=("Microsoft YaHei UI", 8))
            status_label.grid(row=0, column=6, sticky="nsew", pady=(9, 0))

            self.comic_storyboard_selection_vars[index] = selection_var
            self.comic_storyboard_prompt_editors[index] = prompt_editor
            self.comic_storyboard_row_widgets[index] = {
                "outer": row_outer,
                "default_border": row_bg,
                "selection": selection,
                "preview": preview,
                "characters": character_list,
                "character_values": role_values,
                "scene": scene_list,
                "scene_values": scene_values,
                "status": status_label,
            }
            self._bind_comic_storyboard_wheel(row_outer)

        done = sum(1 for shot in shots if Path(str(shot.get("local_path", ""))).is_file())
        if self.comic_count_label:
            self.comic_count_label.configure(text=f"{len(shots)} 个分镜 · {done} 张有图")
        if self.comic_storyboard_page_label:
            visible_range = f"{start_index + 1}-{end_index}" if shots else "0"
            self.comic_storyboard_page_label.configure(text=f"第 {self.comic_storyboard_page + 1}/{page_count} 页 · 镜头 {visible_range}")
        if self.comic_storyboard_canvas:
            storyboard_canvas = self.comic_storyboard_canvas

            def update_scrollregion() -> None:
                if self.comic_storyboard_canvas is not storyboard_canvas:
                    return
                try:
                    storyboard_canvas.configure(scrollregion=storyboard_canvas.bbox("all"))
                except TclError:
                    pass

            self.root.after_idle(update_scrollregion)

    @staticmethod
    def _comic_shot_status_text(shot: dict[str, object]) -> str:
        status = str(shot.get("status", "待生成"))
        progress = str(shot.get("progress", ""))
        return f"{status}\n{progress}" if progress and progress not in {"0%", "100%"} else status

    def _toggle_comic_storyboard_row(self, index: int) -> None:
        if index in self.comic_storyboard_selected_indices:
            self.comic_storyboard_selected_indices.remove(index)
        else:
            self.comic_storyboard_selected_indices.add(index)
            self.current_comic_shot_index = index
        selected = index in self.comic_storyboard_selected_indices
        selection_var = self.comic_storyboard_selection_vars.get(index)
        row_widgets = self.comic_storyboard_row_widgets.get(index, {})
        if selection_var:
            selection_var.set("✓ 已选" if selected else "○ 选择")
        selection = row_widgets.get("selection")
        outer = row_widgets.get("outer")
        if selection:
            selection.configure(bg=ACCENT if selected else SURFACE_ALT, fg=SIDEBAR if selected else MUTED)
        if outer:
            outer.configure(highlightbackground=ACCENT if selected else row_widgets.get("default_border", SURFACE))

    def _open_comic_storyboard_image(self, index: int) -> None:
        shots = self.state["comic"].get("shots", [])
        if not (0 <= index < len(shots)):
            return
        path = str(shots[index].get("local_path", ""))
        if Path(path).is_file():
            self._show_comic_image(path, str(shots[index].get("title", f"分镜 {index + 1:03d}")))
        else:
            self.comic_status.configure(text=f"分镜 {index + 1:03d} 尚未生成图片。", fg=MUTED)

    def _save_inline_comic_shot_prompt(self, index: int, silent: bool = False) -> None:
        shots = self.state["comic"].get("shots", [])
        editor = self.comic_storyboard_prompt_editors.get(index)
        if not editor or not (0 <= index < len(shots)):
            return
        try:
            new_prompt = editor.get("1.0", "end-1c").strip()
        except TclError:
            return
        shot = shots[index]
        old_prompt = str(shot.get("prompt", "")).strip()
        if old_prompt == new_prompt:
            return
        shot["prompt"] = new_prompt
        if shot.get("local_path") or shot.get("image_url"):
            self._mark_comic_shot_stale(shot)
            self._invalidate_comic_draft()
        self._schedule_state_save()
        self._update_comic_storyboard_row(index)
        if not silent and hasattr(self, "comic_status"):
            self.comic_status.configure(text=f"分镜 {index + 1:03d} 的提示词已保存。", fg=ACCENT_DARK)

    def _save_inline_comic_shot_characters(self, index: int, silent: bool = False) -> None:
        shots = self.state["comic"].get("shots", [])
        widgets = self.comic_storyboard_row_widgets.get(index, {})
        character_list = widgets.get("characters")
        if not character_list or not (0 <= index < len(shots)):
            return
        try:
            new_characters = [str(character_list.get(item)).strip() for item in character_list.curselection() if str(character_list.get(item)).strip()]
        except TclError:
            return
        shot = shots[index]
        old_characters = [str(name).strip() for name in shot.get("characters", []) if str(name).strip()]
        if old_characters == new_characters:
            return
        shot["characters"] = new_characters
        if shot.get("local_path") or shot.get("image_url"):
            self._mark_comic_shot_stale(shot)
            self._invalidate_comic_draft()
        self._schedule_state_save()
        self._update_comic_storyboard_row(index)
        if not silent and hasattr(self, "comic_status"):
            self.comic_status.configure(text=f"分镜 {index + 1:03d} 的角色已直接更新。", fg=ACCENT_DARK)

    def _save_inline_comic_shot_scene(self, index: int, silent: bool = False) -> None:
        shots = self.state["comic"].get("shots", [])
        widgets = self.comic_storyboard_row_widgets.get(index, {})
        scene_list = widgets.get("scene")
        if not scene_list or not (0 <= index < len(shots)):
            return
        try:
            selection = scene_list.curselection()
            if not selection:
                return
            new_scene = str(scene_list.get(selection[0])).strip()
        except TclError:
            return
        if new_scene == "（无场景）":
            new_scene = ""
        shot = shots[index]
        if str(shot.get("scene", "")).strip() == new_scene:
            return
        shot["scene"] = new_scene
        if shot.get("local_path") or shot.get("image_url"):
            self._mark_comic_shot_stale(shot)
            self._invalidate_comic_draft()
        self._schedule_state_save()
        self._update_comic_storyboard_row(index)
        if not silent and hasattr(self, "comic_status"):
            self.comic_status.configure(text=f"分镜 {index + 1:03d} 的场景已直接更新。", fg=ACCENT_DARK)

    def _save_all_inline_comic_shot_prompts(self) -> None:
        for index in list(self.comic_storyboard_prompt_editors):
            self._save_inline_comic_shot_prompt(index, silent=True)
            self._save_inline_comic_shot_characters(index, silent=True)
            self._save_inline_comic_shot_scene(index, silent=True)

    def _update_comic_storyboard_row(self, index: int) -> None:
        shots = self.state["comic"].get("shots", [])
        widgets = self.comic_storyboard_row_widgets.get(index)
        if not widgets or not (0 <= index < len(shots)):
            return
        shot = shots[index]
        character_list = widgets.get("characters")
        if character_list:
            selected_characters = {str(name) for name in shot.get("characters", [])}
            character_list.selection_clear(0, END)
            for character_index, name in enumerate(widgets.get("character_values", [])):
                if name in selected_characters:
                    character_list.selection_set(character_index)
        scene_list = widgets.get("scene")
        if scene_list:
            selected_scene = str(shot.get("scene", "")).strip() or "（无场景）"
            scene_list.selection_clear(0, END)
            for scene_index, name in enumerate(widgets.get("scene_values", [])):
                if name == selected_scene:
                    scene_list.selection_set(scene_index)
                    scene_list.activate(scene_index)
                    scene_list.see(scene_index)
                    break
        widgets["status"].configure(text=self._comic_shot_status_text(shot), fg=ACCENT_DARK if shot.get("local_path") else MUTED)
        self._render_local_image(widgets["preview"], str(shot.get("local_path", "")), placeholder="等待出图", max_size=(104, 124))

    def _create_comic_shot_tree(self, parent, *, with_previews: bool = False, preview_height: int = 650) -> None:
        tree_outer, tree_shell = self._rounded_widget_shell(parent, bg=SURFACE)
        if with_previews:
            tree_outer.set_fixed_height(preview_height)
        tree_outer.pack(fill=BOTH, expand=True)
        self.comic_shot_tree_with_previews = with_previews
        self.comic_shot_preview_images = {}
        self.comic_shot_tree = ttk.Treeview(
            tree_shell,
            columns=("title", "characters", "scene", "status"),
            show=("tree", "headings") if with_previews else "headings",
            style="Studio.Preview.Treeview" if with_previews else "Studio.Treeview",
            selectmode="extended",
            height=4 if with_previews else 10,
        )
        if with_previews:
            self.comic_shot_tree.heading("#0", text="对应图片")
            self.comic_shot_tree.column("#0", width=132, minwidth=132, stretch=False, anchor="center")
        self.comic_shot_tree.heading("title", text="片段")
        self.comic_shot_tree.heading("characters", text="角色")
        self.comic_shot_tree.heading("scene", text="场景")
        self.comic_shot_tree.heading("status", text="状态")
        self.comic_shot_tree.column("title", width=185 if with_previews else 210, anchor="w")
        self.comic_shot_tree.column("characters", width=90 if with_previews else 100, anchor="w")
        self.comic_shot_tree.column("scene", width=85 if with_previews else 105, anchor="w")
        self.comic_shot_tree.column("status", width=75 if with_previews else 90, anchor="center")
        self.comic_shot_tree.tag_configure("alternate", background=COMIC_INSET)
        self.comic_shot_tree.tag_configure("done", foreground=ACCENT_DARK)
        self.comic_shot_tree.tag_configure("error", foreground=ERROR)
        tree_scrollbar = self._pack_vertical_scroller(tree_shell, self.comic_shot_tree)
        if with_previews:
            def update_preview_scroll(first, last) -> None:
                tree_scrollbar.set(first, last)
                self._schedule_visible_comic_shot_previews()

            self.comic_shot_tree.configure(yscrollcommand=update_preview_scroll)
            self.comic_shot_tree.bind("<Configure>", lambda _event: self._schedule_visible_comic_shot_previews(), add="+")
            self.comic_shot_tree.bind("<KeyRelease>", lambda _event: self._schedule_visible_comic_shot_previews(), add="+")
        self.comic_shot_tree.bind("<<TreeviewSelect>>", self.on_comic_shot_select)
        self.comic_shot_tree.bind("<Button-1>", self._toggle_comic_shot_selection)

    def _build_comic_generation_step(self, parent) -> None:
        page, canvas = self._scrollable_content(parent)
        shell = Frame(page, bg=BG)
        shell.pack(fill=X, padx=(0, 8), pady=(0, 2))
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_columnconfigure(1, weight=0, minsize=260)
        shell.grid_rowconfigure(0, weight=1)
        list_outer = self._card(shell, padx=20, pady=18)
        list_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        listing = list_outer.winfo_children()[0]
        self._comic_section_title(listing, "05", "批量出图", "每张图片固定显示在对应分镜旁边；角色参考图与固定场景参考图会一并提交。")
        list_header = Frame(listing, bg=SURFACE_ALT, padx=12, pady=9)
        list_header.pack(fill=X)
        Label(list_header, text="分镜与对应图片", bg=SURFACE_ALT, fg=INK, font=("Microsoft YaHei UI", 10, "bold")).pack(side=LEFT)
        Label(list_header, text="单击左侧图片即可放大；待生成分镜会显示占位图", bg=SURFACE_ALT, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side=LEFT, padx=(10, 0))
        self.comic_count_label = Label(list_header, text="", bg=SURFACE_ALT, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 9))
        self.comic_count_label.pack(side=RIGHT)
        selection_bar = Frame(listing, bg=SURFACE, padx=2, pady=8)
        selection_bar.pack(fill=X)
        self._button(selection_bar, "全选镜头", self.select_all_comic_shots, kind="ghost").pack(side=LEFT)
        self._button(selection_bar, "清空选择", self.clear_comic_shot_selection, kind="ghost").pack(side=LEFT, padx=(6, 0))
        Label(
            selection_bar,
            text="直接单击片段、角色、场景或状态单元格即可连续多选，无需按 Ctrl",
            bg=SURFACE,
            fg=MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=LEFT, padx=(12, 0))
        self._create_comic_shot_tree(listing, with_previews=True)
        footer = Frame(listing, bg=SURFACE)
        footer.pack(fill=X, pady=(12, 0))
        self._button(footer, "← 静态分镜", lambda: self._switch_comic_step(3), kind="ghost").pack(side=LEFT)
        self._button(footer, "放大选中图片", self.preview_comic_shot, kind="ghost").pack(side=RIGHT)
        self._button(footer, "生成当前镜头", self.generate_selected_comic_shot, kind="primary").pack(side=RIGHT, padx=(0, 8))
        self._button(footer, "重新绘制已选镜头", self.redraw_selected_comic_shots, kind="accent").pack(side=RIGHT, padx=(0, 8))

        control_outer = self._card(shell, bg=SIDEBAR, padx=20, pady=20)
        control_outer.grid(row=0, column=1, sticky="nsew")
        control = control_outer.winfo_children()[0]
        Label(control, text="生产控制", bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        Label(control, text="分镜可选 Lite / Pro", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 8, "bold")).pack(anchor="w", pady=(3, 16))
        metric = Frame(control, bg=COMIC_DARK_ALT, padx=14, pady=12)
        metric.pack(fill=X, pady=(0, 10))
        self.comic_generation_count_label = Label(metric, text="0 / 0", bg=COMIC_DARK_ALT, fg="white", font=("Segoe UI", 21, "bold"))
        self.comic_generation_count_label.pack(anchor="w")
        Label(metric, text="分镜图片已完成", bg=COMIC_DARK_ALT, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        option = Frame(control, bg=COMIC_DARK_ALT, padx=12, pady=10)
        option.pack(fill=X)
        Label(option, text="分镜生图模型", bg=COMIC_DARK_ALT, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        model_buttons = Frame(option, bg=COMIC_DARK_ALT)
        model_buttons.pack(fill=X, pady=(6, 5))
        self._button(
            model_buttons,
            "Lite · 省成本",
            lambda: self._select_comic_shot_model(SHOT_IMAGE_MODEL_OPTIONS[0]),
            kind="accent",
        ).pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        self._button(
            model_buttons,
            "Pro · 高质量",
            lambda: self._select_comic_shot_model(SHOT_IMAGE_MODEL_OPTIONS[1]),
            kind="primary",
        ).pack(side=LEFT, fill=X, expand=True, padx=(4, 0))
        Label(option, textvariable=self.comic_shot_model_var, bg=COMIC_DARK_ALT, fg=ACCENT, wraplength=210, justify=LEFT, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(0, 10))
        Label(option, text="输出分辨率", bg=COMIC_DARK_ALT, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        resolution_buttons = Frame(option, bg=COMIC_DARK_ALT)
        resolution_buttons.pack(fill=X, pady=(6, 3))
        for resolution in ("1K", "2K", "3K", "4K"):
            button = self._button(
                resolution_buttons,
                resolution,
                lambda value=resolution: self._select_comic_resolution(value),
                kind="glass",
                width=3,
            )
            self.comic_resolution_buttons[resolution] = button
        Label(
            option,
            textvariable=self.comic_resolution_hint_var,
            bg=COMIC_DARK_ALT,
            fg=SIDEBAR_MUTED,
            wraplength=210,
            justify=LEFT,
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(0, 10))
        self._refresh_comic_resolution_controls()
        Label(option, text="提示词优化", bg=COMIC_DARK_ALT, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        RoundedCombobox(option, textvariable=self.comic_optimize_var, values=["标准质量", "极速模式"], state="readonly", width=10).pack(anchor="w", pady=(5, 0))
        cover_control = Frame(control, bg=COMIC_DARK_ALT, padx=12, pady=11)
        cover_control.pack(fill=X, pady=(10, 0))
        Label(cover_control, text="项目封面", bg=COMIC_DARK_ALT, fg="white", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.comic_cover_status_label = Label(
            cover_control,
            text="未生成",
            bg=COMIC_DARK_ALT,
            fg=SIDEBAR_MUTED,
            anchor="w",
            justify=LEFT,
            wraplength=205,
            font=("Microsoft YaHei UI", 8),
        )
        self.comic_cover_status_label.pack(fill=X, pady=(3, 7))
        cover_actions = Frame(cover_control, bg=COMIC_DARK_ALT)
        cover_actions.pack(fill=X)
        self._button(cover_actions, "制作封面", self.open_comic_cover_editor, kind="accent").pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        self._button(cover_actions, "放大预览", self.preview_comic_cover, kind="glass").pack(side=LEFT, fill=X, expand=True, padx=(4, 0))
        self.comic_generation_detail_label = Label(control, text="请选择一个分镜查看状态。", bg=SIDEBAR, fg=SIDEBAR_MUTED, justify=LEFT, wraplength=220, font=("Microsoft YaHei UI", 8))
        self.comic_generation_detail_label.pack(fill=X, pady=(18, 16))
        self._button(control, "批量生成未完成分镜", self.generate_all_comic_shots, kind="accent").pack(fill=X)
        self._button(control, "生成剪映草稿  →", lambda: self._switch_comic_step(5), kind="primary").pack(fill=X, pady=(8, 0))
        self._button(control, "打开图片目录", self.open_comic_output_dir, kind="ghost").pack(fill=X, pady=(8, 0))
        Label(control, text="提示：生成前必须为出场角色和已绑定场景确认参考图，以保持人物与环境一致性。", bg=SIDEBAR, fg=SIDEBAR_MUTED, justify=LEFT, wraplength=220, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(18, 0))
        selected = self.current_comic_shot_index if self.current_comic_shot_index is not None else (0 if self.state["comic"]["shots"] else None)
        self._refresh_comic_shot_tree(selected)
        self._refresh_comic_cover_widgets()
        self._bind_page_mousewheel(page, canvas)

    def _comic_cover_record(self) -> dict[str, object]:
        comic = self.state["comic"]
        cover = comic.get("cover")
        if not isinstance(cover, dict):
            cover = {}
            comic["cover"] = cover
        defaults: dict[str, object] = {
            "title": "",
            "prompt": "",
            "character": "",
            "scene": "",
            "task_id": "",
            "status": "未生成",
            "progress": "0%",
            "image_url": "",
            "local_path": "",
            "error": "",
            "final_prompt": "",
            "image_model": "",
            "images": [],
        }
        for key, value in defaults.items():
            cover.setdefault(key, value)
        if not isinstance(cover.get("images"), list):
            cover["images"] = []
        legacy_path = str(cover.get("local_path", "")).strip()
        if legacy_path and not cover["images"]:
            cover["images"] = [
                {
                    "aspect": str(self.state["comic"].get("aspect", "9:16")),
                    "ordinal": 1,
                    "local_path": legacy_path,
                    "image_url": str(cover.get("image_url", "")),
                    "status": str(cover.get("status", "已完成")),
                    "error": str(cover.get("error", "")),
                    "task_id": str(cover.get("task_id", "")),
                    "image_model": str(cover.get("image_model", "")),
                    "final_prompt": str(cover.get("final_prompt", "")),
                }
            ]
        return cover

    def _comic_cover_image_record(self, aspect: str, ordinal: int) -> dict[str, object]:
        cover = self._comic_cover_record()
        images = cover["images"]
        record = next(
            (
                item
                for item in images
                if isinstance(item, dict)
                and str(item.get("aspect", "")) == aspect
                and int(item.get("ordinal", 0) or 0) == ordinal
            ),
            None,
        )
        if record is None:
            record = {
                "aspect": aspect,
                "ordinal": ordinal,
                "local_path": "",
                "image_url": "",
                "status": "未生成",
                "error": "",
                "task_id": "",
                "image_model": "",
                "final_prompt": "",
            }
            images.append(record)
        return record

    def _refresh_comic_cover_widgets(self) -> None:
        cover = self._comic_cover_record()
        status = str(cover.get("status", "未生成")) or "未生成"
        title = str(cover.get("title", "")).strip() or str(self.state["comic"].get("project_name", "漫画推文"))
        images = [item for item in cover.get("images", []) if isinstance(item, dict)]
        planned_keys = set(COMIC_COVER_OUTPUT_PLAN)
        ready_count = sum(
            1
            for item in images
            if (str(item.get("aspect", "")), int(item.get("ordinal", 0) or 0)) in planned_keys
            and Path(str(item.get("local_path", ""))).is_file()
        )
        summary = f"{status} · {ready_count}/4 张 · {title}" if ready_count else status
        for label in (getattr(self, "comic_cover_status_label", None), getattr(self, "comic_cover_dialog_status_label", None)):
            if label is None:
                continue
            try:
                if label.winfo_exists():
                    label.configure(text=summary, fg=ACCENT if ready_count else (ERROR if cover.get("error") else SIDEBAR_MUTED))
            except TclError:
                pass
        previews = getattr(self, "comic_cover_preview_canvases", {})
        image_map = {
            (str(item.get("aspect", "")), int(item.get("ordinal", 0) or 0)): str(item.get("local_path", ""))
            for item in images
        }
        for key, preview in previews.items():
            try:
                if preview.winfo_exists():
                    aspect, ordinal = key
                    self._render_local_image(
                        preview,
                        image_map.get(key, ""),
                        placeholder=f"{aspect} · 第 {ordinal} 张\n等待生成",
                        max_size=(170, 205),
                    )
            except TclError:
                pass

    def _suggest_comic_cover_visual(self) -> str:
        character = self.comic_cover_character_var.get().strip()
        scene = self.comic_cover_scene_var.get().strip()
        if character.startswith("（不使用"):
            character = ""
        if scene.startswith("（不使用"):
            scene = ""
        subject = f"{character}以中近景面对镜头，呈现强烈而清晰的表情和动作" if character else "核心人物以中近景面对镜头，呈现强烈而清晰的表情和动作"
        environment = f"，背景为{scene}并保持环境可辨识" if scene else "，背景简洁并营造剧情冲突氛围"
        return f"{subject}{environment}，画面有悬念感和视觉冲击力，人物面部与封面标题都清楚醒目"

    def _fill_comic_cover_prompt(self) -> None:
        editor = getattr(self, "comic_cover_prompt_editor", None)
        if editor is None:
            return
        editor.delete("1.0", END)
        editor.insert("1.0", self._suggest_comic_cover_visual())

    def _save_comic_cover_settings(self, *, silent: bool = False, persist: bool = True) -> None:
        if not hasattr(self, "comic_cover_title_var"):
            return
        cover = self._comic_cover_record()
        title = self.comic_cover_title_var.get().strip() or str(self.comic_project_var.get()).strip() or "漫画推文"
        character = self.comic_cover_character_var.get().strip()
        scene = self.comic_cover_scene_var.get().strip()
        if character.startswith("（不使用"):
            character = ""
        if scene.startswith("（不使用"):
            scene = ""
        prompt = str(cover.get("prompt", ""))
        editor = getattr(self, "comic_cover_prompt_editor", None)
        if editor is not None:
            try:
                if editor.winfo_exists():
                    prompt = editor.get("1.0", "end-1c").strip()
            except TclError:
                pass
        cover.update({"title": title, "character": character, "scene": scene, "prompt": prompt})
        if persist:
            self.store.save(self.state)
        self._refresh_comic_cover_widgets()
        if not silent and getattr(self, "comic_status", None):
            self.comic_status.configure(text="封面设置已保存。", fg=ACCENT_DARK)

    def open_comic_cover_editor(self) -> None:
        existing = getattr(self, "comic_cover_dialog", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except TclError:
                pass
        cover = self._comic_cover_record()
        self.comic_cover_title_var.set(str(cover.get("title", "")).strip() or self.comic_project_var.get().strip() or "漫画推文")
        self.comic_cover_character_var.set(str(cover.get("character", "")).strip() or "（不使用人物参考）")
        self.comic_cover_scene_var.set(str(cover.get("scene", "")).strip() or "（不使用场景参考）")

        dialog = Toplevel(self.root)
        self.comic_cover_dialog = dialog
        dialog.title("项目封面制作")
        dialog.geometry("1040x760")
        dialog.minsize(900, 680)
        dialog.configure(bg=BG)
        dialog.transient(self.root)

        header = Frame(dialog, bg=SIDEBAR, padx=24, pady=17)
        header.pack(fill=X)
        Label(header, text="COMIC COVER · 项目封面", bg=SIDEBAR, fg=ACCENT, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        Label(header, text="使用已确认人物与场景参考图生成统一画风封面", bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w", pady=(3, 0))

        body = Frame(dialog, bg=BG, padx=22, pady=20)
        body.pack(fill=BOTH, expand=True)
        preview_outer = self._card(body, bg=COMIC_INSET, padx=14, pady=14)
        preview_outer.pack(side=LEFT, fill=Y, padx=(0, 16))
        preview = preview_outer.winfo_children()[0]
        Label(preview, text="四张封面预览", bg=COMIC_INSET, fg=INK, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        gallery = Frame(preview, bg=COMIC_INSET)
        gallery.pack()
        self.comic_cover_preview_canvases = {}
        for plan_index, (cover_aspect, cover_ordinal) in enumerate(COMIC_COVER_OUTPUT_PLAN):
            cell = Frame(gallery, bg=COMIC_INSET)
            cell.grid(row=plan_index // 2, column=plan_index % 2, padx=(0 if plan_index % 2 == 0 else 5, 5 if plan_index % 2 == 0 else 0), pady=(0, 7))
            Label(cell, text=f"{cover_aspect} · 第 {cover_ordinal} 张", bg=COMIC_INSET, fg=MUTED, font=("Microsoft YaHei UI", 8, "bold")).pack(anchor="w", pady=(0, 3))
            canvas_widget = Canvas(cell, width=170, height=205, bg=SURFACE_ALT, highlightthickness=0, borderwidth=0, cursor="hand2")
            canvas_widget.pack()
            canvas_widget.bind(
                "<Button-1>",
                lambda _event, aspect=cover_aspect, ordinal=cover_ordinal: self.preview_comic_cover(aspect, ordinal),
            )
            self.comic_cover_preview_canvases[(cover_aspect, cover_ordinal)] = canvas_widget
        self.comic_cover_preview_canvas = self.comic_cover_preview_canvases.get(COMIC_COVER_OUTPUT_PLAN[0])
        self.comic_cover_dialog_status_label = Label(preview, text="", bg=COMIC_INSET, fg=SIDEBAR_MUTED, anchor="w", wraplength=360, font=("Microsoft YaHei UI", 8))
        self.comic_cover_dialog_status_label.pack(fill=X, pady=(10, 0))
        self._button(preview, "放大查看第一张封面", self.preview_comic_cover, kind="ghost").pack(fill=X, pady=(9, 0))

        editor_outer = self._card(body, padx=20, pady=18)
        editor_outer.pack(side=LEFT, fill=BOTH, expand=True)
        editor = editor_outer.winfo_children()[0]
        self._field_label(editor, "封面标题（会绘制在图片中）").pack(anchor="w")
        self._entry(editor, self.comic_cover_title_var).pack(fill=X, ipady=6, pady=(5, 10))

        selectors = Frame(editor, bg=SURFACE)
        selectors.pack(fill=X, pady=(0, 10))
        selectors.grid_columnconfigure(0, weight=1)
        selectors.grid_columnconfigure(1, weight=1)
        character_panel = Frame(selectors, bg=SURFACE)
        character_panel.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        scene_panel = Frame(selectors, bg=SURFACE)
        scene_panel.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._field_label(character_panel, "主要人物参考（直接点击选择）").pack(anchor="w")
        character_values = ["（不使用人物参考）"] + [
            str(item.get("name", "")).strip() for item in self.state["comic"].get("characters", []) if str(item.get("name", "")).strip()
        ]
        current_character = self.comic_cover_character_var.get()
        if current_character not in character_values:
            character_values.append(current_character)
        character_shell, character_host = self._rounded_widget_shell(character_panel, bg=COMIC_INSET, fixed_height=112)
        character_shell.pack(fill=X, pady=(5, 0))
        character_list = Listbox(
            character_host,
            selectmode="browse",
            exportselection=False,
            bg=COMIC_INSET,
            fg=INK,
            selectbackground=COMIC_MINT,
            selectforeground=ACCENT_DARK,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=("Microsoft YaHei UI", 8),
        )
        for item_index, name in enumerate(character_values):
            character_list.insert(END, name)
            if name == current_character:
                character_list.selection_set(item_index)
                character_list.activate(item_index)
                character_list.see(item_index)
        self._pack_vertical_scroller(character_host, character_list)

        def select_cover_character(_event=None) -> None:
            selected = character_list.curselection()
            if selected:
                self.comic_cover_character_var.set(str(character_list.get(selected[0])))

        character_list.bind("<<ListboxSelect>>", select_cover_character, add="+")
        self._field_label(scene_panel, "固定场景参考（直接点击选择）").pack(anchor="w")
        scene_values = ["（不使用场景参考）"] + [
            str(item.get("name", "")).strip() for item in self.state["comic"].get("scenes", []) if str(item.get("name", "")).strip()
        ]
        current_scene = self.comic_cover_scene_var.get()
        if current_scene not in scene_values:
            scene_values.append(current_scene)
        scene_shell, scene_host = self._rounded_widget_shell(scene_panel, bg=COMIC_INSET, fixed_height=112)
        scene_shell.pack(fill=X, pady=(5, 0))
        scene_list = Listbox(
            scene_host,
            selectmode="browse",
            exportselection=False,
            bg=COMIC_INSET,
            fg=INK,
            selectbackground=COMIC_MINT,
            selectforeground=ACCENT_DARK,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=("Microsoft YaHei UI", 8),
        )
        for item_index, name in enumerate(scene_values):
            scene_list.insert(END, name)
            if name == current_scene:
                scene_list.selection_set(item_index)
                scene_list.activate(item_index)
                scene_list.see(item_index)
        self._pack_vertical_scroller(scene_host, scene_list)

        def select_cover_scene(_event=None) -> None:
            selected = scene_list.curselection()
            if selected:
                self.comic_cover_scene_var.set(str(scene_list.get(selected[0])))

        scene_list.bind("<<ListboxSelect>>", select_cover_scene, add="+")

        prompt_header = Frame(editor, bg=SURFACE)
        prompt_header.pack(fill=X, pady=(2, 5))
        self._field_label(prompt_header, "封面画面提示词（描述表情、动作与氛围）").pack(side=LEFT)
        self._button(prompt_header, "自动填写", self._fill_comic_cover_prompt, kind="ghost").pack(side=RIGHT)
        prompt_shell, prompt_host = self._rounded_widget_shell(editor, bg="#F3F8F7", fixed_height=190)
        prompt_shell.pack(fill=X)
        self.comic_cover_prompt_editor = Text(prompt_host, height=7, wrap="word", undo=True, bg="#F3F8F7", fg=INK, relief="flat", padx=10, pady=9, font=("Microsoft YaHei UI", 9))
        self._pack_vertical_scroller(prompt_host, self.comic_cover_prompt_editor)
        self.comic_cover_prompt_editor.insert("1.0", str(cover.get("prompt", "")))

        model_id = SHOT_IMAGE_MODEL_IDS.get(self.comic_shot_model_var.get(), SEEDREAM_LITE_MODEL)
        model_label = SHOT_IMAGE_MODEL_LABELS.get(model_id, model_id)
        Label(
            editor,
            text=f"固定生成 3:4 两张 + 4:3 两张，沿用批量出图设置：{model_label} · {self.comic_resolution_var.get()}。人物和场景必须已有确认参考图。",
            bg=SURFACE,
            fg=MUTED,
            wraplength=520,
            justify=LEFT,
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(10, 12))
        actions = Frame(editor, bg=SURFACE)
        actions.pack(fill=X, side="bottom")
        self._button(actions, "保存设置", lambda: self._save_comic_cover_settings(), kind="ghost").pack(side=LEFT)
        self._button(actions, "生成 / 重绘四张封面", self.generate_comic_cover, kind="accent").pack(side=RIGHT)

        def close_dialog() -> None:
            self._save_comic_cover_settings(silent=True)
            try:
                dialog.destroy()
            finally:
                self.comic_cover_dialog = None
                self.comic_cover_preview_canvas = None
                self.comic_cover_preview_canvases = {}
                self.comic_cover_prompt_editor = None
                self.comic_cover_dialog_status_label = None

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        self._refresh_comic_cover_widgets()

    def preview_comic_cover(self, aspect: str | None = None, ordinal: int | None = None) -> None:
        cover = self._comic_cover_record()
        images = [item for item in cover.get("images", []) if isinstance(item, dict)]
        selected = None
        if aspect is not None and ordinal is not None:
            selected = next(
                (
                    item
                    for item in images
                    if str(item.get("aspect", "")) == aspect
                    and int(item.get("ordinal", 0) or 0) == ordinal
                ),
                None,
            )
        else:
            for planned_aspect, planned_ordinal in COMIC_COVER_OUTPUT_PLAN:
                selected = next(
                    (
                        item
                        for item in images
                        if str(item.get("aspect", "")) == planned_aspect
                        and int(item.get("ordinal", 0) or 0) == planned_ordinal
                        and Path(str(item.get("local_path", ""))).is_file()
                    ),
                    None,
                )
                if selected is not None:
                    break
        local_path = str(selected.get("local_path", "")) if selected else str(cover.get("local_path", ""))
        preview_aspect = str(selected.get("aspect", "")) if selected else ""
        preview_ordinal = int(selected.get("ordinal", 0) or 0) if selected else 0
        suffix = f" · {preview_aspect} 第 {preview_ordinal} 张" if preview_aspect and preview_ordinal else ""
        self._show_comic_image(local_path, f"项目封面 · {cover.get('title', '漫画推文')}{suffix}")

    def generate_comic_cover(self) -> None:
        if self.is_busy:
            messagebox.showinfo("任务进行中", "请等待当前生成任务完成后再制作封面。")
            return
        self._sync_comic_state()
        cover = self._comic_cover_record()
        comic = self.state["comic"]
        character_name = str(cover.get("character", "")).strip()
        scene_name = str(cover.get("scene", "")).strip()
        character = next((item for item in comic.get("characters", []) if str(item.get("name", "")).strip() == character_name), None)
        scene = next((item for item in comic.get("scenes", []) if str(item.get("name", "")).strip() == scene_name), None)
        if character_name and (character is None or not has_local_reference(character)):
            messagebox.showwarning("人物参考图不可用", f"“{character_name}”还没有已确认的人物参考图，请先完成角色定妆。")
            return
        if scene_name and (scene is None or not has_local_reference(scene)):
            messagebox.showwarning("场景参考图不可用", f"“{scene_name}”还没有已确认的场景参考图，请先完成场景定景。")
            return
        model_id = str(comic.get("shot_image_model", SEEDREAM_LITE_MODEL))
        try:
            client = self._seedream_client(model_id)
        except ComicEngineError as exc:
            messagebox.showwarning("需要 ARK API Key", str(exc))
            return
        title = str(cover.get("title", "")).strip() or str(comic.get("project_name", "漫画推文"))
        visual_prompt = str(cover.get("prompt", "")).strip() or self._suggest_comic_cover_visual()
        references = character_reference_data([character] if character else []) + scene_reference_data(scene)
        cover_dir = self._comic_output_dir() / "covers"
        resolution = str(comic.get("resolution", "2K"))
        optimize_mode = str(comic.get("optimize_mode", "standard"))
        composition_prompts = {
            1: "构图方案一：人物正面或三分之二侧面，以中近景突出表情和动作，情绪冲突明确，画面下半部为标题保留清晰层次",
            2: "构图方案二：人物略微侧身或回头，以中近景制造悬念，表情和动作与方案一明显不同，画面下半部为标题保留清晰层次",
        }
        prompts = {
            (aspect, ordinal): build_cover_prompt(
                title,
                f"{visual_prompt}。{composition_prompts[ordinal]}",
                art_style=str(comic.get("art_style", "")),
                aspect=aspect,
                character_name=character_name,
                scene_name=scene_name,
            )
            for aspect, ordinal in COMIC_COVER_OUTPUT_PLAN
        }
        for aspect, ordinal in COMIC_COVER_OUTPUT_PLAN:
            item = self._comic_cover_image_record(aspect, ordinal)
            item.update({"status": "等待生成", "progress": "0%", "error": "", "task_id": "", "final_prompt": prompts[(aspect, ordinal)]})
        cover.update({"title": title, "prompt": visual_prompt, "status": "提交中", "progress": "0%", "error": ""})
        self.store.save(self.state)
        self.is_busy = True
        self.comic_progress["value"] = 0
        self.comic_status.configure(text=f"正在生成四张项目封面：{title}", fg=ACCENT_DARK)
        self._refresh_comic_cover_widgets()

        def worker() -> None:
            completed = 0
            failed = 0
            total = len(COMIC_COVER_OUTPUT_PLAN)
            for position, (aspect, ordinal) in enumerate(COMIC_COVER_OUTPUT_PLAN, start=1):
                final_prompt = prompts[(aspect, ordinal)]
                destination = cover_dir / f"cover_{aspect.replace(':', 'x')}_{ordinal:02d}.png"
                self.bus.put(("comic_cover_item_started", (position, total, aspect, ordinal, final_prompt)))
                try:
                    def progress(task: dict[str, object], *, item_position: int = position, item_aspect: str = aspect, item_ordinal: int = ordinal) -> None:
                        self.bus.put(
                            (
                                "comic_cover_progress",
                                (
                                    item_position,
                                    total,
                                    item_aspect,
                                    item_ordinal,
                                    str(task.get("progress", "")),
                                    str(task.get("id", "")),
                                ),
                            )
                        )

                    result = client.generate_image(
                        final_prompt,
                        images=references or None,
                        size=resolution,
                        aspect=aspect,
                        optimize_mode=optimize_mode,
                        progress=progress,
                    )
                    client.download_image(str(result["imageUrl"]), destination)
                    completed += 1
                    self.bus.put(("comic_cover_item_done", (aspect, ordinal, result, str(destination), final_prompt)))
                except Exception as exc:
                    failed += 1
                    self.bus.put(("comic_cover_item_error", (aspect, ordinal, str(exc))))
            self.bus.put(("comic_cover_batch_done", (completed, failed, total)))

        threading.Thread(target=worker, daemon=True).start()

    def _select_comic_shot_model(self, label: str) -> None:
        if label not in SHOT_IMAGE_MODEL_IDS:
            return
        self.comic_shot_model_var.set(label)
        model_id = SHOT_IMAGE_MODEL_IDS[label]
        self.state["comic"]["shot_image_model"] = model_id
        supported = SHOT_IMAGE_MODEL_RESOLUTIONS[model_id]
        if self.comic_resolution_var.get().strip().upper() not in supported:
            self.comic_resolution_var.set(supported[0])
            self.state["comic"]["resolution"] = supported[0]
        self.store.save(self.state)
        self._refresh_comic_resolution_controls()
        if self.comic_generation_detail_label:
            self.comic_generation_detail_label.configure(
                text=f"已切换为 {label}。未开通时请到火山方舟“开通管理 → 视觉模型”启用。",
                fg=ACCENT,
            )

    def _supported_comic_resolutions(self) -> tuple[str, ...]:
        model_id = SHOT_IMAGE_MODEL_IDS.get(self.comic_shot_model_var.get(), SEEDREAM_LITE_MODEL)
        return SHOT_IMAGE_MODEL_RESOLUTIONS.get(model_id, ("2K", "3K"))

    def _refresh_comic_resolution_controls(self) -> None:
        supported = self._supported_comic_resolutions()
        selected = self.comic_resolution_var.get().strip().upper()
        if selected not in supported:
            selected = supported[0]
            self.comic_resolution_var.set(selected)
        buttons = getattr(self, "comic_resolution_buttons", {})
        for resolution, button in buttons.items():
            button.pack_forget()
            if resolution not in supported:
                continue
            button.normal_bg = ACCENT if resolution == selected else "#314C5B"
            button.active_bg = "#379B8B" if resolution == selected else "#3C5969"
            button.fg = SIDEBAR if resolution == selected else "white"
            button.pack(side=LEFT, fill=X, expand=True, padx=(0, 5) if resolution != supported[-1] else 0)
            button._draw()
        hint = getattr(self, "comic_resolution_hint_var", None)
        if hint is not None:
            model_name = "Lite" if self.comic_shot_model_var.get() == SHOT_IMAGE_MODEL_OPTIONS[0] else "Pro"
            selected_label = selected
            if model_name == "Pro" and selected == "1K":
                aspect_var = getattr(self, "comic_aspect_var", None)
                aspect = aspect_var.get().strip() if aspect_var is not None else "9:16"
                actual_size = SEEDREAM_PRO_1K_SIZES.get(aspect, SEEDREAM_PRO_1K_SIZES["1:1"])
                selected_label = f"1K（{actual_size.replace('x', '×')}）"
            hint.set(f"当前 {selected_label} · {model_name} 可选 {' / '.join(supported)}")

    def _select_comic_resolution(self, resolution: str) -> None:
        value = resolution.strip().upper()
        supported = self._supported_comic_resolutions()
        if value not in supported:
            messagebox.showinfo("当前模型不支持", f"当前模型只能使用 {'、'.join(supported)} 分辨率。")
            return
        self.comic_resolution_var.set(value)
        self.state["comic"]["resolution"] = value
        self.store.save(self.state)
        self._refresh_comic_resolution_controls()
        if self.comic_generation_detail_label:
            self.comic_generation_detail_label.configure(text=f"输出分辨率已切换为 {value}，后续生成与重绘立即生效。", fg=ACCENT)

    def _build_comic_video_step(self, parent) -> None:
        page, canvas = self._scrollable_content(parent)
        outer = self._card(page, padx=24, pady=20)
        outer.pack(fill=X, padx=(0, 8), pady=(0, 2))
        content = outer.winfo_children()[0]
        self._comic_section_title(content, "06", "静态漫剪映草稿（可选）", "按抖音漫画推文节奏写入可编辑图片、纵向关键帧、配音和黄色描边字幕。")

        work = Frame(content, bg=SURFACE)
        work.pack(fill=BOTH, expand=True)
        work.grid_columnconfigure(0, weight=3)
        work.grid_columnconfigure(1, weight=2)
        work.grid_rowconfigure(0, weight=1)

        inputs_outer = self._card(work, bg=COMIC_INSET, padx=18, pady=16)
        inputs_outer.set_fixed_height(650)
        inputs_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        inputs = inputs_outer.winfo_children()[0]
        Label(inputs, text="成片素材", bg=COMIC_INSET, fg=INK, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        Label(inputs, text="音频决定总时长；导入 SRT 后，镜头切换会尽量对齐字幕段落并把字幕烧录进画面。", bg=COMIC_INSET, fg=MUTED, wraplength=560, justify=LEFT, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(4, 14))

        audio_outer = self._card(inputs, bg=SURFACE, padx=16, pady=14)
        audio_outer.set_fixed_height(150)
        audio_outer.pack(fill=X, pady=(0, 12))
        audio_card = audio_outer.winfo_children()[0]
        Label(audio_card, text="01  配音音频（必需）", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.comic_video_audio_label = Label(audio_card, text="", bg=SURFACE, fg=MUTED, anchor="w", wraplength=500, font=("Microsoft YaHei UI", 8))
        self.comic_video_audio_label.pack(fill=X, pady=(5, 8))
        self._button(audio_card, "选择并导入配音音频", self.choose_comic_audio, kind="accent").pack(fill=X)

        subtitle_outer = self._card(inputs, bg=COMIC_MINT, padx=18, pady=18)
        subtitle_outer.set_fixed_height(190)
        subtitle_outer.pack(fill=X, pady=(0, 12))
        subtitle_card = subtitle_outer.winfo_children()[0]
        Label(subtitle_card, text="02  SRT 字幕（推荐）", bg=COMIC_MINT, fg=INK, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        Label(subtitle_card, text="导入后会按字幕时间轴匹配分镜图片，并写入剪映字幕轨道。", bg=COMIC_MINT, fg=MUTED, justify=LEFT, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(5, 4))
        self.comic_video_subtitle_label = Label(subtitle_card, text="", bg=COMIC_MINT, fg=INK, anchor="w", wraplength=500, font=("Microsoft YaHei UI", 9, "bold"))
        self.comic_video_subtitle_label.pack(fill=X, pady=(6, 12))
        self._button(subtitle_card, "选择并导入 SRT 字幕文件", self.choose_comic_subtitles, kind="primary").pack(fill=X)

        motion_outer = self._card(inputs, bg=SURFACE, padx=16, pady=14)
        motion_outer.set_fixed_height(145)
        motion_outer.pack(fill=X)
        motion_card = motion_outer.winfo_children()[0]
        Label(motion_card, text="03  成片画面效果", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        RoundedCombobox(motion_card, textvariable=self.comic_motion_var, values=list(MOTION_MODE_OPTIONS), state="readonly").pack(fill=X, pady=(8, 0))
        Label(motion_card, text="推荐项会交替上下缓慢推移、按字幕节奏硬切；所有画风共用，不做人物 AI 动画。", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(7, 0))

        result_outer = self._card(work, bg=SIDEBAR, padx=20, pady=20)
        result_outer.set_fixed_height(650)
        result_outer.grid(row=0, column=1, sticky="nsew")
        result = result_outer.winfo_children()[0]
        Label(result, text="JY DRAFT OUTPUT", bg=SIDEBAR, fg=ACCENT, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        Label(result, text="可编辑剪映草稿", bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", pady=(4, 12))
        self.comic_video_result_label = Label(result, text="", bg=COMIC_DARK_ALT, fg=SIDEBAR_MUTED, justify=LEFT, wraplength=250, padx=14, pady=14, font=("Microsoft YaHei UI", 8))
        self.comic_video_result_label.pack(fill=X)
        self.comic_draft_progress = ttk.Progressbar(result, mode="determinate", maximum=100, style="Studio.Horizontal.TProgressbar")
        self.comic_draft_progress.pack(fill=X, pady=(16, 5))
        self.comic_draft_status_label = Label(result, text="等待生成", bg=SIDEBAR, fg=SIDEBAR_MUTED, justify=LEFT, wraplength=250, font=("Microsoft YaHei UI", 8))
        self.comic_draft_status_label.pack(fill=X)
        self._button(result, "生成剪映草稿", self.generate_comic_draft, kind="accent").pack(fill=X, pady=(13, 0))
        self._button(result, "生成草稿并打开剪映", lambda: self.generate_comic_draft(open_after=True), kind="primary").pack(fill=X, pady=(8, 0))
        self._button(result, "打开剪映查看草稿", self.open_comic_draft_in_jianying, kind="ghost").pack(fill=X, pady=(8, 0))
        self._button(result, "打开草稿目录", self.open_comic_draft_directory, kind="ghost").pack(fill=X, pady=(8, 0))
        Label(result, text="生成要求：所有分镜已出图并导入音频。草稿内的图片、配音、字幕和关键帧均可继续编辑。", bg=SIDEBAR, fg=SIDEBAR_MUTED, justify=LEFT, wraplength=240, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(18, 0))

        footer = Frame(content, bg=SURFACE)
        footer.pack(fill=X, pady=(14, 0))
        self._button(footer, "← 批量出图", lambda: self._switch_comic_step(4), kind="ghost").pack(side=LEFT)
        self._refresh_comic_video_labels()
        self._bind_page_mousewheel(page, canvas)

    def edit_seedream_settings(self) -> None:
        dialog = Toplevel(self.root)
        dialog.title("Doubao Seedream 连接设置")
        dialog.geometry("590x475")
        dialog.resizable(False, False)
        dialog.configure(bg=BG)
        Label(dialog, text="Doubao Seedream 5.0", bg=BG, fg=INK, font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w", padx=24, pady=(22, 4))
        Label(dialog, text="角色和场景默认使用下方模型；分镜图片可在批量出图页单独选择 Lite 或 Pro。", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=24)
        card_outer = self._card(dialog, padx=18, pady=16)
        card_outer.pack(fill=X, padx=24, pady=18)
        card = card_outer.winfo_children()[0]
        base_url_var = StringVar(value=self.state["settings"].get("ark_base_url", SEEDREAM_BASE_URL))
        model_var = StringVar(value=self.state["settings"].get("ark_model", SEEDREAM_MODEL))
        self._field_label(card, "火山方舟 API 地址").pack(anchor="w", pady=(0, 4))
        self._entry(card, base_url_var).pack(fill=X, ipady=6)
        self._field_label(card, "角色/场景默认模型 ID").pack(anchor="w", pady=(12, 4))
        self._entry(card, model_var).pack(fill=X, ipady=6)
        self._field_label(card, "ARK API Key").pack(anchor="w", pady=(12, 4))
        key_entry = self._entry(card, self.ark_api_key)
        key_entry.configure(show="•")
        key_entry.pack(fill=X, ipady=6)
        ttk.Checkbutton(card, text="安全记住 ARK API Key", variable=self.remember_ark_api_key).pack(anchor="w", pady=(9, 0))
        self.comic_api_dialog_status = Label(card, text="", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 8))
        self.comic_api_dialog_status.pack(anchor="w", pady=(8, 0))

        def apply_connection() -> None:
            self.state["settings"]["ark_base_url"] = base_url_var.get().strip().rstrip("/") or SEEDREAM_BASE_URL
            self.state["settings"]["ark_model"] = model_var.get().strip() or SEEDREAM_MODEL
            self.save_comic_settings(silent=True)
            self._refresh_comic_overview()
            self.comic_api_dialog_status.configure(text="设置已保存", fg=ACCENT_DARK)

        def test_connection() -> None:
            apply_connection()
            self.test_seedream_connection()

        actions = Frame(dialog, bg=BG)
        actions.pack(fill=X, padx=24)
        self._button(actions, "保存", apply_connection, kind="primary").pack(side=RIGHT)
        self._button(actions, "验证 Key", test_connection, kind="ghost").pack(side=RIGHT, padx=(0, 8))

    def _comic_output_dir(self) -> Path:
        project_name = self.comic_project_var.get().strip() if hasattr(self, "comic_project_var") else self.state["comic"].get("project_name", "漫画推文")
        configured = self.comic_output_var.get().strip() if hasattr(self, "comic_output_var") else self.state["comic"].get("output_dir", "")
        if configured:
            return Path(configured)
        return self.store.base_dir / "comic_projects" / safe_filename(project_name, "漫画推文")

    def _shared_character_asset_dir(self) -> Path:
        return self.store.base_dir / "shared_assets" / "characters"

    @staticmethod
    def _delete_comic_asset_files(item: dict[str, object], kind: str) -> list[str]:
        errors: list[str] = []
        seen: set[Path] = set()
        for key in ("local_path", "candidate_path"):
            raw_path = str(item.get(key, "")).strip()
            if not raw_path:
                continue
            path = Path(raw_path)
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path.absolute()
            if resolved in seen:
                continue
            seen.add(resolved)
            allowed_name = "_reference" in path.stem or "_candidate" in path.stem
            if path.parent.name != kind or not allowed_name:
                errors.append(f"为安全起见未删除非标准资产路径：{path}")
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                errors.append(f"{path.name}：{exc}")
        return errors

    def _sync_comic_state(self) -> None:
        if not hasattr(self, "comic_project_var"):
            return
        self._save_current_comic_character()
        self._save_current_comic_scene()
        self.save_comic_shot_prompt(silent=True)
        self._save_comic_cover_settings(silent=True, persist=False)
        comic = self.state["comic"]
        comic["project_name"] = self.comic_project_var.get().strip() or "未命名漫画推文"
        comic["art_style"] = self.comic_style_var.get().strip() or COMIC_STYLE_PRESETS[0]
        next_aspect = self.comic_aspect_var.get().strip() or "9:16"
        character_names = {
            str(item.get("name", "")).strip()
            for item in comic.get("characters", [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        }
        for character in comic.get("characters", []):
            if not isinstance(character, dict):
                continue
            base_name = str(character.get("base_character", "")).strip()
            if base_name not in character_names or base_name == str(character.get("name", "")).strip():
                base_name = ""
                character["base_character"] = ""
            prompt = str(character.get("prompt", "")).strip()
            if not prompt:
                prompt = build_character_prompt(
                    str(character.get("name", "角色")),
                    str(character.get("description", "")),
                    comic["art_style"],
                    base_name,
                )
            character["prompt"] = (
                enforce_character_variant_prompt(
                    prompt,
                    base_name,
                    comic["art_style"],
                    str(character.get("description", "")),
                )
                if base_name
                else enforce_character_reference_prompt(prompt, comic["art_style"])
            )
        for scene in comic.get("scenes", []):
            if not isinstance(scene, dict):
                continue
            prompt = str(scene.get("prompt", "")).strip()
            if not prompt:
                prompt = build_scene_prompt(
                    str(scene.get("name", "场景")),
                    str(scene.get("description", "")),
                    comic["art_style"],
                    next_aspect,
                )
            scene["prompt"] = enforce_scene_reference_prompt(prompt, comic["art_style"], next_aspect)
        if self.current_comic_character_index is not None and self.comic_character_prompt_editor:
            index = self.current_comic_character_index
            if 0 <= index < len(comic.get("characters", [])):
                self.comic_character_prompt_editor.delete("1.0", END)
                self.comic_character_prompt_editor.insert("1.0", str(comic["characters"][index].get("prompt", "")))
        if self.current_comic_scene_index is not None and self.comic_scene_prompt_editor:
            index = self.current_comic_scene_index
            if 0 <= index < len(comic.get("scenes", [])):
                self.comic_scene_prompt_editor.delete("1.0", END)
                self.comic_scene_prompt_editor.insert("1.0", str(comic["scenes"][index].get("prompt", "")))
        if comic.get("aspect") != next_aspect:
            comic["video_output_path"] = ""
            self.comic_video_output_var.set("")
            comic["jianying_draft_path"] = ""
            comic["jianying_draft_name"] = ""
            self.comic_draft_output_var.set("")
        comic["aspect"] = next_aspect
        shot_model_id = SHOT_IMAGE_MODEL_IDS.get(self.comic_shot_model_var.get(), SEEDREAM_LITE_MODEL)
        supported_resolutions = SHOT_IMAGE_MODEL_RESOLUTIONS.get(shot_model_id, ("2K", "3K"))
        resolution = self.comic_resolution_var.get().strip().upper()
        if resolution not in supported_resolutions:
            resolution = supported_resolutions[0]
            self.comic_resolution_var.set(resolution)
        comic["resolution"] = resolution
        comic["optimize_mode"] = "fast" if self.comic_optimize_var.get() == "极速模式" else "standard"
        comic["shot_image_model"] = shot_model_id
        comic["source_text"] = self.comic_source_editor.get("1.0", "end-1c") if self.comic_source_editor else comic.get("source_text", "")
        comic["output_dir"] = str(self._comic_output_dir())
        self.comic_output_var.set(comic["output_dir"])
        comic["audio_path"] = self.comic_audio_var.get().strip()
        comic["subtitles_path"] = self.comic_subtitles_var.get().strip()
        next_motion = normalize_motion_mode(self.comic_motion_var.get() or DOUYIN_COMIC_MOTION)
        if self.comic_motion_var.get() != next_motion:
            self.comic_motion_var.set(next_motion)
        if comic.get("motion_mode") != next_motion:
            comic["video_output_path"] = ""
            self.comic_video_output_var.set("")
            comic["jianying_draft_path"] = ""
            comic["jianying_draft_name"] = ""
            self.comic_draft_output_var.set("")
        comic["motion_mode"] = next_motion
        comic["video_output_path"] = self.comic_video_output_var.get().strip()
        comic["jianying_draft_path"] = self.comic_draft_output_var.get().strip()
        comic["updated_at"] = datetime.now().isoformat(timespec="seconds")
        shared = self.state.setdefault("shared_characters", [])
        if comic.get("characters") is not shared:
            merged_shared = self._merge_imported_comic_assets([dict(item) for item in shared], [dict(item) for item in comic.get("characters", [])])
            shared[:] = merged_shared
            comic["characters"] = shared
        for project in self.state.get("projects", []):
            if isinstance(project, dict):
                project["characters"] = shared
        settings = self.state["settings"]
        settings["remember_ark_api_key"] = self.remember_ark_api_key.get()
        settings.setdefault("ark_base_url", SEEDREAM_BASE_URL)
        settings.setdefault("ark_model", SEEDREAM_MODEL)

    def save_comic_settings(self, silent: bool = False) -> None:
        self._sync_comic_state()
        error = ""
        if not silent:
            try:
                if self.remember_ark_api_key.get() and self.ark_api_key.get().strip():
                    save_api_key("ark", self.ark_api_key.get().strip())
                else:
                    delete_api_key("ark")
            except SecretStoreError as exc:
                error = str(exc)
        self._cancel_scheduled_state_save()
        self.store.save(self.state)
        self._refresh_comic_overview()
        if silent:
            return
        if error:
            messagebox.showwarning("项目已保存", f"漫画项目已保存，但 ARK API Key 未能安全保存：\n{error}")
        else:
            messagebox.showinfo("已保存", "漫画项目、角色与场景设定和 Seedream 配置已保存。")

    def choose_comic_output_dir(self) -> None:
        path = filedialog.askdirectory(title="选择漫画图片保存目录")
        if path:
            self.comic_output_var.set(path)
            self.state["comic"]["output_dir"] = path
            self.store.save(self.state)
            if hasattr(self, "comic_output_hint") and self.comic_output_hint.winfo_exists():
                self.comic_output_hint.configure(text=Path(path).name)

    def export_comic_assets(self) -> None:
        self._sync_comic_state()
        comic = self.state["comic"]
        characters = [dict(item) for item in comic.get("characters", [])]
        scenes = [dict(item) for item in comic.get("scenes", [])]
        if not characters and not scenes:
            messagebox.showinfo("没有可导出的资产", "请先建立角色或场景资产。")
            return
        initial_name = f"{safe_filename(str(comic.get('project_name', '漫画项目')))}_人物场景资产包.zip"
        destination = filedialog.asksaveasfilename(
            title="导出人物与场景资产包",
            defaultextension=".zip",
            initialfile=initial_name,
            filetypes=[("漫画资产包", "*.zip"), ("所有文件", "*.*")],
        )
        if not destination:
            return
        try:
            summary = export_comic_asset_pack(
                destination,
                characters=characters,
                scenes=scenes,
                metadata={
                    "project_name": comic.get("project_name", ""),
                    "art_style": comic.get("art_style", ""),
                    "aspect": comic.get("aspect", ""),
                    "app_version": APP_VERSION,
                },
            )
        except ComicEngineError as exc:
            messagebox.showerror("资产包导出失败", str(exc))
            return
        missing = int(summary["characters"]) + int(summary["scenes"]) - int(summary["references"])
        note = f"已导出 {summary['characters']} 个角色、{summary['scenes']} 个场景和 {summary['references']} 张本地固定参考图。"
        if missing:
            note += f"\n\n其中 {missing} 个资产尚无本地固定参考图，仅导出了名称、描述和提示词。"
        messagebox.showinfo("资产包已导出", f"{note}\n\n文件：{destination}")
        self.comic_status.configure(text=f"人物与场景资产包已保存：{Path(destination).name}", fg=ACCENT_DARK)

    @staticmethod
    def _merge_imported_comic_assets(existing: list[dict[str, object]], imported: list[dict[str, object]]) -> list[dict[str, object]]:
        result = [dict(item) for item in existing]
        positions = {str(item.get("name", "")).strip(): index for index, item in enumerate(result) if str(item.get("name", "")).strip()}
        for incoming in imported:
            record = dict(incoming)
            name = str(record.get("name", "")).strip()
            if name in positions:
                old = result[positions[name]]
                if not has_local_reference(record) and has_local_reference(old):
                    for key in ("task_id", "image_url", "local_path", "candidate_path", "candidate_image_url", "status"):
                        record[key] = old.get(key, "")
                result[positions[name]] = record
            else:
                positions[name] = len(result)
                result.append(record)
        return result

    def import_comic_assets(self) -> None:
        self._sync_comic_state()
        source = filedialog.askopenfilename(
            title="导入人物与场景资产包",
            filetypes=[("漫画资产包", "*.zip"), ("所有文件", "*.*")],
        )
        if not source:
            return
        comic = self.state["comic"]
        if (comic.get("characters") or comic.get("scenes")) and not messagebox.askyesno(
            "合并人物与场景资产",
            "资产包将合并到当前项目；同名资产会更新为导入版本，原本的本地图片文件不会被自动删除。是否继续？",
        ):
            return
        try:
            imported = import_comic_asset_pack(source, self._comic_output_dir())
        except ComicEngineError as exc:
            messagebox.showerror("资产包导入失败", str(exc))
            return
        imported_characters = [dict(item) for item in imported.get("characters", [])]
        imported_scenes = [dict(item) for item in imported.get("scenes", [])]
        merged_characters = self._merge_imported_comic_assets([dict(item) for item in self.state.get("shared_characters", [])], imported_characters)
        shared = self.state.setdefault("shared_characters", [])
        shared[:] = merged_characters
        comic["characters"] = shared
        for project in self.state.get("projects", []):
            if isinstance(project, dict):
                project["characters"] = shared
        comic["scenes"] = self._merge_imported_comic_assets([dict(item) for item in comic.get("scenes", [])], imported_scenes)
        self.store.save(self.state)
        self._refresh_comic_character_list(0 if comic["characters"] else None)
        self._refresh_comic_scene_list(0 if comic["scenes"] else None)
        self._refresh_comic_shot_tree(self.current_comic_shot_index)
        self._refresh_comic_overview()
        local_references = sum(1 for item in imported_characters + imported_scenes if has_local_reference(item))
        messagebox.showinfo(
            "资产包导入完成",
            f"已导入并保存到当前电脑：\n{len(imported_characters)} 个角色\n{len(imported_scenes)} 个场景\n{local_references} 张本地固定参考图",
        )
        self.comic_status.configure(text=f"资产包导入完成：{Path(source).name}", fg=ACCENT_DARK)

    def import_comic_novel(self) -> None:
        path = filedialog.askopenfilename(title="导入小说", filetypes=[("文本文档", "*.txt *.md *.docx"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            content = read_document(path)
        except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
            messagebox.showerror("导入失败", f"无法读取文件：{exc}")
            return
        comic = self.state["comic"]
        comic["source_path"] = path
        comic["source_text"] = content
        self.comic_source_editor.delete("1.0", END)
        self.comic_source_editor.insert("1.0", content)
        self._sync_comic_state()
        self.store.save(self.state)
        self._refresh_comic_overview()
        self.comic_status.configure(text=f"已导入 {len(content)} 字，可进行 AI 分析或本地拆分。", fg=ACCENT_DARK)

    def _schedule_comic_asset_autosave(self, kind: str) -> None:
        if self._loading_comic_asset_editor:
            return
        if self.comic_asset_autosave_after_id:
            try:
                self.root.after_cancel(self.comic_asset_autosave_after_id)
            except TclError:
                pass

        def commit() -> None:
            self.comic_asset_autosave_after_id = None
            if kind == "character":
                self._save_current_comic_character()
                self._update_comic_character_list_row(self.current_comic_character_index)
            elif kind == "scene":
                self._save_current_comic_scene()
                self._update_comic_scene_list_row(self.current_comic_scene_index)
            self.store.save(self.state)
            self._refresh_comic_shot_binding_controls()

        self.comic_asset_autosave_after_id = self.root.after(450, commit)

    def _cancel_scheduled_state_save(self) -> None:
        after_id = getattr(self, "state_save_after_id", None)
        if after_id:
            try:
                self.root.after_cancel(after_id)
            except TclError:
                pass
        self.state_save_after_id = None

    def _schedule_state_save(self, delay: int = 350) -> None:
        """Coalesce rapid inline edits into one atomic state write."""
        self._cancel_scheduled_state_save()

        def commit() -> None:
            self.state_save_after_id = None
            self.store.save(self.state)

        self.state_save_after_id = self.root.after(max(50, int(delay)), commit)

    @staticmethod
    def _comic_character_list_text(item: dict[str, object]) -> str:
        ready = "✓" if has_local_reference(item) else ("?" if Path(str(item.get("candidate_path", ""))).is_file() else "·")
        base_name = str(item.get("base_character", "")).strip()
        relation = f"  ↳ 换装自 {base_name}" if base_name else ""
        return f" {ready}  {item.get('name', '未命名角色')}{relation}  ·  {item.get('status', '未生成')}"

    def _refresh_comic_character_base_choices(self, index: int | None = None) -> None:
        if not self.comic_character_base_combo or self.comic_character_base_var is None:
            return
        characters = self.state["comic"].get("characters", [])
        current_name = ""
        if index is not None and 0 <= index < len(characters):
            current_name = str(characters[index].get("name", "")).strip()
        names = [
            str(item.get("name", "")).strip()
            for item in characters
            if str(item.get("name", "")).strip() and str(item.get("name", "")).strip() != current_name
        ]
        values = [CHARACTER_BASE_NONE] + list(dict.fromkeys(names))
        self.comic_character_base_combo.configure(values=values)
        if self.comic_character_base_var.get() not in values:
            self.comic_character_base_var.set(CHARACTER_BASE_NONE)

    def _on_comic_character_base_selected(self, _event=None) -> None:
        if self._loading_comic_asset_editor:
            return
        index = self.current_comic_character_index
        if index is None:
            return
        self._save_current_comic_character()
        self._load_comic_character(index)
        self.store.save(self.state)
        self._update_comic_character_list_row(index)

    def _update_comic_character_list_row(self, index: int | None) -> None:
        characters = self.state["comic"].get("characters", [])
        if not self.comic_character_list or index is None or not (0 <= index < len(characters)):
            return
        self.comic_character_list.delete(index)
        self.comic_character_list.insert(index, self._comic_character_list_text(characters[index]))
        self.comic_character_list.selection_set(index)
        self.comic_character_list.activate(index)

    @staticmethod
    def _comic_scene_list_text(item: dict[str, object]) -> str:
        ready = "✓" if has_local_reference(item) else ("?" if Path(str(item.get("candidate_path", ""))).is_file() else "·")
        return f" {ready}  {item.get('name', '未命名场景')}  ·  {item.get('status', '未生成')}"

    def _update_comic_scene_list_row(self, index: int | None) -> None:
        scenes = self.state["comic"].get("scenes", [])
        if not self.comic_scene_list or index is None or not (0 <= index < len(scenes)):
            return
        self.comic_scene_list.delete(index)
        self.comic_scene_list.insert(index, self._comic_scene_list_text(scenes[index]))
        self.comic_scene_list.selection_set(index)
        self.comic_scene_list.activate(index)

    def import_comic_character_prompt(self) -> None:
        index = self.current_comic_character_index
        if index is None or not self.comic_character_prompt_editor:
            messagebox.showinfo("没有角色", "请先选择要导入提示词的角色。")
            return
        source = filedialog.askopenfilename(
            title="导入角色定妆提示词",
            filetypes=[("提示词文本", "*.txt *.md"), ("所有文件", "*.*")],
        )
        if not source:
            return
        try:
            prompt = read_document(source).strip()
        except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self.comic_character_prompt_editor.delete("1.0", END)
        self.comic_character_prompt_editor.insert("1.0", prompt)
        self._save_current_comic_character()
        self.store.save(self.state)
        self.comic_status.configure(text=f"已导入并保存角色提示词：{Path(source).name}", fg=ACCENT_DARK)

    def export_comic_character_prompt(self) -> None:
        self._save_current_comic_character()
        index = self.current_comic_character_index
        characters = self.state["comic"].get("characters", [])
        if index is None or not (0 <= index < len(characters)):
            messagebox.showinfo("没有角色", "请先选择要导出提示词的角色。")
            return
        character = characters[index]
        prompt = str(character.get("prompt", "")).strip()
        if not prompt:
            messagebox.showinfo("没有提示词", "当前角色的定妆提示词为空。")
            return
        destination = filedialog.asksaveasfilename(
            title="导出角色定妆提示词",
            defaultextension=".txt",
            initialfile=f"{safe_filename(str(character.get('name', '角色')))}_定妆提示词.txt",
            filetypes=[("TXT 文本", "*.txt"), ("所有文件", "*.*")],
        )
        if not destination:
            return
        try:
            Path(destination).write_text(prompt + "\n", encoding="utf-8-sig")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.store.save(self.state)
        messagebox.showinfo("导出完成", f"角色提示词已保存到：\n{destination}")

    def add_comic_character(self) -> None:
        self._save_current_comic_character()
        characters = self.state["comic"]["characters"]
        characters.append(default_character(f"角色 {len(characters) + 1}"))
        self._refresh_comic_character_list(len(characters) - 1)
        self.store.save(self.state)
        self._refresh_comic_overview()

    def delete_comic_character(self) -> None:
        index = self.current_comic_character_index
        characters = self.state["comic"]["characters"]
        if index is None or not (0 <= index < len(characters)):
            messagebox.showinfo("没有角色", "请先选择要删除的角色。")
            return
        name = str(characters[index].get("name", "该角色"))
        if not messagebox.askyesno("永久删除共享角色", f"确定删除共享角色“{name}”吗？\n\n该角色会从所有推文项目中移除，候选图和固定参考图也会永久删除。此操作无法撤销。"):
            return
        delete_errors = self._delete_comic_asset_files(characters[index], "characters")
        characters.pop(index)
        for character in characters:
            if str(character.get("base_character", "")).strip() == name:
                character["base_character"] = ""
        for project in self.state.get("projects", []):
            for shot in project.get("shots", []) if isinstance(project, dict) else []:
                shot["characters"] = [item for item in shot.get("characters", []) if item != name]
        self.current_comic_character_index = None
        self._refresh_comic_character_list(min(index, len(characters) - 1) if characters else None)
        self._refresh_comic_shot_tree(self.current_comic_shot_index)
        self.store.save(self.state)
        self._refresh_comic_overview()
        if delete_errors:
            messagebox.showwarning("部分文件未删除", "角色记录已删除，但以下本地文件被安全保留或删除失败：\n" + "\n".join(delete_errors))

    def _save_current_comic_character(self) -> None:
        index = self.current_comic_character_index
        characters = self.state["comic"]["characters"]
        if (
            index is None
            or not (0 <= index < len(characters))
            or not hasattr(self, "comic_character_name_var")
            or not self.comic_character_description_editor
            or not self.comic_character_prompt_editor
        ):
            return
        old_name = str(characters[index].get("name", ""))
        new_name = self.comic_character_name_var.get().strip() or f"角色 {index + 1}"
        characters[index]["name"] = new_name
        base_name = self.comic_character_base_var.get().strip() if self.comic_character_base_var is not None else ""
        valid_base_names = {
            str(item.get("name", "")).strip()
            for item_index, item in enumerate(characters)
            if item_index != index and str(item.get("name", "")).strip()
        }
        characters[index]["base_character"] = base_name if base_name in valid_base_names and base_name != CHARACTER_BASE_NONE else ""
        characters[index]["description"] = self.comic_character_description_editor.get("1.0", "end-1c").strip()
        characters[index]["prompt"] = self.comic_character_prompt_editor.get("1.0", "end-1c").strip()
        if old_name and old_name != new_name:
            for character in characters:
                if str(character.get("base_character", "")).strip() == old_name:
                    character["base_character"] = new_name
            replace_character_in_shots(self.state["comic"].get("shots", []), old_name, new_name)
            for project in self.state.get("projects", []):
                for shot in project.get("shots", []) if isinstance(project, dict) else []:
                    replace_character_in_shots([shot], old_name, new_name)
            self._refresh_comic_shot_tree(self.current_comic_shot_index)
        self._refresh_comic_character_base_choices(index)

    def _load_comic_character(self, index: int) -> None:
        characters = self.state["comic"]["characters"]
        if not (0 <= index < len(characters)):
            self.current_comic_character_index = None
            return
        self.current_comic_character_index = index
        character = characters[index]
        art_style = self.comic_style_var.get().strip() or str(self.state["comic"].get("art_style", ""))
        valid_base_names = {
            str(item.get("name", "")).strip()
            for item_index, item in enumerate(characters)
            if item_index != index and str(item.get("name", "")).strip()
        }
        base_name = str(character.get("base_character", "")).strip()
        if base_name not in valid_base_names:
            base_name = ""
            character["base_character"] = ""
        prompt = str(character.get("prompt", "")).strip()
        if not prompt:
            prompt = build_character_prompt(
                str(character.get("name", "角色")),
                str(character.get("description", "")),
                art_style,
                base_name,
            )
        character["prompt"] = (
            enforce_character_variant_prompt(
                prompt,
                base_name,
                art_style,
                str(character.get("description", "")),
            )
            if base_name
            else enforce_character_reference_prompt(prompt, art_style)
        )
        self._loading_comic_asset_editor = True
        try:
            self._refresh_comic_character_base_choices(index)
            if self.comic_character_base_var is not None:
                self.comic_character_base_var.set(base_name or CHARACTER_BASE_NONE)
            self.comic_character_name_var.set(str(character.get("name", "")))
            self.comic_character_description_editor.delete("1.0", END)
            self.comic_character_description_editor.insert("1.0", str(character.get("description", "")))
            self.comic_character_prompt_editor.delete("1.0", END)
            self.comic_character_prompt_editor.insert("1.0", str(character.get("prompt", "")))
        finally:
            self._loading_comic_asset_editor = False
        local = str(character.get("local_path", "")) if has_local_reference(character) else ""
        candidate_path = str(character.get("candidate_path", ""))
        candidate = candidate_path if candidate_path and Path(candidate_path).is_file() else ""
        status = str(character.get("status", "未生成"))
        details = [status]
        if base_name:
            details.append(f"换装本体：{base_name}")
        if candidate:
            details.append(f"候选：{Path(candidate).name}")
        if local:
            details.append(f"已确认：{Path(local).name}")
        self.comic_character_status.configure(text=" · ".join(details), fg=ACCENT_DARK if local else (WARM if candidate else MUTED))
        confirmed = bool(local and "已确认" in status)
        preview_path = local if confirmed else (candidate or local)
        preview_kind = "已确认角色参考图" if confirmed else ("候选定妆图 · 待确认" if candidate else "角色参考图")
        if self.comic_character_preview_title:
            self.comic_character_preview_title.configure(text=f"{preview_kind}\n{character.get('name', '')}", fg=ACCENT_DARK if confirmed else (WARM if candidate else INK))
        self._render_local_image(self.comic_character_preview_canvas, preview_path, placeholder="尚未生成角色参考图", max_size=(250, 190))

    def _refresh_comic_character_list(self, selected: int | None = None) -> None:
        if not self.comic_character_list:
            return
        self.comic_character_list.delete(0, END)
        for item in self.state["comic"]["characters"]:
            self.comic_character_list.insert(END, self._comic_character_list_text(item))
        if selected is not None and self.state["comic"]["characters"]:
            selected = min(max(selected, 0), len(self.state["comic"]["characters"]) - 1)
            self.comic_character_list.selection_set(selected)
            self.comic_character_list.activate(selected)
            self._load_comic_character(selected)
        elif not self.state["comic"]["characters"]:
            self.current_comic_character_index = None
            self._loading_comic_asset_editor = True
            if self.comic_character_base_var is not None:
                self.comic_character_base_var.set(CHARACTER_BASE_NONE)
            self._loading_comic_asset_editor = False
            self._refresh_comic_character_base_choices(None)
            self.comic_character_name_var.set("")
            self.comic_character_description_editor.delete("1.0", END)
            self.comic_character_prompt_editor.delete("1.0", END)
            self._render_local_image(self.comic_character_preview_canvas, "", placeholder="尚未添加角色", max_size=(250, 190))

    def on_comic_character_select(self, _event=None) -> None:
        if not self.comic_character_list or not self.comic_character_list.curselection():
            return
        next_index = int(self.comic_character_list.curselection()[0])
        if self.current_comic_character_index == next_index:
            return
        self._save_current_comic_character()
        self._load_comic_character(next_index)

    def choose_comic_character_image(self) -> None:
        self._save_current_comic_character()
        index = self.current_comic_character_index
        if index is None:
            messagebox.showinfo("没有角色", "请先添加或选择一个角色。")
            return
        source = filedialog.askopenfilename(title="选择角色参考图", filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")])
        if not source:
            return
        if Path(source).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            messagebox.showerror("图片格式不支持", "固定参考图仅支持 PNG、JPG、JPEG 或 WebP。")
            return
        character = self.state["comic"]["characters"][index]
        destination = self._shared_character_asset_dir() / f"{safe_filename(str(character.get('name', '角色')))}_reference{Path(source).suffix.lower()}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        character.update({"local_path": str(destination), "image_url": "", "candidate_path": "", "candidate_image_url": "", "task_id": "", "status": "定妆已确认"})
        self.store.save(self.state)
        self._refresh_comic_character_list(index)
        self._refresh_comic_overview()

    def confirm_comic_character_candidate(self) -> None:
        self._save_current_comic_character()
        index = self.current_comic_character_index
        if index is None:
            messagebox.showinfo("没有角色", "请先选择角色。")
            return
        character = self.state["comic"]["characters"][index]
        candidate = Path(str(character.get("candidate_path", "")))
        if not candidate.is_file():
            messagebox.showinfo("没有候选定妆", "请先生成一张候选定妆图，再确认保留。")
            return
        reference = self._shared_character_asset_dir() / f"{safe_filename(str(character.get('name', '角色')))}_reference{candidate.suffix.lower()}"
        try:
            reference.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, reference)
        except OSError as exc:
            messagebox.showerror("确认失败", str(exc))
            return
        character.update(
            {
                "local_path": str(reference),
                "image_url": str(character.get("candidate_image_url", "")),
                "status": "定妆已确认",
            }
        )
        self.store.save(self.state)
        self._refresh_comic_character_list(index)
        self._refresh_comic_overview()
        self.comic_status.configure(text=f"已确认 {character.get('name', '角色')} 的参考图，后续镜头将自动使用它。", fg=ACCENT_DARK)

    def _seedream_client(self, model: str | None = None) -> DoubaoSeedreamClient:
        key = self.ark_api_key.get().strip()
        if not key:
            raise ComicEngineError("请先在漫画推文工作台填写火山方舟 ARK API Key。")
        settings = self.state["settings"]
        return DoubaoSeedreamClient(
            SeedreamConfig(
                key,
                settings.get("ark_base_url", SEEDREAM_BASE_URL),
                model or settings.get("ark_model", SEEDREAM_MODEL),
            )
        )

    def test_seedream_connection(self) -> None:
        if self.is_busy:
            return
        try:
            client = self._seedream_client()
        except ComicEngineError as exc:
            messagebox.showwarning("无法测试", str(exc))
            return
        self.is_busy = True
        self._set_comic_api_status("测试中…", ACCENT_DARK)

        def worker() -> None:
            try:
                client.check_connection()
                self.bus.put(("comic_api_ok", None))
            except Exception as exc:
                self.bus.put(("comic_api_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _set_comic_api_status(self, text: str, color: str) -> None:
        for name in ("comic_api_status", "comic_api_dialog_status"):
            label = getattr(self, name, None)
            try:
                if label and label.winfo_exists():
                    label.configure(text=text, fg=color)
            except TclError:
                pass

    def generate_comic_character(self) -> None:
        if self.is_busy:
            return
        self._sync_comic_state()
        index = self.current_comic_character_index
        if index is None:
            messagebox.showinfo("没有角色", "请先添加或选择一个角色。")
            return
        try:
            client = self._seedream_client()
        except ComicEngineError as exc:
            messagebox.showwarning("需要 ARK API Key", str(exc))
            return
        character = dict(self.state["comic"]["characters"][index])
        base_name = str(character.get("base_character", "")).strip()
        base_character = next(
            (
                item
                for item in self.state["comic"].get("characters", [])
                if str(item.get("name", "")).strip() == base_name
                and str(item.get("name", "")).strip() != str(character.get("name", "")).strip()
            ),
            None,
        )
        reference_images: list[str] = []
        if base_name:
            if base_character is None:
                messagebox.showwarning("换装关联已失效", f"找不到关联的本体角色“{base_name}”，请重新选择关联角色。")
                return
            if not has_local_reference(base_character):
                messagebox.showwarning(
                    "本体参考图尚未确认",
                    f"请先为本体角色“{base_name}”生成并确认参考图，再生成当前换装角色。",
                )
                return
            reference_images = character_reference_data([base_character])
            if not reference_images:
                messagebox.showwarning("本体参考图不可用", f"无法读取“{base_name}”的已确认参考图，请重新导入或确认。")
                return
        prompt = str(character.get("prompt", "")).strip()
        if not prompt:
            prompt = build_character_prompt(
                str(character.get("name", "角色")),
                str(character.get("description", "")),
                self.state["comic"]["art_style"],
                base_name,
            )
        prompt = (
            enforce_character_variant_prompt(
                prompt,
                base_name,
                self.state["comic"]["art_style"],
                str(character.get("description", "")),
            )
            if base_name
            else enforce_character_reference_prompt(prompt, self.state["comic"]["art_style"])
        )
        if prompt != str(character.get("prompt", "")).strip():
            self.state["comic"]["characters"][index]["prompt"] = prompt
            self.comic_character_prompt_editor.delete("1.0", END)
            self.comic_character_prompt_editor.insert("1.0", prompt)
        confirm_text = (
            f"将以“{base_name}”的已确认参考图为人物本体，只按照当前要求更换服装，并保持脸、发型、体态与身份一致。"
            if base_name
            else "将使用 Doubao Seedream 5.0 Pro 生成一张独立人物候选定妆图。"
        )
        if not messagebox.askyesno("生成候选定妆", f"{confirm_text}\n\n生成后需要预览并点击“确认候选为参考图”，才会用于后续镜头。是否继续？"):
            return
        output = self._shared_character_asset_dir() / f"{safe_filename(str(character.get('name', '角色')))}_candidate.png"
        resolution = self.comic_resolution_var.get()
        optimize_mode = "fast" if self.comic_optimize_var.get() == "极速模式" else "standard"
        self.is_busy = True
        self.comic_progress["value"] = 0
        self.comic_status.configure(text=f"正在生成 {character.get('name', '角色')} 的定妆照…", fg=ACCENT_DARK)

        def worker() -> None:
            try:
                def progress(task: dict[str, object]) -> None:
                    self.bus.put(("comic_character_progress", (index, str(task.get("progress", "")), str(task.get("id", "")))))

                result = client.generate_image(
                    prompt,
                    images=reference_images or None,
                    size=resolution,
                    aspect=str(self.state["comic"].get("aspect", "9:16")),
                    optimize_mode=optimize_mode,
                    progress=progress,
                )
                client.download_image(str(result["imageUrl"]), output)
                self.bus.put(("comic_character_done", (index, result, str(output))))
            except Exception as exc:
                self.bus.put(("comic_character_error", (index, str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def add_comic_scene(self) -> None:
        self._save_current_comic_scene()
        scenes = self.state["comic"]["scenes"]
        scenes.append(default_scene(f"场景 {len(scenes) + 1}"))
        self._refresh_comic_scene_list(len(scenes) - 1)
        self.store.save(self.state)
        self._refresh_comic_overview()

    def delete_comic_scene(self) -> None:
        index = self.current_comic_scene_index
        scenes = self.state["comic"]["scenes"]
        if index is None or not (0 <= index < len(scenes)):
            messagebox.showinfo("没有场景", "请先选择要删除的场景。")
            return
        name = str(scenes[index].get("name", "该场景"))
        if not messagebox.askyesno("永久删除场景资产", f"确定删除“{name}”吗？\n\n该场景会从项目中移除，素材目录内的候选图和固定参考图也会永久删除，相关分镜的场景绑定会清空。此操作无法撤销。"):
            return
        delete_errors = self._delete_comic_asset_files(scenes[index], "scenes")
        scenes.pop(index)
        for shot in self.state["comic"]["shots"]:
            if str(shot.get("scene", "")) == name:
                shot["scene"] = ""
        self.current_comic_scene_index = None
        self._refresh_comic_scene_list(min(index, len(scenes) - 1) if scenes else None)
        self._refresh_comic_shot_tree(self.current_comic_shot_index)
        self.store.save(self.state)
        self._refresh_comic_overview()
        if delete_errors:
            messagebox.showwarning("部分文件未删除", "场景记录已删除，但以下本地文件被安全保留或删除失败：\n" + "\n".join(delete_errors))

    def _save_current_comic_scene(self) -> None:
        index = self.current_comic_scene_index
        scenes = self.state["comic"].get("scenes", [])
        if (
            index is None
            or not (0 <= index < len(scenes))
            or not hasattr(self, "comic_scene_name_var")
            or not self.comic_scene_description_editor
            or not self.comic_scene_prompt_editor
        ):
            return
        old_name = str(scenes[index].get("name", ""))
        new_name = self.comic_scene_name_var.get().strip() or f"场景 {index + 1}"
        scenes[index]["name"] = new_name
        scenes[index]["description"] = self.comic_scene_description_editor.get("1.0", "end-1c").strip()
        scenes[index]["prompt"] = self.comic_scene_prompt_editor.get("1.0", "end-1c").strip()
        if old_name and old_name != new_name:
            replace_scene_in_shots(self.state["comic"].get("shots", []), old_name, new_name)
            self._refresh_comic_shot_tree(self.current_comic_shot_index)

    def _load_comic_scene(self, index: int) -> None:
        scenes = self.state["comic"]["scenes"]
        if not (0 <= index < len(scenes)):
            self.current_comic_scene_index = None
            return
        self.current_comic_scene_index = index
        scene = scenes[index]
        art_style = self.comic_style_var.get().strip() or str(self.state["comic"].get("art_style", ""))
        aspect = self.comic_aspect_var.get().strip() or str(self.state["comic"].get("aspect", "9:16"))
        prompt = str(scene.get("prompt", "")).strip()
        if not prompt:
            prompt = build_scene_prompt(
                str(scene.get("name", "场景")),
                str(scene.get("description", "")),
                art_style,
                aspect,
            )
        scene["prompt"] = enforce_scene_reference_prompt(prompt, art_style, aspect)
        self._loading_comic_asset_editor = True
        try:
            self.comic_scene_name_var.set(str(scene.get("name", "")))
            self.comic_scene_description_editor.delete("1.0", END)
            self.comic_scene_description_editor.insert("1.0", str(scene.get("description", "")))
            self.comic_scene_prompt_editor.delete("1.0", END)
            self.comic_scene_prompt_editor.insert("1.0", str(scene.get("prompt", "")))
        finally:
            self._loading_comic_asset_editor = False
        local = str(scene.get("local_path", "")) if has_local_reference(scene) else ""
        candidate_path = str(scene.get("candidate_path", ""))
        candidate = candidate_path if candidate_path and Path(candidate_path).is_file() else ""
        status = str(scene.get("status", "未生成"))
        details = [status]
        if candidate:
            details.append(f"候选：{Path(candidate).name}")
        if local:
            details.append(f"已确认：{Path(local).name}")
        self.comic_scene_status.configure(text=" · ".join(details), fg=ACCENT_DARK if local else (WARM if candidate else MUTED))
        confirmed = bool(local and "已确认" in status)
        preview_path = local if confirmed else (candidate or local)
        preview_kind = "已确认场景参考图" if confirmed else ("候选场景图 · 待确认" if candidate else "场景参考图")
        if self.comic_scene_preview_title:
            self.comic_scene_preview_title.configure(text=f"{preview_kind}\n{scene.get('name', '')}", fg=ACCENT_DARK if confirmed else (WARM if candidate else INK))
        self._render_local_image(self.comic_scene_preview_canvas, preview_path, placeholder="尚未生成场景参考图", max_size=(250, 190))

    def _refresh_comic_scene_list(self, selected: int | None = None) -> None:
        if not self.comic_scene_list:
            return
        scenes = self.state["comic"]["scenes"]
        self.comic_scene_list.delete(0, END)
        for item in scenes:
            self.comic_scene_list.insert(END, self._comic_scene_list_text(item))
        if selected is not None and scenes:
            selected = min(max(selected, 0), len(scenes) - 1)
            self.comic_scene_list.selection_set(selected)
            self.comic_scene_list.activate(selected)
            self._load_comic_scene(selected)
        elif not scenes:
            self.current_comic_scene_index = None
            self.comic_scene_name_var.set("")
            self.comic_scene_description_editor.delete("1.0", END)
            self.comic_scene_prompt_editor.delete("1.0", END)
            self._render_local_image(self.comic_scene_preview_canvas, "", placeholder="尚未添加场景", max_size=(250, 190))

    def on_comic_scene_select(self, _event=None) -> None:
        if not self.comic_scene_list or not self.comic_scene_list.curselection():
            return
        next_index = int(self.comic_scene_list.curselection()[0])
        if self.current_comic_scene_index == next_index:
            return
        self._save_current_comic_scene()
        self._load_comic_scene(next_index)

    def choose_comic_scene_image(self) -> None:
        self._save_current_comic_scene()
        index = self.current_comic_scene_index
        if index is None:
            messagebox.showinfo("没有场景", "请先添加或选择一个场景。")
            return
        source = filedialog.askopenfilename(title="选择场景参考图", filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")])
        if not source:
            return
        if Path(source).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            messagebox.showerror("图片格式不支持", "固定参考图仅支持 PNG、JPG、JPEG 或 WebP。")
            return
        scene = self.state["comic"]["scenes"][index]
        destination = self._comic_output_dir() / "scenes" / f"{safe_filename(str(scene.get('name', '场景')))}_reference{Path(source).suffix.lower()}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        scene.update({"local_path": str(destination), "image_url": "", "candidate_path": "", "candidate_image_url": "", "task_id": "", "status": "定景已确认"})
        self.store.save(self.state)
        self._refresh_comic_scene_list(index)
        self._refresh_comic_overview()

    def confirm_comic_scene_candidate(self) -> None:
        self._save_current_comic_scene()
        index = self.current_comic_scene_index
        if index is None:
            messagebox.showinfo("没有场景", "请先选择场景。")
            return
        scene = self.state["comic"]["scenes"][index]
        candidate = Path(str(scene.get("candidate_path", "")))
        if not candidate.is_file():
            messagebox.showinfo("没有候选场景", "请先生成一张候选场景图，再确认保留。")
            return
        reference = self._comic_output_dir() / "scenes" / f"{safe_filename(str(scene.get('name', '场景')))}_reference{candidate.suffix.lower()}"
        try:
            reference.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, reference)
        except OSError as exc:
            messagebox.showerror("确认失败", str(exc))
            return
        scene.update({"local_path": str(reference), "image_url": str(scene.get("candidate_image_url", "")), "status": "定景已确认"})
        self.store.save(self.state)
        self._refresh_comic_scene_list(index)
        self._refresh_comic_overview()
        self.comic_status.configure(text=f"已确认 {scene.get('name', '场景')} 的参考图，绑定它的镜头将自动使用该环境。", fg=ACCENT_DARK)

    def generate_comic_scene(self) -> None:
        if self.is_busy:
            return
        self._sync_comic_state()
        index = self.current_comic_scene_index
        if index is None:
            messagebox.showinfo("没有场景", "请先添加或选择一个场景。")
            return
        try:
            client = self._seedream_client()
        except ComicEngineError as exc:
            messagebox.showwarning("需要 ARK API Key", str(exc))
            return
        scene = dict(self.state["comic"]["scenes"][index])
        prompt = str(scene.get("prompt", "")).strip()
        if not prompt:
            prompt = build_scene_prompt(str(scene.get("name", "场景")), str(scene.get("description", "")), self.state["comic"]["art_style"], self.state["comic"]["aspect"])
        prompt = enforce_scene_reference_prompt(
            prompt,
            self.state["comic"]["art_style"],
            self.state["comic"]["aspect"],
        )
        if prompt != str(scene.get("prompt", "")).strip():
            self.state["comic"]["scenes"][index]["prompt"] = prompt
            self.comic_scene_prompt_editor.delete("1.0", END)
            self.comic_scene_prompt_editor.insert("1.0", prompt)
        if not messagebox.askyesno("生成候选场景", "将使用 Doubao Seedream 5.0 Pro 生成一张无人场景定景图。确认保留后，才会用于后续镜头。是否继续？"):
            return
        output = self._comic_output_dir() / "scenes" / f"{safe_filename(str(scene.get('name', '场景')))}_candidate.png"
        resolution = self.comic_resolution_var.get()
        optimize_mode = "fast" if self.comic_optimize_var.get() == "极速模式" else "standard"
        self.is_busy = True
        self.comic_progress["value"] = 0
        self.comic_status.configure(text=f"正在生成 {scene.get('name', '场景')} 的定景图…", fg=ACCENT_DARK)

        def worker() -> None:
            try:
                def progress(task: dict[str, object]) -> None:
                    self.bus.put(("comic_scene_progress", (index, str(task.get("progress", "")), str(task.get("id", "")))))

                result = client.generate_image(
                    prompt,
                    size=resolution,
                    aspect=str(self.state["comic"].get("aspect", "9:16")),
                    optimize_mode=optimize_mode,
                    progress=progress,
                )
                client.download_image(str(result["imageUrl"]), output)
                self.bus.put(("comic_scene_done", (index, result, str(output))))
            except Exception as exc:
                self.bus.put(("comic_scene_error", (index, str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def preview_comic_scene(self) -> None:
        index = self.current_comic_scene_index
        if index is None:
            messagebox.showinfo("没有场景", "请先选择场景。")
            return
        scene = self.state["comic"]["scenes"][index]
        candidate = str(scene.get("candidate_path", ""))
        has_candidate = bool(candidate and Path(candidate).is_file())
        path = candidate if has_candidate else str(scene.get("local_path") or "")
        title_kind = "候选场景" if has_candidate else "已确认参考"
        self._show_comic_image(path, f"{title_kind} · {scene.get('name', '')}")

    def analyze_comic_story(self, generation_mode: str = "all") -> None:
        if self.is_busy:
            return
        if generation_mode == "both":
            generation_mode = "all"
        if generation_mode not in {"characters", "scenes", "shots", "all"}:
            messagebox.showerror("模式无效", "不支持的 AI 生成模式。")
            return
        self._sync_comic_state()
        comic = self.state["comic"]
        source = comic["source_text"].strip()
        if not source:
            messagebox.showinfo("没有正文", "请导入小说，或把正文粘贴到编辑框。")
            return
        mode_label = {"characters": "只识别角色", "scenes": "只识别场景", "shots": "只生成静态分镜", "all": "生成角色、场景与静态分镜"}[generation_mode]
        if generation_mode in {"shots", "all"} and comic["shots"] and not messagebox.askyesno(
            "重新生成静态分镜",
            f"“{mode_label}”会在全部批次校验通过后替换现有分镜；已确认的角色与场景参考图会按名称保留，"
            "现有分镜图片不会自动套用到新分镜，需要重新出图。是否继续？",
        ):
            return
        try:
            client = self._ai_client()
        except AIClientError as exc:
            messagebox.showwarning("需要文本 AI", f"请先在“模型与工具”中配置文本模型：\n{exc}")
            return
        existing = [dict(item) for item in comic["characters"]]
        existing_scenes = [dict(item) for item in comic.get("scenes", [])]
        art_style = comic["art_style"]
        source_chunks = split_story_source_chunks(source, int(comic.get("analysis_chunk_chars", 3500)))
        if not source_chunks:
            messagebox.showinfo("没有可分析内容", "正文没有可分析的内容。")
            return
        self.is_busy = True
        self.comic_status.configure(text=f"{mode_label}：正文将分 {len(source_chunks)} 个请求发送，AI 将按单张静止画面的变化判断边界…", fg=ACCENT_DARK)
        self.comic_progress["value"] = 2

        def worker() -> None:
            accumulated_characters = [dict(item) for item in existing]
            accumulated_scenes = [dict(item) for item in existing_scenes]
            generated_shots: list[dict[str, object]] = []
            pending_batches = [
                {"chunk_id": f"B{index:04d}", "source": chunk}
                for index, chunk in enumerate(source_chunks, start=1)
            ]
            request_index = 0
            completed_chars = 0
            total_chars = sum(len(chunk) for chunk in source_chunks)
            split_count = 0
            while pending_batches:
                batch = pending_batches.pop(0)
                batch_source = str(batch.get("source", "")).strip()
                batch_id = str(batch.get("chunk_id", "当前批次"))
                request_index += 1
                last_error: Exception | None = None
                parsed: dict[str, list[dict[str, object]]] | None = None
                ordered_shots: list[dict[str, object]] = []
                batch_succeeded = False
                for attempt in range(1, 4):
                    try:
                        system, user = build_ai_split_storyboard_prompt(
                            batch_source,
                            art_style=art_style,
                            existing_characters=accumulated_characters if generation_mode in {"characters", "all"} else existing,
                            existing_scenes=accumulated_scenes if generation_mode in {"scenes", "all"} else existing_scenes,
                            generation_mode=generation_mode,
                            batch_index=request_index,
                            batch_total=request_index + len(pending_batches),
                        )
                        raw = client.complete(system, user, temperature=0.2)
                        parsed = parse_storyboard_response(raw, art_style=art_style, generation_mode=generation_mode)
                        if generation_mode in {"shots", "all"}:
                            ordered_shots = validate_ai_storyboard_split(
                                parsed["shots"], batch_source, start_index=len(generated_shots) + 1
                            )
                        batch_succeeded = True
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt < 3:
                            self.bus.put(("comic_analysis_retry", (request_index, request_index + len(pending_batches), str(exc))))
                if not batch_succeeded or parsed is None:
                    smaller_chunks = split_story_source_chunks(batch_source, max(300, len(batch_source) // 2))
                    if len(smaller_chunks) > 1:
                        replacements = [
                            {"chunk_id": f"{batch_id}.{index}", "source": chunk}
                            for index, chunk in enumerate(smaller_chunks, start=1)
                        ]
                        pending_batches[0:0] = replacements
                        split_count += 1
                        self.bus.put(("comic_analysis_split", (batch_id, len(batch_source), str(last_error or "未知错误"))))
                        continue
                    self.bus.put(("comic_analysis_error", (request_index, request_index + len(pending_batches), f"{batch_id} 连续三次失败：{last_error or '未知错误'}")))
                    return
                if generation_mode in {"characters", "all"}:
                    accumulated_characters = self._merge_comic_characters(accumulated_characters, parsed["characters"])
                if generation_mode in {"scenes", "all"}:
                    accumulated_scenes = self._merge_comic_scenes(accumulated_scenes, parsed["scenes"])
                if generation_mode in {"shots", "all"}:
                    generated_shots.extend(ordered_shots)
                completed_chars += len(batch_source)
                self.bus.put(("comic_analysis_progress", (completed_chars, total_chars, mode_label)))

            if generation_mode == "characters" and not accumulated_characters:
                self.bus.put(("comic_analysis_error", (len(source_chunks), len(source_chunks), "所有批次均未识别到角色。")))
                return
            if generation_mode == "scenes" and not accumulated_scenes:
                self.bus.put(("comic_analysis_error", (len(source_chunks), len(source_chunks), "所有批次均未识别到固定场景。")))
                return
            for index, shot in enumerate(generated_shots, start=1):
                original_title = str(shot.get("title", "")).strip()
                shot["index"] = index
                shot["title"] = f"{index:03d} · {original_title}" if original_title else f"分镜 {index:03d}"
            result = {
                "characters": accumulated_characters if generation_mode in {"characters", "all"} else [],
                "scenes": accumulated_scenes if generation_mode in {"scenes", "all"} else [],
                "shots": generated_shots if generation_mode in {"shots", "all"} else [],
            }
            split_note = f"，并自动细分 {split_count} 次" if split_count else ""
            if generation_mode in {"shots", "all"}:
                note = f"{mode_label}完成：AI 按静态画面节奏拆分为 {len(generated_shots)} 个分镜，并已校验原文完整性{split_note}。"
            else:
                note = f"{mode_label}完成：已分析全部正文{split_note}。"
            self.bus.put(("comic_analysis_done", (result, note, generation_mode)))

        threading.Thread(target=worker, daemon=True).start()

    def split_comic_locally(self) -> None:
        self.analyze_comic_story("shots")

    @staticmethod
    def _merge_comic_characters(existing: list[dict[str, object]], analyzed: list[dict[str, object]]) -> list[dict[str, object]]:
        by_name = {str(item.get("name", "")).strip(): item for item in existing if str(item.get("name", "")).strip()}
        merged: list[dict[str, object]] = []
        for item in analyzed:
            name = str(item.get("name", "")).strip()
            old = by_name.pop(name, None)
            if old:
                record = dict(item)
                for key in ("task_id", "image_url", "local_path", "candidate_path", "candidate_image_url", "status"):
                    if old.get(key):
                        record[key] = old[key]
                if old.get("description") and not record.get("description"):
                    record["description"] = old["description"]
                merged.append(record)
            else:
                merged.append(dict(item))
        merged.extend(by_name.values())
        return merged

    @staticmethod
    def _merge_comic_scenes(existing: list[dict[str, object]], analyzed: list[dict[str, object]]) -> list[dict[str, object]]:
        by_name = {str(item.get("name", "")).strip(): item for item in existing if str(item.get("name", "")).strip()}
        merged: list[dict[str, object]] = []
        for item in analyzed:
            name = str(item.get("name", "")).strip()
            old = by_name.pop(name, None)
            if old:
                record = dict(item)
                for key in ("task_id", "image_url", "local_path", "candidate_path", "candidate_image_url", "status"):
                    if old.get(key):
                        record[key] = old[key]
                if old.get("description") and not record.get("description"):
                    record["description"] = old["description"]
                merged.append(record)
            else:
                merged.append(dict(item))
        merged.extend(by_name.values())
        return merged

    def _invalidate_comic_draft(self) -> None:
        comic = self.state["comic"]
        comic["video_output_path"] = ""
        comic["jianying_draft_path"] = ""
        comic["jianying_draft_name"] = ""
        if hasattr(self, "comic_video_output_var"):
            self.comic_video_output_var.set("")
        if hasattr(self, "comic_draft_output_var"):
            self.comic_draft_output_var.set("")

    @staticmethod
    def _mark_comic_shot_stale(shot: dict[str, object]) -> None:
        shot.update(
            {
                "task_id": "",
                "status": "待重新生成",
                "progress": "0%",
                "image_url": "",
                "local_path": "",
                "error": "",
                "final_prompt": "",
            }
        )

    def _comic_batch_shot_indices(self) -> list[int]:
        shots = self.state["comic"].get("shots", [])
        if self.comic_batch_scope_var.get() != "选中分镜":
            return list(range(len(shots)))
        return self._selected_comic_shot_indices()

    def _selected_comic_shot_indices(self) -> list[int]:
        shots = self.state["comic"].get("shots", [])
        if self.comic_storyboard_body:
            return sorted(index for index in self.comic_storyboard_selected_indices if 0 <= index < len(shots))
        if not self.comic_shot_tree:
            return []
        return sorted({int(item) for item in self.comic_shot_tree.selection() if str(item).isdigit() and 0 <= int(item) < len(shots)})

    def batch_replace_comic_character(self) -> None:
        self.save_comic_shot_prompt(silent=True)
        source = self.comic_batch_character_from_var.get().strip()
        target_value = self.comic_batch_character_to_var.get().strip()
        target = "" if target_value == "（移除角色）" else target_value
        indices = self._comic_batch_shot_indices()
        if not source:
            messagebox.showinfo("请选择角色", "请选择需要被替换的角色。")
            return
        if not indices:
            messagebox.showinfo("没有选中分镜", "请先选择一个或多个分镜，或将范围改为“全部分镜”。")
            return
        shots = self.state["comic"]["shots"]
        affected = [index for index in indices if source in {str(name) for name in shots[index].get("characters", [])}]
        changed = replace_character_in_shots([shots[index] for index in indices], source, target)
        if not changed:
            messagebox.showinfo("没有匹配项", f"所选范围内没有绑定角色“{source}”。")
            return
        for index in affected:
            self._mark_comic_shot_stale(shots[index])
        self._invalidate_comic_draft()
        self.store.save(self.state)
        self._refresh_comic_shot_tree(affected[0] if affected else self.current_comic_shot_index)
        action = f"替换为“{target}”" if target else "移除"
        self.comic_status.configure(text=f"已在 {changed} 个静态分镜中将“{source}”{action}。", fg=ACCENT_DARK)

    def batch_replace_comic_scene(self) -> None:
        self.save_comic_shot_prompt(silent=True)
        source = self.comic_batch_scene_from_var.get().strip()
        target_value = self.comic_batch_scene_to_var.get().strip()
        target = "" if target_value == "（清空场景）" else target_value
        indices = self._comic_batch_shot_indices()
        if not source:
            messagebox.showinfo("请选择场景", "请选择需要被替换的固定场景。")
            return
        if not indices:
            messagebox.showinfo("没有选中分镜", "请先选择一个或多个分镜，或将范围改为“全部分镜”。")
            return
        shots = self.state["comic"]["shots"]
        affected = [index for index in indices if str(shots[index].get("scene", "")).strip() == source]
        changed = replace_scene_in_shots([shots[index] for index in indices], source, target)
        if not changed:
            messagebox.showinfo("没有匹配项", f"所选范围内没有绑定场景“{source}”。")
            return
        for index in affected:
            self._mark_comic_shot_stale(shots[index])
        self._invalidate_comic_draft()
        self.store.save(self.state)
        self._refresh_comic_shot_tree(affected[0] if affected else self.current_comic_shot_index)
        action = f"替换为“{target}”" if target else "清空"
        self.comic_status.configure(text=f"已在 {changed} 个静态分镜中将“{source}”{action}。", fg=ACCENT_DARK)

    def _refresh_comic_shot_binding_controls(self) -> None:
        index = self.current_comic_shot_index
        shots = self.state["comic"].get("shots", [])
        self._loading_comic_shot_editor = True
        try:
            if self.comic_shot_character_list:
                selected_names = {str(name) for name in shots[index].get("characters", [])} if index is not None and 0 <= index < len(shots) else set()
                self.comic_shot_character_list.delete(0, END)
                for character_index, character in enumerate(self.state["comic"].get("characters", [])):
                    name = str(character.get("name", "未命名角色"))
                    self.comic_shot_character_list.insert(END, name)
                    if name in selected_names:
                        self.comic_shot_character_list.selection_set(character_index)
            if self.comic_shot_scene_combo:
                values = [""] + [str(scene.get("name", "未命名场景")) for scene in self.state["comic"].get("scenes", [])]
                self.comic_shot_scene_combo.configure(values=values)
                if index is not None and 0 <= index < len(shots):
                    self.comic_shot_scene_var.set(str(shots[index].get("scene", "")))
        finally:
            self._loading_comic_shot_editor = False

    def _comic_shot_preview_image(self, index: int, shot: dict[str, object]) -> ImageTk.PhotoImage:
        """Build a compact preview that stays inside the matching storyboard row."""
        width, height = 112, 142
        path = Path(str(shot.get("local_path", "")))
        if path.is_file():
            try:
                stat = path.stat()
                cache_key = ("shot-tree", str(path.absolute()), stat.st_mtime_ns, stat.st_size, width, height)
            except OSError:
                cache_key = ("shot-tree-placeholder", width, height)
        else:
            cache_key = ("shot-tree-placeholder", width, height)
        cached = self._cached_thumbnail_photo(cache_key)
        if cached is not None:
            self.comic_shot_preview_images[index] = cached
            return cached
        preview = Image.new("RGB", (width, height), COMIC_INSET)
        if path.is_file():
            try:
                with Image.open(path) as source:
                    image = ImageOps.exif_transpose(source).convert("RGB")
                    image.thumbnail((width - 8, height - 8), Image.Resampling.LANCZOS)
                    x = (width - image.width) // 2
                    y = (height - image.height) // 2
                    preview.paste(image, (x, y))
            except (OSError, ValueError):
                path = Path()
        if not path.is_file():
            draw = ImageDraw.Draw(preview)
            draw.rounded_rectangle((8, 8, width - 9, height - 9), radius=14, fill=SURFACE_ALT, outline=BORDER, width=2)
            draw.rounded_rectangle((25, 37, width - 26, height - 38), radius=9, outline="#9CB2B0", width=3)
            draw.ellipse((38, 50, 51, 63), fill="#9CB2B0")
            draw.line((31, height - 49, 52, height - 70, 65, height - 57, 78, height - 76, width - 31, height - 49), fill="#9CB2B0", width=4, joint="curve")
        image = self._remember_thumbnail_photo(cache_key, ImageTk.PhotoImage(preview))
        self.comic_shot_preview_images[index] = image
        return image

    @staticmethod
    def _comic_shot_preview_signature(shot: dict[str, object]) -> tuple[object, ...]:
        path = Path(str(shot.get("local_path", "")))
        if not path.is_file():
            return ("", 0, 0)
        try:
            stat = path.stat()
            return (str(path.absolute()), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (str(path), 0, 0)

    def _schedule_visible_comic_shot_previews(self) -> None:
        if not self.comic_shot_tree_with_previews or not self.comic_shot_tree:
            return
        if self.comic_shot_preview_after_id is not None:
            return

        def load() -> None:
            self.comic_shot_preview_after_id = None
            self._load_visible_comic_shot_previews()

        try:
            self.comic_shot_preview_after_id = self.root.after_idle(load)
        except TclError:
            self.comic_shot_preview_after_id = None

    def _load_visible_comic_shot_previews(self) -> None:
        self.comic_shot_preview_after_id = None
        tree = self.comic_shot_tree
        if not self.comic_shot_tree_with_previews or not tree:
            return
        try:
            items = tree.get_children()
            first, last = tree.yview()
        except TclError:
            return
        if not items:
            return
        start = max(0, int(first * len(items)) - 2)
        end = min(len(items), max(start + 1, int(last * len(items) + 0.999) + 2))
        shots = self.state["comic"].get("shots", [])
        pending: list[tuple[str, int, dict[str, object], tuple[object, ...]]] = []
        for item in items[start:end]:
            index = int(item)
            if not (0 <= index < len(shots)):
                continue
            shot = shots[index]
            signature = self._comic_shot_preview_signature(shot)
            if self.comic_shot_loaded_preview_signatures.get(index) != signature:
                pending.append((item, index, shot, signature))
        for item, index, shot, signature in pending[:3]:
            try:
                tree.item(item, image=self._comic_shot_preview_image(index, shot))
                self.comic_shot_loaded_preview_signatures[index] = signature
            except TclError:
                return
        if len(pending) > 3:
            try:
                self.comic_shot_preview_after_id = self.root.after(12, self._load_visible_comic_shot_previews)
            except TclError:
                self.comic_shot_preview_after_id = None

    def _update_comic_shot_tree_row(self, index: int) -> None:
        if self.comic_storyboard_body:
            self._update_comic_storyboard_row(index)
            return
        shots = self.state["comic"].get("shots", [])
        if not self.comic_shot_tree or not (0 <= index < len(shots)) or not self.comic_shot_tree.exists(str(index)):
            return
        shot = shots[index]
        status = str(shot.get("status", "待生成"))
        progress = str(shot.get("progress", ""))
        if progress and progress not in {"0%", "100%"}:
            status = f"{status} {progress}"
        tags: list[str] = ["alternate"] if index % 2 else []
        if shot.get("local_path") or shot.get("image_url"):
            tags.append("done")
        elif shot.get("error"):
            tags.append("error")
        options: dict[str, object] = {
            "text": "",
            "values": (shot.get("title", f"分镜 {index + 1:02d}"), "、".join(shot.get("characters", [])), shot.get("scene", ""), status),
            "tags": tuple(tags),
        }
        if self.comic_shot_tree_with_previews:
            options["image"] = self._comic_shot_preview_image(-1, {})
            self.comic_shot_loaded_preview_signatures.pop(index, None)
        self.comic_shot_tree.item(str(index), **options)
        if self.comic_shot_tree_with_previews:
            self._schedule_visible_comic_shot_previews()

    def _refresh_comic_shot_tree(self, selected: int | None = None) -> None:
        if self.comic_storyboard_body:
            self._refresh_comic_storyboard_rows(selected)
            return
        if not self.comic_shot_tree:
            return
        self.comic_shot_preview_images = {}
        self.comic_shot_loaded_preview_signatures = {}
        for item in self.comic_shot_tree.get_children():
            self.comic_shot_tree.delete(item)
        shots = self.state["comic"]["shots"]
        for index, shot in enumerate(shots):
            status = str(shot.get("status", "待生成"))
            progress = str(shot.get("progress", ""))
            if progress and progress not in {"0%", "100%"}:
                status = f"{status} {progress}"
            tags: list[str] = []
            if index % 2:
                tags.append("alternate")
            if shot.get("local_path") or shot.get("image_url"):
                tags.append("done")
            elif shot.get("error"):
                tags.append("error")
            options: dict[str, object] = {
                "text": "",
                "values": (shot.get("title", f"分镜 {index + 1:02d}"), "、".join(shot.get("characters", [])), shot.get("scene", ""), status),
                "tags": tuple(tags),
            }
            if self.comic_shot_tree_with_previews:
                options["image"] = self._comic_shot_preview_image(-1, {})
            self.comic_shot_tree.insert("", END, iid=str(index), **options)
        done = sum(1 for shot in shots if shot.get("local_path"))
        if self.comic_step == 3:
            self.comic_count_label.configure(text=f"{len(shots)} 个分镜 · {done} 张有图")
        else:
            self.comic_count_label.configure(text=f"{done}/{len(shots)} 已完成")
        if selected is not None and shots:
            selected = min(max(selected, 0), len(shots) - 1)
            self.comic_shot_tree.selection_set(str(selected))
            self.comic_shot_tree.focus(str(selected))
            self._load_comic_shot(selected)
        elif not shots:
            self.current_comic_shot_index = None
            if self.comic_shot_prompt_editor:
                self.comic_shot_prompt_editor.delete("1.0", END)
            if self.comic_generation_detail_label:
                self.comic_generation_detail_label.configure(text="暂无分镜。请返回上一步分析或拆分小说。")
        if self.comic_shot_tree_with_previews:
            self._schedule_visible_comic_shot_previews()

    def _load_comic_shot(self, index: int) -> None:
        shots = self.state["comic"]["shots"]
        if not (0 <= index < len(shots)):
            return
        self.current_comic_shot_index = index
        shot = shots[index]
        source = str(shot.get("source", "")).replace("\n", " ")
        if self.comic_shot_source_label:
            self.comic_shot_source_label.configure(text=f"{shot.get('title', '')} · 原文：{source[:240]}")
        if self.comic_shot_prompt_editor:
            self.comic_shot_prompt_editor.delete("1.0", END)
            self.comic_shot_prompt_editor.insert("1.0", str(shot.get("prompt", "")))
        self._loading_comic_shot_editor = True
        try:
            if self.comic_shot_character_list:
                self.comic_shot_character_list.selection_clear(0, END)
                selected_names = {str(name) for name in shot.get("characters", [])}
                for character_index, character in enumerate(self.state["comic"]["characters"]):
                    if str(character.get("name", "")) in selected_names:
                        self.comic_shot_character_list.selection_set(character_index)
            if self.comic_shot_scene_combo:
                self.comic_shot_scene_var.set(str(shot.get("scene", "")))
        finally:
            self._loading_comic_shot_editor = False
        if self.comic_generation_detail_label:
            characters = "、".join(shot.get("characters", [])) or "无指定角色"
            scene = str(shot.get("scene", "")).strip() or "未绑定场景"
            path = str(shot.get("local_path", ""))
            detail = f"{shot.get('title', '')}\n角色：{characters}\n场景：{scene}\n状态：{shot.get('status', '待生成')}"
            image_model = str(shot.get("image_model", "")).strip()
            if image_model:
                detail += f"\n模型：{SHOT_IMAGE_MODEL_LABELS.get(image_model, image_model)}"
            if path:
                detail += f"\n文件：{Path(path).name}"
            if shot.get("error"):
                detail += f"\n失败原因：{shot.get('error')}"
            self.comic_generation_detail_label.configure(text=detail)

    def on_comic_shot_select(self, _event=None) -> None:
        if not self.comic_shot_tree or not self.comic_shot_tree.selection():
            return
        selected = self.comic_shot_tree.selection()
        focused = self.comic_shot_tree.focus()
        next_item = focused if focused in selected else selected[-1]
        next_index = int(next_item)
        if self.current_comic_shot_index == next_index:
            return
        self.save_comic_shot_prompt(silent=True)
        self._load_comic_shot(next_index)

    def _toggle_comic_shot_selection(self, event):
        """Toggle a shot with a plain click so multi-selection never needs Ctrl."""
        if not self.comic_shot_tree or self.comic_shot_tree.identify_region(event.x, event.y) not in {"cell", "tree"}:
            return None
        item = self.comic_shot_tree.identify_row(event.y)
        if not item:
            return None
        if self.comic_shot_tree_with_previews and self.comic_shot_tree.identify_column(event.x) == "#0":
            index = int(item)
            self.comic_shot_tree.selection_set(item)
            self.comic_shot_tree.focus(item)
            self._load_comic_shot(index)
            shot = self.state["comic"]["shots"][index]
            path = str(shot.get("local_path", ""))
            if Path(path).is_file():
                self._show_comic_image(path, str(shot.get("title", f"分镜 {index + 1:03d}")))
            else:
                self.comic_status.configure(text=f"分镜 {index + 1:03d} 尚未生成图片。", fg=MUTED)
            return "break"
        selected = set(self.comic_shot_tree.selection())
        if item in selected:
            self.comic_shot_tree.selection_remove(item)
        else:
            self.save_comic_shot_prompt(silent=True)
            self.comic_shot_tree.selection_add(item)
            self.comic_shot_tree.focus(item)
            self._load_comic_shot(int(item))
        return "break"

    def select_all_comic_shots(self) -> None:
        if self.comic_storyboard_body:
            shots = self.state["comic"].get("shots", [])
            self.comic_storyboard_selected_indices = set(range(len(shots)))
            for index, selection_var in self.comic_storyboard_selection_vars.items():
                selection_var.set("✓ 已选")
                widgets = self.comic_storyboard_row_widgets.get(index, {})
                if widgets.get("selection"):
                    widgets["selection"].configure(bg=ACCENT, fg=SIDEBAR)
                if widgets.get("outer"):
                    widgets["outer"].configure(highlightbackground=ACCENT)
            return
        if not self.comic_shot_tree:
            return
        items = self.comic_shot_tree.get_children()
        if items:
            self.comic_shot_tree.selection_set(items)

    def clear_comic_shot_selection(self) -> None:
        if self.comic_storyboard_body:
            self.comic_storyboard_selected_indices.clear()
            for index, selection_var in self.comic_storyboard_selection_vars.items():
                selection_var.set("○ 选择")
                widgets = self.comic_storyboard_row_widgets.get(index, {})
                if widgets.get("selection"):
                    widgets["selection"].configure(bg=SURFACE_ALT, fg=MUTED)
                if widgets.get("outer"):
                    widgets["outer"].configure(highlightbackground=widgets.get("default_border", SURFACE))
            return
        if self.comic_shot_tree:
            self.comic_shot_tree.selection_remove(self.comic_shot_tree.selection())

    @staticmethod
    def _renumber_comic_shots(shots: list[dict[str, object]]) -> None:
        for index, shot in enumerate(shots, start=1):
            shot["index"] = index
            shot["segment_id"] = f"S{index:05d}"

    def merge_selected_comic_shots(self) -> None:
        self.save_comic_shot_prompt(silent=True)
        indices = self._selected_comic_shot_indices()
        if len(indices) != 2:
            messagebox.showinfo("请选择两个镜头", "请在分镜列表中选中两个相邻镜头，再点击合并。普通单击即可多选，不需要按 Ctrl。")
            return
        first_index, second_index = indices
        if second_index != first_index + 1:
            messagebox.showinfo("镜头不相邻", "只能合并前后相邻的两个镜头，请重新选择。")
            return
        shots = self.state["comic"]["shots"]
        first = shots[first_index]
        second = shots[second_index]
        notes: list[str] = []
        if str(first.get("scene", "")).strip() != str(second.get("scene", "")).strip():
            notes.append("两个镜头的场景不同，合并后场景会留空，请重新选择。")
        if first.get("local_path") or second.get("local_path"):
            notes.append("两个镜头现有的图片将不再绑定，合并后的镜头需要重新出图。")
        if notes and not messagebox.askyesno("确认合并镜头", "\n\n".join(notes) + "\n\n是否继续？"):
            return
        merged = merge_storyboard_shots(first, second)
        shots[first_index : second_index + 1] = [merged]
        self._renumber_comic_shots(shots)
        self._invalidate_comic_draft()
        self.store.save(self.state)
        self.current_comic_shot_index = first_index
        self.comic_storyboard_selected_indices = {first_index}
        self._refresh_comic_shot_tree(first_index)
        self._refresh_comic_overview()
        self.comic_status.configure(text=f"已将第 {first_index + 1}、{second_index + 1} 个镜头合并，请在对应行校对提示词。", fg=ACCENT_DARK)

    def split_current_comic_shot(self) -> None:
        self.save_comic_shot_prompt(silent=True)
        selected_indices = self._selected_comic_shot_indices()
        if self.comic_storyboard_body:
            if len(selected_indices) != 1:
                messagebox.showinfo("请选择一个镜头", "请先在列表中只选中一个需要拆分的镜头。")
                return
            index = selected_indices[0]
        else:
            index = selected_indices[0] if len(selected_indices) == 1 else self.current_comic_shot_index
        shots = self.state["comic"]["shots"]
        if index is None or not (0 <= index < len(shots)):
            messagebox.showinfo("请选择镜头", "请先在列表中选择一个需要拆分的镜头。")
            return
        shot = shots[index]
        source = str(shot.get("source", ""))
        if len(source.strip()) < 2:
            messagebox.showinfo("原文太短", "当前镜头没有足够的原文可拆分。")
            return

        midpoint = len(source) // 2
        boundaries = [position + 1 for position, char in enumerate(source) if char in "。！？!?；;\n"]
        initial_offset = min(boundaries, key=lambda value: abs(value - midpoint)) if boundaries else midpoint
        initial_offset = min(max(initial_offset, 1), len(source) - 1)

        dialog = Toplevel(self.root)
        dialog.title(f"手动拆分 · {shot.get('title', f'分镜 {index + 1:02d}')}")
        dialog.geometry("860x650")
        dialog.minsize(760, 580)
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.grab_set()
        Label(dialog, text="手动选择拆分位置", bg=BG, fg=INK, font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w", padx=24, pady=(20, 4))
        Label(dialog, text="在原文中单击分界位置：绿色部分成为上一个镜头，浅黄色部分成为下一个镜头。不会重新调用 AI。", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=24)

        source_outer = self._card(dialog, bg=SURFACE, padx=14, pady=12)
        source_outer.pack(fill=X, padx=24, pady=(16, 10))
        source_panel = source_outer.winfo_children()[0]
        source_editor_outer, source_editor_shell = self._rounded_widget_shell(source_panel, bg=SURFACE, fixed_height=220)
        source_editor_outer.pack(fill=X)
        source_editor = Text(source_editor_shell, height=9, wrap="word", bg=SURFACE, fg=INK, relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=14, pady=12, cursor="xterm", font=("Microsoft YaHei UI", 10))
        self._pack_vertical_scroller(source_editor_shell, source_editor, fill=X, expand=True)
        source_editor.insert("1.0", source)
        source_editor.tag_configure("before_split", background="#DDF1EC")
        source_editor.tag_configure("after_split", background="#F7EEDC")

        preview_row = Frame(dialog, bg=BG)
        preview_row.pack(fill=BOTH, expand=True, padx=24)
        preview_row.grid_columnconfigure(0, weight=1, uniform="split_preview")
        preview_row.grid_columnconfigure(1, weight=1, uniform="split_preview")
        before_outer = self._card(preview_row, bg=SURFACE, padx=12, pady=10)
        before_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        after_outer = self._card(preview_row, bg=SURFACE, padx=12, pady=10)
        after_outer.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        before_panel = before_outer.winfo_children()[0]
        after_panel = after_outer.winfo_children()[0]
        Label(before_panel, text="上一个镜头原文", bg=SURFACE, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        Label(after_panel, text="下一个镜头原文", bg=SURFACE, fg=WARM, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        before_text = Text(before_panel, height=7, wrap="word", state="disabled", bg=SURFACE, fg=INK, relief="flat", padx=5, pady=7, font=("Microsoft YaHei UI", 9))
        before_text.pack(fill=BOTH, expand=True)
        after_text = Text(after_panel, height=7, wrap="word", state="disabled", bg=SURFACE, fg=INK, relief="flat", padx=5, pady=7, font=("Microsoft YaHei UI", 9))
        after_text.pack(fill=BOTH, expand=True)
        split_state = {"offset": initial_offset}
        split_status_var = StringVar()

        def set_preview_text(widget: Text, value: str) -> None:
            widget.configure(state="normal")
            widget.delete("1.0", END)
            widget.insert("1.0", value)
            widget.configure(state="disabled")

        def update_split(offset: int) -> None:
            offset = min(max(int(offset), 1), len(source) - 1)
            split_state["offset"] = offset
            split_index = f"1.0+{offset}c"
            source_editor.tag_remove("before_split", "1.0", END)
            source_editor.tag_remove("after_split", "1.0", END)
            source_editor.tag_add("before_split", "1.0", split_index)
            source_editor.tag_add("after_split", split_index, "end-1c")
            source_editor.mark_set("insert", split_index)
            source_editor.see(split_index)
            before = source[:offset].strip()
            after = source[offset:].strip()
            set_preview_text(before_text, before)
            set_preview_text(after_text, after)
            split_status_var.set(f"拆分位置：第 {offset} / {len(source)} 个字符 · 上镜头 {len(before)} 字 · 下镜头 {len(after)} 字")

        def choose_split(event) -> str:
            text_index = source_editor.index(f"@{event.x},{event.y}")
            count = source_editor.count("1.0", text_index, "chars")
            update_split(int(count[0]) if count else initial_offset)
            return "break"

        def move_split(event) -> str:
            if event.keysym == "Left":
                update_split(split_state["offset"] - 1)
            elif event.keysym == "Right":
                update_split(split_state["offset"] + 1)
            elif event.keysym == "Home":
                update_split(1)
            elif event.keysym == "End":
                update_split(len(source) - 1)
            return "break"

        source_editor.bind("<Button-1>", choose_split)
        source_editor.bind("<KeyPress>", move_split)
        update_split(initial_offset)

        footer = Frame(dialog, bg=BG)
        footer.pack(fill=X, padx=24, pady=(12, 20))
        Label(footer, textvariable=split_status_var, bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side=LEFT)
        self._button(footer, "取消", dialog.destroy, kind="ghost").pack(side=RIGHT)

        def confirm_split() -> None:
            try:
                first, second = split_storyboard_shot(shot, split_state["offset"])
            except ValueError as exc:
                messagebox.showwarning("无法拆分", str(exc), parent=dialog)
                return
            shots[index : index + 1] = [first, second]
            self._renumber_comic_shots(shots)
            self._invalidate_comic_draft()
            self.store.save(self.state)
            dialog.destroy()
            self.current_comic_shot_index = index
            self.comic_storyboard_selected_indices = {index, index + 1}
            self._refresh_comic_shot_tree(index)
            self._refresh_comic_overview()
            self.comic_status.configure(text=f"已将第 {index + 1} 个镜头拆成两个，请在对应行校对提示词。", fg=ACCENT_DARK)

        self._button(footer, "确认拆成两个镜头", confirm_split, kind="accent").pack(side=RIGHT, padx=(0, 8))

    def _on_comic_shot_binding_change(self, _event=None) -> None:
        if self._loading_comic_shot_editor:
            return
        self.save_comic_shot_prompt(silent=True)

    def save_comic_shot_prompt(self, silent: bool = False) -> None:
        if self.comic_storyboard_prompt_editors:
            self._save_all_inline_comic_shot_prompts()
            if not self.comic_shot_prompt_editor:
                if not silent and hasattr(self, "comic_status"):
                    self.comic_status.configure(text="分镜提示词已保存。", fg=ACCENT_DARK)
                return
        index = self.current_comic_shot_index
        shots = self.state["comic"]["shots"]
        if index is None or not (0 <= index < len(shots)) or not self.comic_shot_prompt_editor:
            return
        old_prompt = str(shots[index].get("prompt", "")).strip()
        old_characters = [str(item) for item in shots[index].get("characters", [])]
        old_scene = str(shots[index].get("scene", "")).strip()
        new_prompt = self.comic_shot_prompt_editor.get("1.0", "end-1c").strip()
        shots[index]["prompt"] = new_prompt
        if self.comic_shot_character_list:
            characters = self.state["comic"]["characters"]
            shots[index]["characters"] = [
                str(characters[item].get("name", ""))
                for item in self.comic_shot_character_list.curselection()
                if 0 <= item < len(characters)
            ]
        if self.comic_shot_scene_combo:
            shots[index]["scene"] = self.comic_shot_scene_var.get().strip()
        changed = (
            old_prompt != new_prompt
            or old_characters != [str(item) for item in shots[index].get("characters", [])]
            or old_scene != str(shots[index].get("scene", "")).strip()
        )
        if changed and (shots[index].get("local_path") or shots[index].get("image_url")):
            self._mark_comic_shot_stale(shots[index])
            self._invalidate_comic_draft()
        self.store.save(self.state)
        self._update_comic_shot_tree_row(index)
        if not silent and hasattr(self, "comic_status"):
            self.comic_status.configure(text="当前分镜提示词已保存。", fg=ACCENT_DARK)

    def select_all_comic_shot_characters(self) -> None:
        if self.comic_shot_character_list:
            self.comic_shot_character_list.selection_set(0, END)

    def clear_comic_shot_characters(self) -> None:
        if self.comic_shot_character_list:
            self.comic_shot_character_list.selection_clear(0, END)

    def _comic_reference_characters(self, shot: dict[str, object], characters: list[dict[str, object]]) -> list[dict[str, object]]:
        selected_names = {str(name) for name in shot.get("characters", [])}
        if not selected_names:
            source = str(shot.get("source", ""))
            selected_names = {str(item.get("name", "")) for item in characters if str(item.get("name", "")) and str(item.get("name", "")) in source}
        if not selected_names and len(characters) == 1:
            selected_names = {str(characters[0].get("name", ""))}
        return [item for item in characters if str(item.get("name", "")) in selected_names]

    @staticmethod
    def _comic_reference_scene(shot: dict[str, object], scenes: list[dict[str, object]]) -> dict[str, object] | None:
        scene_name = str(shot.get("scene", "")).strip()
        return next((item for item in scenes if str(item.get("name", "")).strip() == scene_name), None)

    def _generate_comic_shots(self, indices: list[int], *, confirm_batch: bool = True) -> None:
        if self.is_busy or not indices:
            return
        self._sync_comic_state()
        comic = self.state["comic"]
        shot_model_id = str(comic.get("shot_image_model", SEEDREAM_LITE_MODEL)).strip()
        if shot_model_id not in {SEEDREAM_LITE_MODEL, SEEDREAM_PRO_MODEL}:
            shot_model_id = SEEDREAM_LITE_MODEL
            comic["shot_image_model"] = shot_model_id
        shot_model_label = SHOT_IMAGE_MODEL_LABELS[shot_model_id]
        try:
            client = self._seedream_client(shot_model_id)
        except ComicEngineError as exc:
            messagebox.showwarning("需要 ARK API Key", str(exc))
            return
        characters = [dict(item) for item in comic["characters"]]
        scenes = [dict(item) for item in comic.get("scenes", [])]
        shots = [dict(item) for item in comic["shots"]]
        missing: set[str] = set()
        missing_scenes: set[str] = set()
        too_many_references: list[str] = []
        for index in indices:
            selected_characters = self._comic_reference_characters(shots[index], characters)
            selected_scene = self._comic_reference_scene(shots[index], scenes)
            reference_count = len(selected_characters) + (1 if selected_scene else 0)
            if reference_count > 10:
                too_many_references.append(f"{shots[index].get('title', f'分镜 {index + 1:02d}')}（{len(selected_characters)} 个角色 + {1 if selected_scene else 0} 个场景）")
            for character in selected_characters:
                if not has_local_reference(character):
                    missing.add(str(character.get("name", "未命名角色")))
            assigned_scene = str(shots[index].get("scene", "")).strip()
            if assigned_scene and (not selected_scene or not has_local_reference(selected_scene)):
                missing_scenes.add(assigned_scene)
        if too_many_references:
            messagebox.showwarning("参考图过多", "Seedream 5.0 每次最多接收 10 张参考图（角色与场景合计），请调整以下分镜：\n" + "\n".join(too_many_references))
            return
        if missing:
            messagebox.showwarning("请先确认角色定妆", "以下角色尚无已确认的参考图：\n" + "、".join(sorted(missing)))
            return
        if missing_scenes:
            messagebox.showwarning("请先确认场景定景", "以下已绑定场景尚无已确认的参考图，或已从场景库删除：\n" + "、".join(sorted(missing_scenes)))
            return
        if confirm_batch and len(indices) > 1 and not messagebox.askyesno("批量生成分镜", f"将使用 {shot_model_label} 按顺序生成 {len(indices)} 张分镜图，可能产生火山方舟 API 费用。是否继续？"):
            return
        art_style = comic["art_style"]
        aspect = comic["aspect"]
        resolution = str(comic.get("resolution", "2K"))
        optimize_mode = str(comic.get("optimize_mode", "standard"))
        output_dir = self._comic_output_dir() / "shots"
        self.is_busy = True
        self.comic_progress["value"] = 0

        def worker() -> None:
            completed = 0
            failed = 0
            total = len(indices)
            for position, index in enumerate(indices, start=1):
                shot = shots[index]
                selected = self._comic_reference_characters(shot, characters)
                selected_scene = self._comic_reference_scene(shot, scenes)
                prompt_shot = dict(shot)
                prompt_shot["characters"] = [str(item.get("name", "")) for item in selected]
                prompt = compose_shot_prompt(prompt_shot, art_style=art_style, aspect=aspect, characters=characters, scenes=scenes)
                references = character_reference_data(selected) + scene_reference_data(selected_scene)
                destination = output_dir / f"{index + 1:03d}_{safe_filename(str(shot.get('title', '分镜')))}.png"
                self.bus.put(("comic_shot_started", (index, position, total, prompt, shot_model_label)))
                try:
                    def progress(task: dict[str, object], shot_index: int = index) -> None:
                        self.bus.put(("comic_shot_progress", (shot_index, str(task.get("progress", "")), str(task.get("id", "")))))

                    result = client.generate_image(
                        prompt,
                        images=references,
                        size=resolution,
                        aspect=aspect,
                        optimize_mode=optimize_mode,
                        progress=progress,
                    )
                    client.download_image(str(result["imageUrl"]), destination)
                    self.bus.put(("comic_shot_done", (index, result, str(destination), prompt)))
                    completed += 1
                except Exception as exc:
                    self.bus.put(("comic_shot_error", (index, str(exc))))
                    failed += 1
            self.bus.put(("comic_batch_done", (completed, failed, total)))

        threading.Thread(target=worker, daemon=True).start()

    def generate_selected_comic_shot(self) -> None:
        self.save_comic_shot_prompt(silent=True)
        if self.current_comic_shot_index is None:
            messagebox.showinfo("没有分镜", "请先选择一个分镜。")
            return
        self._generate_comic_shots([self.current_comic_shot_index])

    def redraw_selected_comic_shots(self) -> None:
        """Regenerate exactly the rows selected in the batch image table."""
        self.save_comic_shot_prompt(silent=True)
        indices = self._selected_comic_shot_indices()
        if not indices:
            messagebox.showinfo("没有选中镜头", "请直接单击列表中的镜头行进行多选，不需要按 Ctrl。")
            return
        shots = self.state["comic"].get("shots", [])
        existing = sum(
            1
            for index in indices
            if shots[index].get("local_path") or shots[index].get("image_url")
        )
        model_label = self.comic_shot_model_var.get().strip() or "当前模型"
        resolution = self.comic_resolution_var.get().strip() or "2K"
        message = (
            f"将使用 {model_label}、{resolution} 重新绘制选中的 {len(indices)} 个镜头。\n\n"
            f"其中 {existing} 个镜头已有图片，生成成功后会替换对应原图；"
            "如果某个镜头生成失败，其原图片文件仍会保留。\n\n"
            "本操作会产生火山方舟 API 费用，是否继续？"
        )
        if not messagebox.askyesno("重新绘制已选镜头", message):
            return
        self._generate_comic_shots(indices, confirm_batch=False)

    def generate_all_comic_shots(self) -> None:
        self.save_comic_shot_prompt(silent=True)
        shots = self.state["comic"]["shots"]
        if not shots:
            messagebox.showinfo("没有分镜", "请先分析小说或进行本地拆分。")
            return
        indices = [index for index, shot in enumerate(shots) if not shot.get("local_path") or shot.get("error")]
        if not indices:
            if not messagebox.askyesno("重新生成", "所有分镜都已有图片。是否全部重新生成？"):
                return
            indices = list(range(len(shots)))
        self._generate_comic_shots(indices)

    def _show_comic_image(self, path: str, title: str) -> None:
        if not path or not Path(path).is_file():
            messagebox.showinfo("没有图片", "当前项目还没有可预览的本地图片。")
            return
        dialog = Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("980x860")
        dialog.minsize(720, 620)
        dialog.configure(bg=BG)
        try:
            with Image.open(path) as source:
                original = ImageOps.exif_transpose(source).convert("RGBA")
        except (OSError, ValueError):
            Label(dialog, text=f"系统预览器可打开此图片：\n{path}", bg=BG, fg=INK, wraplength=680, font=("Microsoft YaHei UI", 11)).pack(fill=BOTH, expand=True, padx=24, pady=24)
            return

        toolbar = Frame(dialog, bg=BG)
        toolbar.pack(fill=X, padx=18, pady=(16, 8))
        Label(toolbar, text=title, bg=BG, fg=INK, font=("Microsoft YaHei UI", 12, "bold")).pack(side=LEFT)
        Label(toolbar, text="滚轮缩放 · 按住鼠标左键拖动画面", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side=LEFT, padx=(12, 0))
        viewport_outer = self._card(dialog, bg="#111A22", padx=0, pady=0)
        viewport_outer.pack(fill=BOTH, expand=True, padx=18, pady=(0, 18))
        viewport_host = viewport_outer.winfo_children()[0]
        viewport = Canvas(viewport_host, bg="#111A22", highlightthickness=0, borderwidth=0, cursor="fleur")
        viewport.pack(fill=BOTH, expand=True)
        state = {"scale": 1.0, "fit": 1.0}

        def render(*, keep_center: bool = True) -> None:
            viewport.update_idletasks()
            available_width = max(120, viewport.winfo_width() - 24)
            available_height = max(120, viewport.winfo_height() - 24)
            state["fit"] = min(1.0, available_width / original.width, available_height / original.height)
            scale = max(0.08, min(float(state["scale"]), 4.0))
            target = (max(1, int(original.width * scale)), max(1, int(original.height * scale)))
            resized = original.resize(target, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(resized, master=viewport)
            viewport.delete("all")
            canvas_width = max(viewport.winfo_width(), target[0] + 24)
            canvas_height = max(viewport.winfo_height(), target[1] + 24)
            viewport.configure(scrollregion=(0, 0, canvas_width, canvas_height))
            viewport.create_image(canvas_width / 2, canvas_height / 2, image=photo, anchor="center")
            viewport._preview_photo = photo
            self.comic_preview_image = photo
            if keep_center:
                viewport.xview_moveto(max(0.0, (canvas_width - viewport.winfo_width()) / max(canvas_width, 1) / 2))
                viewport.yview_moveto(max(0.0, (canvas_height - viewport.winfo_height()) / max(canvas_height, 1) / 2))

        def zoom(multiplier: float) -> None:
            state["scale"] = max(0.08, min(4.0, float(state["scale"]) * multiplier))
            render()

        def fit_image() -> None:
            viewport.update_idletasks()
            state["scale"] = min(1.0, max(0.08, (viewport.winfo_width() - 24) / original.width), max(0.08, (viewport.winfo_height() - 24) / original.height))
            render()

        self._button(toolbar, "+ 放大", lambda: zoom(1.25), kind="primary").pack(side=RIGHT)
        self._button(toolbar, "－ 缩小", lambda: zoom(0.8), kind="ghost").pack(side=RIGHT, padx=(0, 7))
        self._button(toolbar, "适应窗口", fit_image, kind="ghost").pack(side=RIGHT, padx=(0, 7))
        viewport.bind("<ButtonPress-1>", lambda event: viewport.scan_mark(event.x, event.y))
        viewport.bind("<B1-Motion>", lambda event: viewport.scan_dragto(event.x, event.y, gain=1))

        def wheel_zoom(event):
            zoom(1.12 if event.delta > 0 else 0.89)
            return "break"

        viewport.bind("<MouseWheel>", wheel_zoom)
        dialog.after(80, fit_image)

    def preview_comic_character(self) -> None:
        index = self.current_comic_character_index
        if index is None:
            messagebox.showinfo("没有角色", "请先选择角色。")
            return
        character = self.state["comic"]["characters"][index]
        candidate = str(character.get("candidate_path", ""))
        has_candidate = bool(candidate and Path(candidate).is_file())
        path = candidate if has_candidate else str(character.get("local_path") or "")
        title_kind = "候选定妆" if has_candidate else "已确认参考"
        self._show_comic_image(path, f"{title_kind} · {character.get('name', '')}")

    def preview_comic_shot(self) -> None:
        index = self.current_comic_shot_index
        if index is None:
            messagebox.showinfo("没有分镜", "请先选择分镜。")
            return
        shot = self.state["comic"]["shots"][index]
        self._show_comic_image(str(shot.get("local_path", "")), str(shot.get("title", "分镜预览")))

    def _refresh_comic_video_labels(self) -> None:
        comic = self.state["comic"]
        audio = str(comic.get("audio_path", ""))
        subtitles = str(comic.get("subtitles_path", ""))
        output = str(comic.get("jianying_draft_path", ""))
        duration = float(comic.get("audio_duration", 0.0) or 0.0)
        if self.comic_video_audio_label:
            self.comic_video_audio_label.configure(text=f"{Path(audio).name} · {duration:.1f} 秒" if audio else "尚未导入配音音频")
        if self.comic_video_subtitle_label:
            if subtitles:
                try:
                    cue_count = len(load_srt(subtitles)) if Path(subtitles).is_file() else 0
                except OSError:
                    cue_count = 0
                self.comic_video_subtitle_label.configure(text=f"{Path(subtitles).name} · {cue_count} 条字幕")
            else:
                self.comic_video_subtitle_label.configure(text="未导入字幕；仍可生成无字幕草稿")
        if self.comic_video_result_label:
            images = sum(1 for shot in comic.get("shots", []) if Path(str(shot.get("local_path", ""))).is_file())
            total = len(comic.get("shots", []))
            result_text = f"分镜图片：{images}/{total}\n音频：{'已导入' if audio else '未导入'}\n字幕：{'已导入' if subtitles else '未导入'}\n成片效果：{normalize_motion_mode(comic.get('motion_mode'))}"
            if output and Path(output).is_dir():
                result_text += f"\n\n已生成草稿：{comic.get('jianying_draft_name') or Path(output).name}"
            self.comic_video_result_label.configure(text=result_text)

    def choose_comic_audio(self) -> None:
        source = filedialog.askopenfilename(title="导入漫画配音", filetypes=[("音频", "*.mp3 *.wav *.m4a *.aac *.flac"), ("所有文件", "*.*")])
        if not source:
            return
        destination = self._comic_output_dir() / "audio" / f"{safe_filename(Path(source).stem, 'voice')}{Path(source).suffix.lower()}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if Path(source).resolve() != destination.resolve():
                shutil.copy2(source, destination)
        except OSError as exc:
            messagebox.showerror("音频导入失败", str(exc))
            return
        path = str(destination)
        duration = probe_audio_duration(path) or 0.0
        if duration <= 0:
            settings = self.state["settings"]
            ffprobe = find_executable(str(settings.get("ffprobe_path", "")), "ffprobe")
            duration = probe_duration(path, ffprobe) or 0.0
        if duration <= 0:
            messagebox.showerror("无法读取音频", "无法读取音频时长，请检查文件，或在“模型与工具”中配置 FFprobe。")
            return
        comic = self.state["comic"]
        comic.update({"audio_path": path, "audio_duration": duration, "video_output_path": "", "jianying_draft_path": "", "jianying_draft_name": ""})
        self.comic_audio_var.set(path)
        self.comic_video_output_var.set("")
        self.comic_draft_output_var.set("")
        self.store.save(self.state)
        self._refresh_comic_video_labels()
        self._refresh_comic_overview()

    def choose_comic_subtitles(self) -> None:
        source = filedialog.askopenfilename(title="导入漫画字幕", filetypes=[("SRT 字幕", "*.srt"), ("所有文件", "*.*")])
        if not source:
            return
        destination = self._comic_output_dir() / "audio" / f"{safe_filename(Path(source).stem, 'subtitles')}.srt"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if Path(source).resolve() != destination.resolve():
                shutil.copy2(source, destination)
        except OSError as exc:
            messagebox.showerror("字幕导入失败", str(exc))
            return
        path = str(destination)
        try:
            cues = load_srt(path)
        except OSError as exc:
            messagebox.showerror("字幕读取失败", str(exc))
            return
        if not cues:
            messagebox.showerror("字幕格式无效", "没有识别到有效的 SRT 时间轴。")
            return
        self.state["comic"].update({"subtitles_path": path, "video_output_path": "", "jianying_draft_path": "", "jianying_draft_name": ""})
        self.comic_subtitles_var.set(path)
        self.comic_video_output_var.set("")
        self.comic_draft_output_var.set("")
        self.store.save(self.state)
        self._refresh_comic_video_labels()
        self._refresh_comic_overview()

    def generate_comic_draft(self, open_after: bool = False) -> None:
        if self.is_busy:
            messagebox.showinfo("任务进行中", "当前任务尚未结束，请等待右侧进度完成后再生成剪映草稿。")
            return
        self._sync_comic_state()
        comic = self.state["comic"]
        shots = list(comic.get("shots", []))
        if not shots:
            messagebox.showinfo("没有分镜", "请先生成静态漫画分镜和图片。")
            return
        image_paths = [str(shot.get("local_path", "")) for shot in shots]
        missing = [shots[index].get("title", f"分镜 {index + 1:02d}") for index, path in enumerate(image_paths) if not Path(path).is_file()]
        if missing:
            messagebox.showwarning("分镜图片未完成", "请先生成以下分镜图片：\n" + "、".join(str(item) for item in missing[:12]))
            return
        audio_path = self.comic_audio_var.get().strip()
        if not Path(audio_path).is_file():
            messagebox.showwarning("需要配音音频", "请先导入配音音频。")
            return
        duration = float(comic.get("audio_duration", 0.0) or 0.0)
        if duration <= 0:
            duration = probe_audio_duration(audio_path) or 0.0
        if duration <= 0:
            ffprobe = find_executable(str(self.state["settings"].get("ffprobe_path", "")), "ffprobe")
            duration = probe_duration(audio_path, ffprobe) or 0.0
        if duration <= 0:
            messagebox.showerror("无法读取音频", "无法确定音频时长，请检查音频文件后重试。")
            return
        subtitles_path = self.comic_subtitles_var.get().strip()
        try:
            cues = load_srt(subtitles_path) if subtitles_path and Path(subtitles_path).is_file() else []
            shot_texts = [str(shot.get("source", "") or shot.get("narration", "")) for shot in shots]
            durations = allocate_shot_durations(duration, len(image_paths), cues, shot_texts)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法准备草稿", str(exc))
            return

        settings = self.state["settings"]
        drafts_path = detect_jianying_drafts_path(str(settings.get("jianying_drafts_path", "")))
        executable = detect_jianying_executable(str(settings.get("jianying_exe", "")))
        if not drafts_path:
            messagebox.showwarning("需要剪映草稿目录", "没有找到剪映草稿目录，请在“模型与工具”中选择后重试。")
            self.navigate("settings")
            return
        if open_after and not executable:
            messagebox.showwarning("未找到剪映", "没有找到剪映专业版程序，请在“模型与工具”中选择 JianyingPro.exe。")
            self.navigate("settings")
            return
        settings["jianying_drafts_path"] = drafts_path
        if executable:
            settings["jianying_exe"] = executable
        self.store.save(self.state)

        self.is_busy = True
        self.open_jianying_after_draft = bool(open_after)
        self.comic_progress["value"] = 1
        if self.comic_draft_progress:
            self.comic_draft_progress["value"] = 1
        if self.comic_draft_status_label:
            self.comic_draft_status_label.configure(text="1% · 正在准备剪映草稿", fg=SIDEBAR_MUTED)
        self.comic_status.configure(text="正在准备可编辑剪映草稿…", fg=ACCENT_DARK)
        requested_name = f"{safe_filename(str(comic.get('project_name', '漫画推文')))}_静态漫"
        motion_mode = self.comic_motion_var.get()

        def worker() -> None:
            try:
                def progress(value: float, detail: str) -> None:
                    self.bus.put(("comic_draft_progress", (value, detail)))

                result = create_comic_jianying_draft(
                    image_paths,
                    durations,
                    audio_path=audio_path,
                    subtitles_path=subtitles_path,
                    aspect=str(comic.get("aspect", "9:16")),
                    motion_mode=motion_mode,
                    drafts_root=drafts_path,
                    requested_name=requested_name,
                    fps=30,
                    on_progress=progress,
                )
                self.bus.put(("comic_draft_done", (result, executable or "")))
            except Exception as exc:
                self.bus.put(("comic_draft_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def open_comic_draft_in_jianying(self) -> None:
        comic = self.state["comic"]
        path = str(comic.get("jianying_draft_path", ""))
        if not path or not Path(path).is_dir():
            messagebox.showinfo("没有剪映草稿", "请先生成剪映草稿。")
            return
        settings = self.state["settings"]
        executable = detect_jianying_executable(str(settings.get("jianying_exe", "")))
        if not executable:
            messagebox.showwarning("未找到剪映", "没有找到剪映专业版程序，请在“模型与工具”中选择 JianyingPro.exe。")
            return
        settings["jianying_exe"] = executable
        self.store.save(self.state)
        try:
            open_jianying(executable)
        except JianyingEngineError as exc:
            messagebox.showerror("无法打开剪映", str(exc))
            return
        draft_name = str(comic.get("jianying_draft_name", "")) or Path(path).name
        self._copy_text(draft_name)
        messagebox.showinfo(
            "剪映已打开",
            f"可编辑草稿“{draft_name}”已写入剪映本地草稿列表，并已复制草稿名。\n\n"
            "剪映 11 暂不支持外部程序直接跳入指定草稿；请在本地草稿列表中打开它。若列表尚未刷新，请重启一次剪映。",
        )

    def open_comic_draft_directory(self) -> None:
        path = str(self.state["comic"].get("jianying_draft_path", ""))
        if not path or not Path(path).is_dir():
            path = detect_jianying_drafts_path(str(self.state["settings"].get("jianying_drafts_path", ""))) or ""
        if not path or not Path(path).is_dir():
            messagebox.showinfo("没有草稿目录", "请先生成剪映草稿，或在“模型与工具”中选择草稿目录。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                __import__("subprocess").Popen(["/usr/bin/open", path], close_fds=True)
            else:
                __import__("subprocess").Popen(["xdg-open", path], close_fds=True)
        except OSError as exc:
            messagebox.showerror("无法打开草稿目录", str(exc))

    def generate_comic_video(self, open_jianying_after: bool = False) -> None:
        if self.is_busy:
            return
        self._sync_comic_state()
        comic = self.state["comic"]
        shots = list(comic.get("shots", []))
        if not shots:
            messagebox.showinfo("没有分镜", "请先生成静态漫画分镜和图片。")
            return
        image_paths = [str(shot.get("local_path", "")) for shot in shots]
        missing = [shots[index].get("title", f"分镜 {index + 1:02d}") for index, path in enumerate(image_paths) if not Path(path).is_file()]
        if missing:
            messagebox.showwarning("分镜图片未完成", "请先生成以下分镜图片：\n" + "、".join(str(item) for item in missing[:12]))
            return
        audio_path = self.comic_audio_var.get().strip()
        if not Path(audio_path).is_file():
            messagebox.showwarning("需要配音音频", "请先导入配音音频。")
            return
        settings = self.state["settings"]
        ffmpeg = find_executable(str(settings.get("ffmpeg_path", "")), "ffmpeg")
        if not ffmpeg:
            messagebox.showerror("缺少 FFmpeg", "请先在“模型与工具”中配置 FFmpeg，才能把静态分镜合成为视频。")
            return
        duration = float(comic.get("audio_duration", 0.0) or 0.0)
        if duration <= 0:
            ffprobe = find_executable(str(settings.get("ffprobe_path", "")), "ffprobe")
            duration = probe_duration(audio_path, ffprobe) or 0.0
        if duration <= 0:
            messagebox.showerror("无法读取音频", "无法确定音频时长，请配置 FFprobe 后重试。")
            return
        subtitles_path = self.comic_subtitles_var.get().strip()
        cues = load_srt(subtitles_path) if subtitles_path and Path(subtitles_path).is_file() else []
        try:
            shot_texts = [str(shot.get("source", "") or shot.get("narration", "")) for shot in shots]
            durations = allocate_shot_durations(duration, len(image_paths), cues, shot_texts)
            output = self._comic_output_dir() / f"{safe_filename(str(comic.get('project_name', '漫画推文')))}_静态漫.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            command = build_comic_video_command(
                image_paths,
                durations,
                audio_path=audio_path,
                subtitles_path=subtitles_path,
                aspect=str(comic.get("aspect", "9:16")),
                motion_mode=self.comic_motion_var.get(),
                ffmpeg_path=ffmpeg,
                output_path=str(output),
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法生成视频", str(exc))
            return
        self.is_busy = True
        self.open_jianying_after_video = bool(open_jianying_after)
        self.comic_progress["value"] = 0
        self.comic_status.configure(text="正在合成静态分镜、上下关键帧、配音和字幕…", fg=ACCENT_DARK)

        def worker() -> None:
            try:
                def progress(value: float, detail: str) -> None:
                    self.bus.put(("comic_video_progress", (value, detail)))

                run_export(command, duration, progress)
                self.bus.put(("comic_video_done", (str(output), duration)))
            except Exception as exc:
                self.bus.put(("comic_video_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def open_comic_video(self) -> None:
        path = self.comic_video_output_var.get().strip() or str(self.state["comic"].get("video_output_path", ""))
        if not Path(path).is_file():
            messagebox.showinfo("没有视频", "请先生成静态漫视频。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                __import__("subprocess").Popen(["/usr/bin/open", path], close_fds=True)
            else:
                __import__("subprocess").Popen(["xdg-open", path], close_fds=True)
        except OSError as exc:
            messagebox.showerror("无法播放视频", str(exc))

    def open_comic_video_in_jianying(self) -> None:
        path = self.comic_video_output_var.get().strip() or str(self.state["comic"].get("video_output_path", ""))
        if not Path(path).is_file():
            messagebox.showinfo("没有视频", "请先生成静态漫视频，再交给剪映继续编辑。")
            return
        settings = self.state["settings"]
        configured = self.jianying_var.get().strip() if hasattr(self, "jianying_var") else str(settings.get("jianying_exe", ""))
        executable = detect_jianying_executable(configured)
        if not executable:
            messagebox.showwarning("未找到剪映", "没有找到剪映专业版程序。请在“模型与工具”中选择 JianyingPro.exe，然后重试。")
            return
        settings["jianying_exe"] = executable
        self.store.save(self.state)
        try:
            open_jianying(executable)
            self._copy_text(path)
            if sys.platform == "win32":
                __import__("subprocess").Popen(["explorer.exe", "/select,", str(Path(path).resolve())], close_fds=True)
        except (JianyingEngineError, OSError) as exc:
            messagebox.showerror("无法打开剪映", str(exc))
            return
        messagebox.showinfo(
            "已打开剪映",
            "剪映已启动，并已在文件管理器中定位生成的视频。视频路径也已复制到剪贴板；如果剪映没有自动导入，直接拖入素材区即可。",
        )

    def open_comic_output_dir(self) -> None:
        path = self._comic_output_dir()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                __import__("subprocess").Popen(["/usr/bin/open", str(path)], close_fds=True)
            else:
                __import__("subprocess").Popen(["xdg-open", str(path)], close_fds=True)
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc))

    @staticmethod
    def _percent_value(value: str) -> float:
        match = __import__("re").search(r"(\d+(?:\.\d+)?)", value or "")
        return min(max(float(match.group(1)), 0), 100) if match else 0

    def _handle_comic_bus(self, event: str, payload: object) -> None:
        if event == "comic_api_ok":
            self.is_busy = False
            self.save_comic_settings(silent=True)
            self._set_comic_api_status("● 连接成功", ACCENT_DARK)
        elif event == "comic_api_error":
            self.is_busy = False
            self._set_comic_api_status("● 连接失败", ERROR)
            messagebox.showerror("火山方舟连接失败", str(payload))
        elif event == "comic_cover_item_started":
            position, total, aspect, ordinal, final_prompt = payload
            cover = self._comic_cover_record()
            item = self._comic_cover_image_record(aspect, ordinal)
            item.update({"status": "提交中", "progress": "0%", "error": "", "final_prompt": final_prompt})
            cover.update({"status": "生成中", "progress": f"{position - 1}/{total}", "error": ""})
            self.comic_status.configure(text=f"正在生成封面 {position}/{total}：{aspect} 第 {ordinal} 张", fg=ACCENT_DARK)
            self._refresh_comic_cover_widgets()
        elif event == "comic_cover_progress":
            position, total, aspect, ordinal, progress, task_id = payload
            cover = self._comic_cover_record()
            item = self._comic_cover_image_record(aspect, ordinal)
            item.update({"status": "生成中", "progress": progress or "排队中"})
            if task_id:
                item["task_id"] = task_id
                cover["task_id"] = task_id
            item_percent = self._percent_value(progress)
            overall_percent = ((position - 1) + item_percent / 100) / total * 100
            cover["progress"] = f"{overall_percent:.0f}%"
            self.comic_progress["value"] = overall_percent
            self.comic_status.configure(
                text=f"正在生成封面 {position}/{total}：{aspect} 第 {ordinal} 张 · {progress or '排队中'}",
                fg=ACCENT_DARK,
            )
            self._refresh_comic_cover_widgets()
        elif event == "comic_cover_item_done":
            aspect, ordinal, result, local_path, final_prompt = payload
            cover = self._comic_cover_record()
            item = self._comic_cover_image_record(aspect, ordinal)
            item.update(
                {
                    "task_id": str(result.get("id", "")),
                    "image_url": str(result.get("imageUrl", "")),
                    "local_path": local_path,
                    "status": "已完成",
                    "progress": "100%",
                    "error": "",
                    "final_prompt": final_prompt,
                    "image_model": str(result.get("model", "")),
                }
            )
            if (aspect, ordinal) == COMIC_COVER_OUTPUT_PLAN[0] or not str(cover.get("local_path", "")):
                cover.update(
                    {
                        "task_id": item["task_id"],
                        "image_url": item["image_url"],
                        "local_path": local_path,
                        "final_prompt": final_prompt,
                        "image_model": item["image_model"],
                    }
                )
            self.store.save(self.state)
            self._refresh_comic_cover_widgets()
            self._refresh_comic_overview()
        elif event == "comic_cover_item_error":
            aspect, ordinal, error = payload
            cover = self._comic_cover_record()
            item = self._comic_cover_image_record(aspect, ordinal)
            item.update({"status": "失败，可重试", "progress": "0%", "error": error})
            cover["error"] = error
            self.store.save(self.state)
            self._refresh_comic_cover_widgets()
        elif event == "comic_cover_batch_done":
            completed, failed, total = payload
            cover = self._comic_cover_record()
            self.is_busy = False
            if completed == total:
                cover.update({"status": "已完成", "progress": "100%", "error": ""})
                self.comic_progress["value"] = 100
                self.comic_status.configure(text=f"四张项目封面已全部生成：{cover.get('title', '漫画推文')}", fg=ACCENT_DARK)
            elif completed:
                cover.update({"status": "部分完成", "progress": f"{completed}/{total}"})
                self.comic_progress["value"] = completed / total * 100
                self.comic_status.configure(text=f"封面已生成 {completed}/{total} 张，{failed} 张失败", fg=ERROR)
            else:
                cover.update({"status": "生成失败", "progress": "0%"})
                self.comic_progress["value"] = 0
                self.comic_status.configure(text="四张封面均生成失败，可重新生成", fg=ERROR)
            self.store.save(self.state)
            self._refresh_comic_cover_widgets()
            self._refresh_comic_overview()
            if failed:
                messagebox.showwarning("封面生成未全部完成", f"已生成 {completed}/{total} 张，失败 {failed} 张。可再次点击“生成 / 重绘四张封面”重试。")
        elif event == "comic_character_progress":
            index, progress, task_id = payload
            character = self.state["comic"]["characters"][index]
            character["status"] = f"生成中 {progress or '排队中'}"
            if task_id:
                character["task_id"] = task_id
            self.comic_progress["value"] = self._percent_value(progress)
            self._refresh_comic_character_list(index)
        elif event == "comic_character_done":
            index, result, local_path = payload
            character = self.state["comic"]["characters"][index]
            character.update({"task_id": str(result.get("id", "")), "candidate_image_url": str(result.get("imageUrl", "")), "candidate_path": local_path, "status": "候选待确认"})
            self.store.save(self.state)
            self.is_busy = False
            self.comic_progress["value"] = 100
            self._refresh_comic_character_list(index)
            self._refresh_comic_overview()
            self.comic_status.configure(text=f"{character.get('name', '角色')} 的候选定妆已生成。请预览并确认保留后再用于分镜。", fg=WARM)
        elif event == "comic_character_error":
            index, error = payload
            self.state["comic"]["characters"][index].update({"status": "生成失败"})
            self.store.save(self.state)
            self.is_busy = False
            self.comic_progress["value"] = 0
            self._refresh_comic_character_list(index)
            messagebox.showerror("角色定妆生成失败", str(error))
        elif event == "comic_scene_progress":
            index, progress, task_id = payload
            scene = self.state["comic"]["scenes"][index]
            scene["status"] = f"生成中 {progress or '排队中'}"
            if task_id:
                scene["task_id"] = task_id
            self.comic_progress["value"] = self._percent_value(progress)
            self._refresh_comic_scene_list(index)
        elif event == "comic_scene_done":
            index, result, local_path = payload
            scene = self.state["comic"]["scenes"][index]
            scene.update({"task_id": str(result.get("id", "")), "candidate_image_url": str(result.get("imageUrl", "")), "candidate_path": local_path, "status": "候选待确认"})
            self.store.save(self.state)
            self.is_busy = False
            self.comic_progress["value"] = 100
            self._refresh_comic_scene_list(index)
            self._refresh_comic_overview()
            self.comic_status.configure(text=f"{scene.get('name', '场景')} 的候选定景图已生成。请预览并确认保留后再用于分镜。", fg=WARM)
        elif event == "comic_scene_error":
            index, error = payload
            self.state["comic"]["scenes"][index].update({"status": "生成失败"})
            self.store.save(self.state)
            self.is_busy = False
            self.comic_progress["value"] = 0
            self._refresh_comic_scene_list(index)
            messagebox.showerror("场景定景生成失败", str(error))
        elif event == "comic_analysis_retry":
            batch_index, batch_total, error = payload
            self.comic_status.configure(text=f"第 {batch_index}/{batch_total} 批校验未通过，正在自动重试… {error}", fg=WARM)
        elif event == "comic_analysis_split":
            batch_id, source_chars, error = payload
            self.comic_status.configure(text=f"{batch_id} 连续失败，已自动把约 {source_chars} 字原文拆成更小请求继续处理。原因：{error}", fg=WARM)
        elif event == "comic_analysis_progress":
            completed_chars, total_chars, mode_label = payload
            progress = min(99, completed_chars / max(total_chars, 1) * 100)
            self.comic_progress["value"] = progress
            self.comic_status.configure(text=f"{mode_label}：已分析并校验约 {progress:.0f}% 原文", fg=ACCENT_DARK)
        elif event == "comic_analysis_error":
            batch_index, batch_total, error = payload
            self.is_busy = False
            self.comic_progress["value"] = 0
            self.comic_status.configure(text=f"AI 生成失败：{error}。原有结果未被替换。", fg=ERROR)
            messagebox.showerror("AI 分批生成失败", f"请求 {batch_index}/{batch_total} 未通过：\n{error}\n\n系统已重试并自动缩小批次；原有结果已完整保留。")
        elif event == "comic_analysis_done":
            result, note, generation_mode = payload
            comic = self.state["comic"]
            if generation_mode in {"characters", "all"}:
                merged_characters = self._merge_comic_characters([dict(item) for item in self.state.get("shared_characters", [])], [dict(item) for item in result.get("characters", [])])
                shared = self.state.setdefault("shared_characters", [])
                shared[:] = merged_characters
                comic["characters"] = shared
                for project in self.state.get("projects", []):
                    if isinstance(project, dict):
                        project["characters"] = shared
            if generation_mode in {"scenes", "all"}:
                comic["scenes"] = self._merge_comic_scenes([dict(item) for item in comic.get("scenes", [])], [dict(item) for item in result.get("scenes", [])])
            if generation_mode in {"shots", "all"}:
                comic["shots"] = [dict(item) for item in result.get("shots", [])]
                comic["video_output_path"] = ""
                comic["jianying_draft_path"] = ""
                comic["jianying_draft_name"] = ""
                self.comic_video_output_var.set("")
                self.comic_draft_output_var.set("")
            self.store.save(self.state)
            self.is_busy = False
            self.comic_progress["value"] = 100
            self._refresh_comic_character_list(0 if comic["characters"] else None)
            self._refresh_comic_scene_list(0 if comic["scenes"] else None)
            self._refresh_comic_shot_tree(0 if comic["shots"] else None)
            self._refresh_comic_overview()
            self.comic_status.configure(text=f"{note} 共 {len(comic['characters'])} 个角色、{len(comic['scenes'])} 个固定场景、{len(comic['shots'])} 个分镜。", fg=ACCENT_DARK)
        elif event == "comic_shot_started":
            index, position, total, prompt, model_label = payload
            shot = self.state["comic"]["shots"][index]
            shot.update({"status": "提交中", "progress": "0%", "error": "", "final_prompt": prompt})
            self.comic_status.configure(text=f"正在使用 {model_label} 生成 {position}/{total}：{shot.get('title', '')}", fg=ACCENT_DARK)
            self._update_comic_shot_tree_row(index)
            if self.current_comic_shot_index == index:
                self._load_comic_shot(index)
        elif event == "comic_shot_progress":
            index, progress, task_id = payload
            shot = self.state["comic"]["shots"][index]
            shot.update({"status": "生成中", "progress": progress or "排队中"})
            if task_id:
                shot["task_id"] = task_id
            self.comic_progress["value"] = self._percent_value(progress)
            self._update_comic_shot_tree_row(index)
            if self.current_comic_shot_index == index:
                self._load_comic_shot(index)
        elif event == "comic_shot_done":
            index, result, local_path, prompt = payload
            shot = self.state["comic"]["shots"][index]
            shot.update({"task_id": str(result.get("id", "")), "image_url": str(result.get("imageUrl", "")), "local_path": local_path, "status": "已完成", "progress": "100%", "error": "", "final_prompt": prompt, "image_model": str(result.get("model", ""))})
            self.state["comic"]["video_output_path"] = ""
            self.state["comic"]["jianying_draft_path"] = ""
            self.state["comic"]["jianying_draft_name"] = ""
            self.comic_video_output_var.set("")
            self.comic_draft_output_var.set("")
            self.store.save(self.state)
            self._update_comic_shot_tree_row(index)
            if self.current_comic_shot_index == index:
                self._load_comic_shot(index)
            self._refresh_comic_overview()
        elif event == "comic_shot_error":
            index, error = payload
            self.state["comic"]["shots"][index].update({"status": "失败，可重试", "error": error})
            self.store.save(self.state)
            self._update_comic_shot_tree_row(index)
            if self.current_comic_shot_index == index:
                self._load_comic_shot(index)
        elif event == "comic_batch_done":
            completed, failed, total = payload
            self.is_busy = False
            self.comic_progress["value"] = 100 if completed else 0
            self._refresh_comic_shot_tree(self.current_comic_shot_index)
            self._refresh_comic_overview()
            self.comic_status.configure(text=f"生成结束：成功 {completed}，失败 {failed}，共 {total} 个分镜。失败项可直接再次批量生成。", fg=ACCENT_DARK if not failed else WARM)
            if failed:
                messagebox.showwarning("批量生成结束", f"成功 {completed} 张，失败 {failed} 张。失败原因已保存在对应分镜中，可再次点击批量生成重试。")
        elif event == "comic_draft_progress":
            value, detail = payload
            percent = min(max(float(value) * 100, 0), 100)
            self.comic_progress["value"] = percent
            if self.comic_draft_progress:
                self.comic_draft_progress["value"] = percent
            if self.comic_draft_status_label:
                self.comic_draft_status_label.configure(text=f"{percent:.0f}% · {detail}", fg=SIDEBAR_MUTED)
            self.comic_status.configure(text=f"正在生成剪映草稿… {percent:.0f}% · {detail}", fg=ACCENT_DARK)
        elif event == "comic_draft_done":
            result, executable = payload
            self.is_busy = False
            comic = self.state["comic"]
            comic.update(
                {
                    "jianying_draft_path": result.path,
                    "jianying_draft_name": result.name,
                    "audio_duration": result.duration_seconds,
                    "motion_mode": self.comic_motion_var.get(),
                }
            )
            self.comic_draft_output_var.set(result.path)
            self.comic_progress["value"] = 100
            if self.comic_draft_progress:
                self.comic_draft_progress["value"] = 100
            if self.comic_draft_status_label:
                self.comic_draft_status_label.configure(text=f"100% · 草稿已生成：{result.name}", fg=ACCENT)
            self.store.save(self.state)
            self._refresh_comic_video_labels()
            self._refresh_comic_overview()
            self.comic_status.configure(text=f"剪映草稿已生成：{result.name}", fg=ACCENT_DARK)
            should_open = self.open_jianying_after_draft
            self.open_jianying_after_draft = False
            if should_open and executable:
                self.open_comic_draft_in_jianying()
            else:
                messagebox.showinfo("剪映草稿生成完成", f"可编辑草稿已保存：\n{result.path}")
        elif event == "comic_draft_error":
            self.is_busy = False
            self.open_jianying_after_draft = False
            self.comic_progress["value"] = 0
            if self.comic_draft_progress:
                self.comic_draft_progress["value"] = 0
            if self.comic_draft_status_label:
                self.comic_draft_status_label.configure(text="生成失败", fg=ERROR)
            self.comic_status.configure(text="剪映草稿生成失败。", fg=ERROR)
            messagebox.showerror("剪映草稿生成失败", str(payload))
        elif event == "comic_video_progress":
            value, _detail = payload
            self.comic_progress["value"] = min(max(float(value) * 100, 0), 100)
            self.comic_status.configure(text=f"正在生成静态漫视频… {float(value) * 100:.0f}%", fg=ACCENT_DARK)
        elif event == "comic_video_done":
            output, duration = payload
            self.is_busy = False
            self.state["comic"].update({"video_output_path": output, "audio_duration": duration, "motion_mode": self.comic_motion_var.get()})
            self.comic_video_output_var.set(output)
            self.comic_progress["value"] = 100
            self.store.save(self.state)
            self._refresh_comic_video_labels()
            self._refresh_comic_overview()
            self.comic_status.configure(text=f"静态漫视频已生成：{Path(output).name}", fg=ACCENT_DARK)
            should_open_jianying = self.open_jianying_after_video
            self.open_jianying_after_video = False
            if should_open_jianying:
                self.open_comic_video_in_jianying()
            else:
                messagebox.showinfo("视频生成完成", f"静态漫视频已保存到：\n{output}")
        elif event == "comic_video_error":
            self.is_busy = False
            self.open_jianying_after_video = False
            self.comic_progress["value"] = 0
            self.comic_status.configure(text="静态漫视频生成失败。", fg=ERROR)
            messagebox.showerror("视频生成失败", str(payload))

    # ----------------------------- Settings page -----------------------------
    def show_settings(self) -> None:
        self._clear_main()
        self.navigate_highlight("settings")
        self._page_header("模型与工具", "可直接选择 DeepSeek、千问、智谱 GLM、Kimi；API Key 由系统安全保管。")
        body = Frame(self.main, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=34, pady=(0, 28))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        model_outer = self._card(body, padx=24, pady=22)
        model_outer.grid(row=0, column=0, sticky="new", padx=(0, 9))
        model = model_outer.winfo_children()[0]
        Label(model, text="AI 模型", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        Label(model, text="选择服务商后会自动填写官方接口与推荐模型，也可继续手动修改。", bg=SURFACE, fg=MUTED, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 18))
        settings = self.state["settings"]
        provider_id = settings.get("provider") or infer_provider(settings.get("base_url", ""), settings.get("model", ""))
        if provider_id not in {item.id for item in PROVIDER_PRESETS}:
            provider_id = "custom"
        self.active_api_provider = provider_id
        if not self.api_key.get().strip():
            self.api_key.set(self._load_provider_api_key(provider_id))
        self.provider_ids_by_label = {item.label: item.id for item in PROVIDER_PRESETS}
        self.provider_labels_by_id = {item.id: item.label for item in PROVIDER_PRESETS}
        self.provider_var = StringVar(value=self.provider_labels_by_id[provider_id])
        self.base_url_var = StringVar(value=settings["base_url"])
        self.model_name_var = StringVar(value=settings["model"])
        self._field_label(model, "模型服务商").pack(anchor="w", pady=(2, 5))
        provider_box = RoundedCombobox(
            model,
            textvariable=self.provider_var,
            values=[item.label for item in PROVIDER_PRESETS],
            state="readonly",
        )
        provider_box.pack(fill=X)
        provider_box.bind("<<ComboboxSelected>>", self._apply_provider_selection)
        self.provider_help = Label(model, bg=SURFACE, fg=ACCENT_DARK, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 8))
        self.provider_help.pack(anchor="w", pady=(6, 0))
        self._settings_entry(model, "Base URL", self.base_url_var)
        self._settings_entry(model, "模型名称", self.model_name_var)
        self._field_label(model, "API Key").pack(anchor="w", pady=(13, 5))
        key_entry = self._entry(model, self.api_key)
        key_entry.configure(show="•")
        key_entry.pack(fill=X, ipady=7)
        self.api_key_hint = Label(model, bg=SURFACE, fg=MUTED, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 8))
        self.api_key_hint.pack(anchor="w", pady=(5, 0))
        ttk.Checkbutton(
            model,
            text="安全记住 API Key（Windows 凭据管理器 / macOS 钥匙串）",
            variable=self.remember_api_key,
        ).pack(anchor="w", pady=(10, 0))
        self.ai_test_status = Label(model, text="尚未测试连接", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 8))
        self.ai_test_status.pack(anchor="w", pady=(12, 0))
        model_actions = Frame(model, bg=SURFACE)
        model_actions.pack(fill=X, pady=(14, 0))
        self._button(model_actions, "测试连接", self.test_ai_connection, kind="ghost").pack(side=LEFT)
        self._button(model_actions, "清除已保存 Key", self.clear_saved_api_key, kind="ghost").pack(side=LEFT, padx=(7, 0))
        self._button(model_actions, "保存模型设置", self.save_settings, kind="primary").pack(side=RIGHT)
        self._update_provider_help()
        self.bus_handler = self._handle_settings_event

        tool_outer = self._card(body, padx=24, pady=22)
        tool_outer.grid(row=0, column=1, sticky="new", padx=(9, 0))
        tool = tool_outer.winfo_children()[0]
        Label(tool, text="剪映草稿与视频工具", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        Label(tool, text="静态漫会直接生成可编辑剪映草稿；FFmpeg 仅用于可选的 MP4 预览导出。", bg=SURFACE, fg=MUTED, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 18))
        self.ffmpeg_var = StringVar(value=settings.get("ffmpeg_path", ""))
        self.ffprobe_var = StringVar(value=settings.get("ffprobe_path", ""))
        self.jianying_var = StringVar(value=settings.get("jianying_exe", "") or detect_jianying_executable("") or "")
        self.jianying_drafts_var = StringVar(value=settings.get("jianying_drafts_path", "") or detect_jianying_drafts_path("") or "")
        executable_suffix = "" if sys.platform == "darwin" else ".exe"
        self._path_field(tool, f"ffmpeg{executable_suffix}", self.ffmpeg_var, "ffmpeg")
        self._path_field(tool, f"ffprobe{executable_suffix}", self.ffprobe_var, "ffprobe")
        self._path_field(tool, "剪映专业版", self.jianying_var, "JianyingPro")
        self._directory_field(tool, "剪映本地草稿目录", self.jianying_drafts_var)
        detected = find_executable(self.ffmpeg_var.get(), "ffmpeg")
        self.ffmpeg_status = Label(tool, text=(f"已找到：{detected}" if detected else "尚未找到 FFmpeg；仍可制作图片，但不能合成静态漫视频。"), bg=SURFACE, fg=ACCENT_DARK if detected else WARM, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 9))
        self.ffmpeg_status.pack(anchor="w", pady=(18, 0))
        self._button(tool, "保存工具设置", self.save_settings, kind="primary").pack(anchor="e", pady=(22, 0))

        note_outer = self._card(body, bg=COMIC_MINT, padx=22, pady=16)
        note_outer.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        note = note_outer.winfo_children()[0]
        note_title = "Mac 兼容与 FFmpeg 说明" if sys.platform == "darwin" else "FFmpeg 配置说明"
        if sys.platform == "darwin":
            note_text = "Mac 应用会自动检查 PATH 中的 ffmpeg/ffprobe；也可以在这里手动选择可执行文件。"
        else:
            note_text = "安装完成后选择 bin 文件夹中的 ffmpeg.exe 与 ffprobe.exe。应用也会自动检查 PATH、WinGet Links 和常见安装目录。"
        Label(note, text=note_title, bg=COMIC_MINT, fg=ACCENT_DARK, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        Label(note, text=note_text, bg=COMIC_MINT, fg=MUTED, wraplength=900, justify=LEFT, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(5, 0))

    def _settings_entry(self, parent, label: str, variable: StringVar) -> None:
        self._field_label(parent, label).pack(anchor="w", pady=(13, 5))
        self._entry(parent, variable).pack(fill=X, ipady=7)

    def _apply_provider_selection(self, _event=None) -> None:
        previous = self.active_api_provider
        self.api_keys[previous] = self.api_key.get().strip()
        selected = self.provider_ids_by_label.get(self.provider_var.get(), "custom")
        self.active_api_provider = selected
        preset = provider_preset(selected)
        if selected != "custom":
            self.base_url_var.set(preset.base_url)
            self.model_name_var.set(preset.model)
        self.api_key.set(self._load_provider_api_key(selected))
        self._update_provider_help()
        if hasattr(self, "ai_test_status"):
            self.ai_test_status.configure(text="切换服务商后请重新测试连接", fg=MUTED)

    def _update_provider_help(self) -> None:
        preset = provider_preset(self.active_api_provider)
        self.provider_help.configure(text=preset.description)
        names = " / ".join(preset.environment_keys)
        self.api_key_hint.configure(text=f"也可在启动前设置环境变量：{names}")

    def _load_provider_api_key(self, provider_id: str) -> str:
        if provider_id in self.api_keys:
            return self.api_keys[provider_id]
        value = ""
        if self.remember_api_key.get():
            try:
                value = load_api_key(provider_id)
            except SecretStoreError:
                value = ""
        value = value or api_key_from_environment(provider_id)
        self.api_keys[provider_id] = value
        return value

    def _persist_current_api_key(self) -> None:
        provider_id = self.active_api_provider
        value = self.api_key.get().strip()
        self.api_keys[provider_id] = value
        if self.remember_api_key.get() and value:
            save_api_key(provider_id, value)
        else:
            delete_api_key(provider_id)

    def clear_saved_api_key(self) -> None:
        provider_id = self.active_api_provider
        try:
            delete_api_key(provider_id)
        except SecretStoreError as exc:
            messagebox.showerror("清除失败", str(exc))
            return
        self.api_keys[provider_id] = ""
        self.api_key.set("")
        messagebox.showinfo("已清除", f"{provider_preset(provider_id).label} 的已保存 API Key 已从系统凭据中删除。")

    def _path_field(self, parent, label: str, variable: StringVar, executable_name: str) -> None:
        self._field_label(parent, label).pack(anchor="w", pady=(13, 5))
        row = Frame(parent, bg=SURFACE)
        row.pack(fill=X)
        self._entry(row, variable).pack(side=LEFT, fill=X, expand=True, ipady=7)

        def browse() -> None:
            if sys.platform == "darwin" and executable_name == "JianyingPro":
                path = filedialog.askopenfilename(
                    title="选择剪映专业版.app（通常位于“应用程序”）",
                    initialdir="/Applications",
                    filetypes=[("Mac 应用", "*.app"), ("所有文件", "*.*")],
                )
            elif sys.platform == "darwin":
                path = filedialog.askopenfilename(title=f"选择 {executable_name}")
            else:
                path = filedialog.askopenfilename(title=f"选择 {executable_name}.exe", filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
            if path:
                variable.set(path)

        self._button(row, "选择", browse, kind="ghost").pack(side=RIGHT, padx=(7, 0))

    def _directory_field(self, parent, label: str, variable: StringVar) -> None:
        self._field_label(parent, label).pack(anchor="w", pady=(13, 5))
        row = Frame(parent, bg=SURFACE)
        row.pack(fill=X)
        self._entry(row, variable).pack(side=LEFT, fill=X, expand=True, ipady=7)

        def browse() -> None:
            path = filedialog.askdirectory(title="选择剪映草稿目录")
            if path:
                variable.set(path)

        self._button(row, "选择", browse, kind="ghost").pack(side=RIGHT, padx=(7, 0))

    def save_settings(self) -> None:
        settings = self.state["settings"]
        secret_error = ""
        if hasattr(self, "base_url_var"):
            settings["provider"] = self.active_api_provider
            settings["base_url"] = self.base_url_var.get().strip().rstrip("/")
            settings["model"] = self.model_name_var.get().strip()
            settings["remember_api_key"] = self.remember_api_key.get()
            try:
                self._persist_current_api_key()
            except SecretStoreError as exc:
                secret_error = str(exc)
            settings["ffmpeg_path"] = self.ffmpeg_var.get().strip()
            settings["ffprobe_path"] = self.ffprobe_var.get().strip()
            settings["jianying_exe"] = self.jianying_var.get().strip()
            settings["jianying_drafts_path"] = self.jianying_drafts_var.get().strip()
        self.store.save(self.state)
        self._refresh_tool_status()
        if hasattr(self, "ffmpeg_status"):
            detected = find_executable(settings["ffmpeg_path"], "ffmpeg")
            self.ffmpeg_status.configure(text=(f"已找到：{detected}" if detected else "尚未找到 FFmpeg；仍可制作图片，但不能合成静态漫视频。"), fg=ACCENT_DARK if detected else WARM)
        if secret_error:
            messagebox.showwarning("设置已保存", f"模型和工具设置已保存，但 API Key 未能安全保存：\n{secret_error}")
        elif self.remember_api_key.get():
            messagebox.showinfo("已保存", "模型、剪映草稿与视频工具设置已保存。API Key 已由系统安全保管，下次打开会自动填入。")
        else:
            messagebox.showinfo("已保存", "模型、剪映草稿与视频工具设置已保存。API Key 未被记住。")

    def _ai_client(self, use_form: bool = False) -> OpenAICompatibleClient:
        settings = self.state["settings"]
        if use_form and hasattr(self, "base_url_var"):
            base_url = self.base_url_var.get()
            model = self.model_name_var.get()
            provider_id = self.active_api_provider
        else:
            base_url = settings.get("base_url", "")
            model = settings.get("model", "")
            provider_id = settings.get("provider") or infer_provider(base_url, model)
        config = AIConfig(base_url, model, self.api_key.get(), provider=provider_id)
        if not config.base_url:
            raise AIClientError("请先填写模型 Base URL。")
        if not config.api_key:
            raise AIClientError("请先在“模型与工具”中填写 API Key。")
        return OpenAICompatibleClient(config)

    def test_ai_connection(self) -> None:
        if self.is_busy:
            return
        try:
            client = self._ai_client(use_form=True)
        except AIClientError as exc:
            messagebox.showwarning("无法测试", str(exc))
            return
        self.is_busy = True
        self.ai_test_status.configure(text=f"正在连接 {provider_preset(self.active_api_provider).label}…", fg=ACCENT_DARK)

        def worker() -> None:
            try:
                reply = client.complete("你是接口连通性测试助手。", "请只回复：连接成功", temperature=0.0)
                self.bus.put(("ai_test_complete", reply))
            except Exception as exc:  # noqa: BLE001 - display provider error in the UI
                self.bus.put(("ai_test_error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_settings_event(self, event: str, payload: object) -> None:
        if event == "ai_test_complete":
            self.is_busy = False
            self.ai_test_status.configure(text=f"连接成功：{str(payload)[:50]}", fg=ACCENT_DARK)
            secret_error = ""
            if self.remember_api_key.get():
                try:
                    self._persist_current_api_key()
                except SecretStoreError as exc:
                    secret_error = str(exc)
            message = f"{provider_preset(self.active_api_provider).label} 接口可以正常使用。"
            if self.remember_api_key.get() and not secret_error:
                message += "\nAPI Key 已安全记住。"
            elif secret_error:
                message += f"\n但 API Key 保存失败：{secret_error}"
            messagebox.showinfo("连接成功", message)
        elif event == "ai_test_error":
            self.is_busy = False
            self.ai_test_status.configure(text="连接失败，请检查 Key、模型名和网络", fg=ERROR)
            messagebox.showerror("连接失败", str(payload))

    # ------------------------------- Utilities -------------------------------
    def _copy_text(self, value: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(value)

    def _save_current_editors(self) -> None:
        if self.current_page == "video" and self.post_editor:
            self._sync_video_state()
        elif self.current_page == "novel":
            self._sync_novel_rules()
            self._save_chapter_editors()
            self.store.save(self.state)
        elif self.current_page == "comic":
            self.save_comic_settings(silent=True)

    def _drain_bus(self) -> None:
        try:
            while True:
                event, payload = self.bus.get_nowait()
                if self.bus_handler:
                    self.bus_handler(event, payload)
        except queue.Empty:
            pass
        self.root.after(120, self._drain_bus)

    def on_close(self) -> None:
        if self.is_busy and not messagebox.askyesno("任务仍在进行", "关闭应用会中断当前任务，确定退出吗？"):
            return
        self._save_current_editors()
        if hasattr(self, "remember_api_key"):
            self.state["settings"]["remember_api_key"] = self.remember_api_key.get()
            try:
                self._persist_current_api_key()
            except SecretStoreError:
                pass
        if hasattr(self, "remember_ark_api_key"):
            self.state["settings"]["remember_ark_api_key"] = self.remember_ark_api_key.get()
            try:
                if self.remember_ark_api_key.get() and self.ark_api_key.get().strip():
                    save_api_key("ark", self.ark_api_key.get().strip())
                else:
                    delete_api_key("ark")
            except SecretStoreError:
                pass
        self._cancel_scheduled_state_save()
        self.store.save(self.state)
        self.store.release_instance_lock()
        self.root.destroy()


def main() -> None:
    root = Tk()
    try:
        StudioApp(root)
    except StudioInstanceRunningError as exc:
        root.withdraw()
        messagebox.showwarning("程序已在运行", str(exc), parent=root)
        root.destroy()
        return
    root.mainloop()


def packaged_self_test() -> None:
    """Exercise native media parsing and Jianying draft assets in a package."""
    import tempfile
    import wave

    from pymediainfo import MediaInfo
    import pyJianYingDraft as draft

    with tempfile.TemporaryDirectory() as temp:
        wav_path = Path(temp) / "media-probe.wav"
        with wave.open(str(wav_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * 800)
        report = MediaInfo.parse(str(wav_path))
        if not report.tracks:
            raise RuntimeError("MediaInfo native library unavailable")

        script = draft.DraftFolder(temp).create_draft("self-test", 1080, 1920, 30)
        script.save()
        if not (Path(temp) / "self-test" / "draft_content.json").is_file():
            raise RuntimeError("Jianying draft assets unavailable")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        packaged_self_test()
    else:
        main()
