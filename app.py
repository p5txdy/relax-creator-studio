from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import zipfile
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
    ComicEngineError,
    build_ai_split_storyboard_prompt,
    build_character_prompt,
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
from core.secret_store import SecretStoreError, delete_api_key, load_api_key, save_api_key
from core.storage import StateStore, new_comic_project
from core.video_engine import (
    find_executable,
    probe_duration,
    run_export,
)


APP_NAME = "æ¼«ç”»æ¨æ–‡"
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
    "å›½é£ 3D åŠ¨æ¼«ï¼Œç”µå½±çº§å…‰å½±ï¼Œé«˜ç»†èŠ‚",
    "æ—¥ç³» 2D åŠ¨ç”»ï¼Œæ¸…æ™°çº¿ç¨¿ï¼Œç»†è…»èµ›ç’ç’ä¸Šè‰²",
    "å¤é£æ°´å¢¨æ¼«ç”»ï¼Œä¸œæ–¹ç¾å­¦ï¼ŒæŸ”å’Œå…‰å½±",
    "ç°ä»£éƒ½å¸‚æ¡æ¼«ï¼Œå†™å®åŠ¨æ¼«ï¼Œé«˜çº§ç”µå½±è°ƒè‰²",
    "éŸ©ç³»å”¯ç¾äºŒç»´æ¼«ç”»ï¼Œæ¼«ç”»åŒ–ç²¾è‡´äº”å®˜ï¼Œæ¸…æ™°çº¿ç¨¿ï¼ŒæŸ”å’Œæ¸å˜ä¸Šè‰²ï¼Œç¦æ­¢çœŸäººç…§ç‰‡ä¸3Då†™å®",
    "ç°ä»£éƒ½å¸‚éŸ©æ¼«ï¼ŒäºŒç»´éŸ©ç³»ç½‘ç»œæ¼«ç”»æ’ç”»ï¼Œç²¾è‡´é«˜é¢œå€¼æ¼«ç”»äººç‰©ï¼Œä¿®é•¿è‡ªç„¶æ¯”ä¾‹ï¼Œæ¸…æ™°çº¿ç¨¿ï¼Œå¹³æ»‘èµ›ç’ç’ä¸æŸ”å’Œæ¸å˜ä¸Šè‰²ï¼Œå¹²å‡€æ¼«ç”»è‚¤è‰²å—ï¼Œæˆå‰§åŒ–è¡¨æƒ…ä¸æ°›å›´å…‰ï¼Œç«–å±ç½‘æ¼«æ„å›¾ï¼Œç¦æ­¢çœŸäººç…§ç‰‡ã€å½±è§†å‰§ç…§ä¸3Då†™å®",
)
CHARACTER_BASE_NONE = "ï¼ˆä¸å…³è”ï¼Œåˆ›å»ºç‹¬ç«‹äººç‰©ï¼‰"
SHOT_IMAGE_MODEL_OPTIONS = (
    "Seedream 5.0 Liteï¼ˆçœé’±æ¨èï¼‰",
    "Seedream 5.0 Proï¼ˆè´¨é‡ä¼˜å…ˆï¼‰",
)
SHOT_IMAGE_MODEL_IDS = {
    SHOT_IMAGE_MODEL_OPTIONS[0]: SEEDREAM_LITE_MODEL,
    SHOT_IMAGE_MODEL_OPTIONS[1]: SEEDREAM_PRO_MODEL,
}
SHOT_IMAGE_MODEL_LABELS = {model_id: label for label, model_id in SHOT_IMAGE_MODEL_IDS.items()}


def _canvas_round_rect(canvas: Canvas, x1: float, y1: float, x2: float, y2: float, radius: float, **kwargs):
    """Draw an anti-aliased rounded rectangle by supersampling with Pillow."""
    width = max(1, int(round(x2 - x1)))
    height = max(1, int(round(y2 - y1)))
    radius = max(2.0, min(radius, width / 2, height / 2))
    scale = 4 if width * height <= 160_000 else 2
    image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = kwargs.pop("fill", None)
    outline = kwargs.pop("outline", None) or None
    line_width = max(1, int(kwargs.pop("width", 1) * scale))
    tags = kwargs.pop("tags", None)
    draw.rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1),
        radius=int(radius * scale),
        fill=fill,
        outline=outline,
        width=line_width,
    )
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(image, master=canvas)
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
        self.content = Frame(self, bg=surface, padx=padx, pady=pady)
        self.content_window = self.create_window(self.inset, self.inset, window=self.content, anchor="nw")
        self.bind("<Configure>", self._redraw, add="+")
        self.content.bind("<Configure>", self._sync_request, add="+")

    def _sync_request(self, _event=None) -> None:
        self.configure(
            width=max(20, self.content.winfo_reqwidth() + self.inset * 2),
            height=self.fixed_height or max(20, self.content.winfo_reqheight() + self.inset * 2),
        )

    def set_fixed_height(self, height: int) -> None:
        self.fixed_height = max(20, int(height))
        self.configure(height=self.fixed_height)

    def _redraw(self, _event=None) -> None:
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
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
        self.delete("all")
        self._aa_round_images = []
        width = max(8, self.winfo_width())
        height = max(8, self.winfo_height())
        _canvas_round_rect(self, 3, 2, width - 3, height - 2, 4, fill=SURFACE_ALT, outline="")
        if self.last - self.first < 0.999:
            top, bottom = self._thumb_bounds()
            color = ACCENT_DARK if self.hovered else "#86AAA4"
            _canvas_round_rect(self, 3, top, width - 3, bottom, 4, fill=color, outline="")

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
        super().bind("<Configure>", self._redraw, add="+")

    def _redraw(self, _event=None) -> None:
        width = max(10, self.winfo_width())
        height = max(10, self.winfo_height())
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
        super().bind("<Configure>", self._redraw, add="+")

    def _redraw(self, _event=None) -> None:
        width = max(10, self.winfo_width())
        height = max(10, self.winfo_height())
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
            pa×M;ç‹h‘éì¶»§q«^w‹Ú[™H™ÚÜİŠKœXÚÊÚYOSQ•
BˆÙ[‹—Ø]ÛŠ[Ù[ØXİ[ÛœË¹®!zfi9mì¹/çykfÙ^H‹Ù[‹˜ÛX\—ÜØ]™YØ\WÚÙ^KÚ[™H™ÚÜİŠKœXÚÊÚYOSQ•YJË
JBˆÙ[‹—Ø]ÛŠ[Ù[ØXİ[ÛœË¹/çykf9ª(yg¢ú+¯¹ïkˆ‹Ù[‹œØ]™WÜÙ][™ÜËÚ[™Hœš[X\HŠKœXÚÊÚYOT’QÒ
BˆÙ[‹—İ\]WÜ›İšY\—Ú[

BˆÙ[‹˜\×Ú[™\ˆHÙ[‹—Ú[™WÜÙ][™Ü×Ù]™[‚ˆÛÛÛİ]\ˆHÙ[‹—ØØ\™
›ÙKYLYOLŒŠBˆÛÛÛİ]\‹™ÜšY
›İÏLÛÛ[[LKİXÚŞOH›™]È‹YJK
JBˆÛÛHÛÛÛİ]\‹Ú[™›×ØÚ[™[Š
VÌBˆX™[
ÛÛ^H¹bj¹¦(:#byê/ù.#º)áºh¤yméyamÈ‹™ÏTÕT‘PÑK™ÏRS’Ë›ÛJ“ZXÜ›ÜÛÙXRZHRH‹M˜›ÛŠJKœXÚÊ[˜ÚÜHÈŠBˆX™[
ÛÛ^Hºgfy  y¯*ù/&¹æí9£©yå'ù¢$9cëùï%º/¤ybj¹¦(:#byê/ûï&Ñ‘›\YÈ9.áyå*9.£¹cëú`"yæ¡T:h¡:)â9kï9aî¸à ˆ‹™ÏTÕT‘PÑK™ÏSUUQÜ˜\[™İMÌ\İYOSQ•›ÛJ“ZXÜ›ÜÛÙXRZHRH‹JJKœXÚÊ[˜ÚÜHÈ‹YOJN
JBˆÙ[‹™™›\Y×İ˜\ˆHİš[™Õ˜\Š˜[YO\Ù][™ÜË™Ù]
™™›\Y×Ü]‹ˆŠJBˆÙ[‹™™œ›Ø™Wİ˜\ˆHİš[™Õ˜\Š˜[YO\Ù][™ÜË™Ù]
™™œ›Ø™WÜ]‹ˆŠJBˆÙ[‹ššX[Z[™×İ˜\ˆHİš[™Õ˜\Š˜[YO\Ù][™ÜË™Ù]
ššX[Z[™×Ù^H‹ˆŠHÜˆ]XİÚšX[Z[™×Ù^Xİ]X›JˆŠHÜˆˆŠBˆÙ[‹ššX[Z[™×Ù˜Y×İ˜\ˆHİš[™Õ˜\Š˜[YO\Ù][™ÜË™Ù]
ššX[Z[™×Ù˜Y×Ü]‹ˆŠHÜˆ]XİÚšX[Z[™×Ù˜Y×Ü]
ˆŠHÜˆˆŠBˆ^Xİ]X›WÜİY™š^HˆˆYˆŞ\Ëœ]›Ü›HOH™\Ú[ˆˆ[ÙH‹™^H‚ˆÙ[‹—Ü]ÙšY[
ÛÛˆ™™›\YŞÙ^Xİ]X›WÜİY™š^H‹Ù[‹™™›\Y×İ˜\‹™™›\YÈŠBˆÙ[‹—Ü]ÙšY[
ÛÛˆ™™œ›Ø™^Ù^Xİ]X›WÜİY™š^H‹Ù[‹™™œ›Ø™Wİ˜\‹™™œ›Ø™HŠBˆÙ[‹—Ü]ÙšY[
ÛÛ¹bj¹¦(9.$ù.&¹âb‹Ù[‹ššX[Z[™×İ˜\‹’šX[Z[™Ô›ÈŠBˆÙ[‹—Ù\™XİÜWÙšY[
ÛÛ¹bj¹¦(9§+9g,:#byê/ùæë¹oeH‹Ù[‹ššX[Z[™×Ù˜Y×İ˜\ŠBˆ]XİYHš[™Ù^Xİ]X›JÙ[‹™™›\Y×İ˜\‹™Ù]

K™™›\YÈŠBˆÙ[‹™™›\Y×Üİ]\ÈHX™[
ÛÛ^Jˆ¹mì¹¢o¹b,;ï&Ù]XİYHˆYˆ]XİY[ÙH¹l&¹§*¹¢o¹b,‘›\Yûï&ù.ãycëùb-¹/g9fï¹âaûï#9/a¹.#z ïyd"9¢$:gfy  y¯*ú)áºh¤xà ˆŠK™ÏTÕT‘PÑK™ÏPPĞÑS•ÑT’ÈYˆ]XİY[ÙHĞT“KÜ˜\[™İMÌ\İYOSQ•›ÛJ“ZXÜ›ÜÛÙXRZHRH‹JJBˆÙ[‹™™›\Y×Üİ]\ËœXÚÊ[˜ÚÜHÈ‹YOJN
JBˆÙ[‹—Ø]ÛŠÛÛ¹/çykf9méyamú+¯¹ïkˆ‹Ù[‹œØ]™WÜÙ][™ÜËÚ[™Hœš[X\HŠKœXÚÊ[˜ÚÜH™H‹YOJŒ‹
JB‚ˆ›İWÛİ]\ˆHÙ[‹—ØØ\™
›ÙK™ÏPÓÓRP×ÓRS•YLŒ‹YOLMŠBˆ›İWÛİ]\‹™ÜšY
›İÏLKÛÛ[[LÛÛ[[œÜ[L‹İXÚŞOH™]È‹YOJN
JBˆ›İHH›İWÛİ]\‹Ú[™›×ØÚ[™[Š
VÌBˆ›İWİ]HH“XXÈ9ao9k®y.#ˆ‘›\YÈ:+í9¦#ˆˆYˆŞ\Ëœ]›Ü›HOH™\Ú[ˆˆ[ÙH‘‘›\YÈ:acyïkº+í9¦#ˆ‚ˆYˆŞ\Ëœ]›Ü›HOH™\Ú[ˆ‚ˆ›İWİ^H“XXÈ9n¥9å*9/&º!ê¹bª9¨à9§éHU9.+yæ¡™›\YËÙ™œ›Ø™{ï&ù.gùcëù.éyg*:/æzaã9¢bùbª:`"y¢êycëù¢iú(c9¥¡ù.í¸à ˆ‚ˆ[ÙN‚ˆ›İWİ^H¹k¢z(áyk£9¢$9d#º`"y¢êHš[ˆ9¥¡ù.í¹i.y.+yæ¡™›\YË™^H9.#ˆ™œ›Ø™K™^xà ¹n¥9å*9.gù/&º!ê¹bª9¨à9§éHU8à UÚ[‘Ù][šÜÈ9d£9n.:)àyk¢z(áyæë¹oexà ˆ‚ˆX™[
›İK^[›İWİ]K™ÏPÓÓRP×ÓRS•™ÏPPĞÑS•ÑT’Ë›ÛJ“ZXÜ›ÜÛÙXRZHRH‹L˜›ÛŠJKœXÚÊ[˜ÚÜHÈŠBˆX™[
›İK^[›İWİ^™ÏPÓÓRP×ÓRS•™ÏSUUQÜ˜\[™İNL\İYOSQ•›ÛJ“ZXÜ›ÜÛÙXRZHRH‹JJKœXÚÊ[˜ÚÜHÈ‹YOJK
JB‚ˆYˆÜÙ][™Ü×Ù[JÙ[‹\™[X™[ˆİ‹˜\šXX›Nˆİš[™Õ˜\ŠHOˆ›Û™N‚ˆÙ[‹—ÙšY[ÛX™[
\™[X™[
KœXÚÊ[˜ÚÜHÈ‹YOJLËJJBˆÙ[‹—Ù[J\™[˜\šXX›JKœXÚÊš[V\YOMÊB‚ˆYˆØ\WÜ›İšY\—ÜÙ[Xİ[ÛŠÙ[‹Ù]™[S›Û™JHOˆ›Û™N‚ˆ™]š[İ\ÈHÙ[‹˜Xİ]™WØ\WÜ›İšY\‚ˆÙ[‹˜\WÚÙ^\ÖÜ™]š[İ\×HHÙ[‹˜\WÚÙ^K™Ù]

Kœİš\

BˆÙ[XİYHÙ[‹œ›İšY\—ÚY×ØWÛX™[™Ù]
Ù[‹œ›İšY\—İ˜\‹™Ù]

K˜İ\İÛHŠBˆÙ[‹˜Xİ]™WØ\WÜ›İšY\ˆHÙ[XİYˆ™\Ù]H›İšY\—Ü™\Ù]
Ù[XİY
BˆYˆÙ[XİYOH˜İ\İÛH‚ˆÙ[‹˜˜\ÙWİ\›İ˜\‹œÙ]
™\Ù]˜˜\ÙWİ\›
BˆÙ[‹›[Ù[Û˜[YWİ˜\‹œÙ]
™\Ù]›[Ù[
BˆÙ[‹˜\WÚÙ^KœÙ]
Ù[‹—ÛØYÜ›İšY\—Ø\WÚÙ^JÙ[XİY
JBˆÙ[‹—İ\]WÜ›İšY\—Ú[

BˆYˆ\Ø]ŠÙ[‹˜ZWİ\İÜİ]\ÈŠN‚ˆÙ[‹˜ZWİ\İÜİ]\Ë˜ÛÛ™šYİ\™J^H¹b!ù£h¹§#yb¨yea¹d#º+íúaãy¥¬9­bú+åz/ç¹£©H‹™ÏSUUQ
B‚ˆYˆİ\]WÜ›İšY\—Ú[
Ù[ŠHOˆ›Û™N‚ˆ™\Ù]H›İšY\—Ü™\Ù]
Ù[‹˜Xİ]™WØ\WÜ›İšY\ŠBˆÙ[‹œ›İšY\—Ú[˜ÛÛ™šYİ\™J^\™\Ù]™\ØÜš\[ÛŠBˆ˜[Y\ÈHˆÈ‹š›Ú[Š™\Ù]™[š\›Û›Y[ÚÙ^\ÊBˆÙ[‹˜\WÚÙ^WÚ[˜ÛÛ™šYİ\™J^Yˆ¹.gùcëùg*9d+ùbª9bcz+¯¹ïk¹ã«ùh ùcæ:aãûï&Û˜[Y\ßHŠB‚ˆYˆÛØYÜ›İšY\—Ø\WÚÙ^JÙ[‹›İšY\—ÚYˆİŠHOˆİ‚ˆYˆ›İšY\—ÚY[ˆÙ[‹˜\WÚÙ^\Î‚ˆ™]\›ˆÙ[‹˜\WÚÙ^\ÖÜ›İšY\—ÚYBˆ˜[YHHˆ‚ˆYˆÙ[‹œ™[Y[X™\—Ø\WÚÙ^K™Ù]

N‚ˆN‚ˆ˜[YHHØYØ\WÚÙ^J›İšY\—ÚY
Bˆ^Ù\ÙXÜ™]İÜ™Q\œ›Ü‚ˆ˜[YHHˆ‚ˆ˜[YHH˜[YHÜˆ\WÚÙ^WÙœ›ÛWÙ[š\›Û›Y[
›İšY\—ÚY
BˆÙ[‹˜\WÚÙ^\ÖÜ›İšY\—ÚYHH˜[YBˆ™]\›ˆ˜[YB‚ˆYˆÜ\œÚ\İØİ\œ™[Ø\WÚÙ^JÙ[ŠHOˆ›Û™N‚ˆ›İšY\—ÚYHÙ[‹˜Xİ]™WØ\WÜ›İšY\‚ˆ˜[YHHÙ[‹˜\WÚÙ^K™Ù]

Kœİš\

BˆÙ[‹˜\WÚÙ^\ÖÜ›İšY\—ÚYHH˜[YBˆYˆÙ[‹œ™[Y[X™\—Ø\WÚÙ^K™Ù]

H[™˜[YN‚ˆØ]™WØ\WÚÙ^J›İšY\—ÚY˜[YJBˆ[ÙN‚ˆ[]WØ\WÚÙ^J›İšY\—ÚY
B‚ˆYˆÛX\—ÜØ]™YØ\WÚÙ^JÙ[ŠHOˆ›Û™N‚ˆ›İšY\—ÚYHÙ[‹˜Xİ]™WØ\WÜ›İšY\‚ˆN‚ˆ[]WØ\WÚÙ^J›İšY\—ÚY
Bˆ^Ù\ÙXÜ™]İÜ™Q\œ›Üˆ\È^Î‚ˆY\ÜØYÙX›ŞœÚİÙ\œ›ÜŠ¹®!zfi9i,z-)H‹İŠ^ÊJBˆ™]\›‚ˆÙ[‹˜\WÚÙ^\ÖÜ›İšY\—ÚYHHˆ‚ˆÙ[‹˜\WÚÙ^KœÙ]
ˆŠBˆY\ÜØYÙX›ŞœÚİÚ[™›Ê¹mì¹®!zfi‹ˆÜ›İšY\—Ü™\Ù]
›İšY\—ÚY
K›X™[H9æ¡9mì¹/çykfTHÙ^H9mì¹.ã¹ìîùîçùaëy£k¹.+yb(:fi8à ˆŠB‚ˆYˆÜ]ÙšY[
Ù[‹\™[X™[ˆİ‹˜\šXX›Nˆİš[™Õ˜\‹^Xİ]X›WÛ˜[YNˆİŠHOˆ›Û™N‚ˆÙ[‹—ÙšY[ÛX™[
\™[X™[
KœXÚÊ[˜ÚÜHÈ‹YOJLËJJBˆ›İÈHœ˜[YJ\™[™ÏTÕT‘PÑJBˆ›İËœXÚÊš[V
BˆÙ[‹—Ù[J›İË˜\šXX›JKœXÚÊÚYOSQ•š[V^[™UYK\YOMÊB‚ˆYˆœ›İÜÙJ
HOˆ›Û™N‚ˆYˆŞ\Ëœ]›Ü›HOH™\Ú[ˆˆ[™^Xİ]X›WÛ˜[YHOH’šX[Z[™Ô›È‚ˆ]Hš[YX[ÙË˜\ÚÛÜ[™š[[˜[YJˆ]OHº`"y¢êybj¹¦(9.$ù.&¹âb˜\;ï":`&¹n.9/cy.£¸ '9n¥9å*9ê"ùn£ø '{ï"H‹ˆ[š]X[\H‹Ğ\XØ][ÛœÈ‹ˆš[]\\ÏVÊ“XXÈ9n¥9å*‹Š‹˜\ŠK
¹¢`9§"y¥¡ù.íˆ‹Š‹ŠˆŠWKˆ
Bˆ[YˆŞ\Ëœ]›Ü›HOH™\Ú[ˆ‚ˆ]Hš[YX[ÙË˜\ÚÛÜ[™š[[˜[YJ]OYˆº`"y¢êHÙ^Xİ]X›WÛ˜[Y_HŠBˆ[ÙN‚ˆ]Hš[YX[ÙË˜\ÚÛÜ[™š[[˜[YJ]OYˆº`"y¢êHÙ^Xİ]X›WÛ˜[Y_K™^H‹š[]\\ÏVÊ¹cëù¢iú(c9¥¡ù.íˆ‹Š‹™^HŠK
¹¢`9§"y¥¡ù.íˆ‹Š‹ŠˆŠWJBˆYˆ]‚ˆ˜\šXX›KœÙ]
]
B‚ˆÙ[‹—Ø]ÛŠ›İËº`"y¢êH‹œ›İÜÙKÚ[™H™ÚÜİŠKœXÚÊÚYOT’QÒYJË
JB‚ˆYˆÙ\™XİÜWÙšY[
Ù[‹\™[X™[ˆİ‹˜\šXX›Nˆİš[™Õ˜\ŠHOˆ›Û™N‚ˆÙ[‹—ÙšY[ÛX™[
\™[X™[
KœXÚÊ[˜ÚÜHÈ‹YOJLËJJBˆ›İÈHœ˜[YJ\™[™ÏTÕT‘PÑJBˆ›İËœXÚÊš[V
BˆÙ[‹—Ù[J›İË˜\šXX›JKœXÚÊÚYOSQ•š[V^[™UYK\YOMÊB‚ˆYˆœ›İÜÙJ
HOˆ›Û™N‚ˆ]Hš[YX[ÙË˜\ÚÙ\™XİÜJ]OHº`"y¢êybj¹¦(:#byê/ùæë¹oeHŠBˆYˆ]‚ˆ˜\šXX›KœÙ]
]
B‚ˆÙ[‹—Ø]ÛŠ›İËº`"y¢êH‹œ›İÜÙKÚ[™H™ÚÜİŠKœXÚÊÚYOT’QÒYJË
JB‚ˆYˆØ]™WÜÙ][™ÜÊÙ[ŠHOˆ›Û™N‚ˆÙ][™ÜÈHÙ[‹œİ]VÈœÙ][™ÜÈ—BˆÙXÜ™]Ù\œ›ÜˆHˆ‚ˆYˆ\Ø]ŠÙ[‹˜˜\ÙWİ\›İ˜\ˆŠN‚ˆÙ][™ÜÖÈœ›İšY\ˆ—HHÙ[‹˜Xİ]™WØ\WÜ›İšY\‚ˆÙ][™ÜÖÈ˜˜\ÙWİ\›—HHÙ[‹˜˜\ÙWİ\›İ˜\‹™Ù]

Kœİš\

Kœœİš\
‹ÈŠBˆÙ][™ÜÖÈ›[Ù[—HHÙ[‹›[Ù[Û˜[YWİ˜\‹™Ù]

Kœİš\

BˆÙ][™ÜÖÈœ™[Y[X™\—Ø\WÚÙ^H—HHÙ[‹œ™[Y[X™\—Ø\WÚÙ^K™Ù]

BˆN‚ˆÙ[‹—Ü\œÚ\İØİ\œ™[Ø\WÚÙ^J
Bˆ^Ù\ÙXÜ™]İÜ™Q\œ›Üˆ\È^Î‚ˆÙXÜ™]Ù\œ›ÜˆHİŠ^ÊBˆÙ][™ÜÖÈ™™›\Y×Ü]—HHÙ[‹™™›\Y×İ˜\‹™Ù]

Kœİš\

BˆÙ][™ÜÖÈ™™œ›Ø™WÜ]—HHÙ[‹™™œ›Ø™Wİ˜\‹™Ù]

Kœİš\

BˆÙ][™ÜÖÈššX[Z[™×Ù^H—HHÙ[‹ššX[Z[™×İ˜\‹™Ù]

Kœİš\

BˆÙ][™ÜÖÈššX[Z[™×Ù˜Y×Ü]—HHÙ[‹ššX[Z[™×Ù˜Y×İ˜\‹™Ù]

Kœİš\

BˆÙ[‹œİÜ™KœØ]™JÙ[‹œİ]JBˆÙ[‹—Ü™Yœ™\ÚİÛÛÜİ]\Ê
BˆYˆ\Ø]ŠÙ[‹™™›\Y×Üİ]\ÈŠN‚ˆ]XİYHš[™Ù^Xİ]X›JÙ][™ÜÖÈ™™›\Y×Ü]—K™™›\YÈŠBˆÙ[‹™™›\Y×Üİ]\Ë˜ÛÛ™šYİ\™J^Jˆ¹mì¹¢o¹b,;ï&Ù]XİYHˆYˆ]XİY[ÙH¹l&¹§*¹¢o¹b,‘›\Yûï&ù.ãycëùb-¹/g9fï¹âaûï#9/a¹.#z ïyd"9¢$:gfy  y¯*ú)áºh¤xà ˆŠK™ÏPPĞÑS•ÑT’ÈYˆ]XİY[ÙHĞT“JBˆYˆÙXÜ™]Ù\œ›Ü‚ˆY\ÜØYÙX›ŞœÚİİØ\›š[™Êº+¯¹ïk¹mì¹/çykf‹ˆ¹ª(yg¢ùd£9méyamú+¯¹ïk¹mì¹/çykf;ï#9/aˆTHÙ^H9§*º ïyk¢yaj9/çykf;ï&—ÜÙXÜ™]Ù\œ›ÜŸHŠBˆ[YˆÙ[‹œ™[Y[X™\—Ø\WÚÙ^K™Ù]

N‚ˆY\ÜØYÙX›ŞœÚİÚ[™›Ê¹mì¹/çykf‹¹ª(yg¢øà ybj¹¦(:#byê/ù.#º)áºh¤yméyamú+¯¹ïk¹mì¹/çykf8à THÙ^H9mì¹å,yìîùîçùk¢yaj9/çyë¨{ï#9."ù«(y¢dùo 9/&º!ê¹bª9hjùaixà ˆŠBˆ[ÙN‚ˆY\ÜØYÙX›ŞœÚİÚ[™›Ê¹mì¹/çykf‹¹ª(yg¢øà ybj¹¦(:#byê/ù.#º)áºh¤yméyamú+¯¹ïk¹mì¹/çykf8à THÙ^H9§*º(ªú+¬9/cøà ˆŠB‚ˆYˆØZWØÛY[
Ù[‹\ÙWÙ›Ü›Nˆ›ÛÛH˜[ÙJHOˆÜ[RPÛÛ\]X›PÛY[‚ˆÙ][™ÜÈHÙ[‹œİ]VÈœÙ][™ÜÈ—BˆYˆ\ÙWÙ›Ü›H[™\Ø]ŠÙ[‹˜˜\ÙWİ\›İ˜\ˆŠN‚ˆ˜\ÙWİ\›HÙ[‹˜˜\ÙWİ\›İ˜\‹™Ù]

Bˆ[Ù[HÙ[‹›[Ù[Û˜[YWİ˜\‹™Ù]

Bˆ›İšY\—ÚYHÙ[‹˜Xİ]™WØ\WÜ›İšY\‚ˆ[ÙN‚ˆ˜\ÙWİ\›HÙ][™ÜË™Ù]
˜˜\ÙWİ\›‹ˆŠBˆ[Ù[HÙ][™ÜË™Ù]
›[Ù[‹ˆŠBˆ›İšY\—ÚYHÙ][™ÜË™Ù]
œ›İšY\ˆŠHÜˆ[™™\—Ü›İšY\Š˜\ÙWİ\›[Ù[
BˆÛÛ™šYÈHRPÛÛ™šYÊ˜\ÙWİ\›[Ù[Ù[‹˜\WÚÙ^K™Ù]

K›İšY\\›İšY\—ÚY
BˆYˆ›İÛÛ™šYË˜˜\ÙWİ\›‚ˆ˜Z\ÙHRPÛY[\œ›ÜŠº+íùab9hjùa¦yª(yg¢È˜\ÙHT“8à ˆŠBˆYˆ›İÛÛ™šYË˜\WÚÙ^N‚ˆ˜Z\ÙHRPÛY[\œ›ÜŠº+íùab9g*8 '9ª(yg¢ù.#¹méyamø 'y.+yhjùa¦HTHÙ^xà ˆŠBˆ™]\›ˆÜ[RPÛÛ\]X›PÛY[
ÛÛ™šYÊB‚ˆYˆ\İØZWØÛÛ›™Xİ[ÛŠÙ[ŠHOˆ›Û™N‚ˆYˆÙ[‹š\×Ø\ŞN‚ˆ™]\›‚ˆN‚ˆÛY[HÙ[‹—ØZWØÛY[
\ÙWÙ›Ü›OUYJBˆ^Ù\RPÛY[\œ›Üˆ\È^Î‚ˆY\ÜØYÙX›ŞœÚİİØ\›š[™Ê¹¥è9¬åy­bú+åH‹İŠ^ÊJBˆ™]\›‚ˆÙ[‹š\×Ø\ŞHHYBˆÙ[‹˜ZWİ\İÜİ]\Ë˜ÛÛ™šYİ\™J^Yˆ¹«hùg*:/ç¹£©HÜ›İšY\—Ü™\Ù]
Ù[‹˜Xİ]™WØ\WÜ›İšY\ŠK›X™[x )ˆ‹™ÏPPĞÑS•ÑT’ÊB‚ˆYˆÛÜšÙ\Š
HOˆ›Û™N‚ˆN‚ˆ™\HHÛY[˜ÛÛ\]J¹/h9¦+ù£©ycèú/çº`&¹ )ù­bú+åybªy¢bøà ˆ‹º+íùcê¹fç¹i#{ï&º/ç¹£©y¢$9b§È‹[\\˜]\™OLŒ
BˆÙ[‹˜\Ëœ]

˜ZWİ\İØÛÛ\]H‹™\JJBˆ^Ù\^Ù\[Ûˆ\È^ÎˆÈ›ÜXNˆ“LHH\Ü^H›İšY\ˆ\œ›Üˆ[ˆHRBˆÙ[‹˜\Ëœ]

˜ZWİ\İÙ\œ›Üˆ‹^ÊJB‚ˆ™XY[™Ë•™XY
\™Ù]]ÛÜšÙ\‹Y[[ÛUYJKœİ\

B‚ˆYˆÚ[™WÜÙ][™Ü×Ù]™[
Ù[‹]™[ˆİ‹^[ØYˆØš™Xİ
HOˆ›Û™N‚ˆYˆ]™[OH˜ZWİ\İØÛÛ\]H‚ˆÙ[‹š\×Ø\ŞHH˜[ÙBˆÙ[‹˜ZWİ\İÜİ]\Ë˜ÛÛ™šYİ\™J^Yˆº/ç¹£©y¢$9b§ûï&ÜİŠ^[ØY
VÎL_H‹™ÏPPĞÑS•ÑT’ÊBˆÙXÜ™]Ù\œ›ÜˆHˆ‚ˆYˆÙ[‹œ™[Y[X™\—Ø\WÚÙ^K™Ù]

N‚ˆN‚ˆÙ[‹—Ü\œÚ\İØİ\œ™[Ø\WÚÙ^J
Bˆ^Ù\ÙXÜ™]İÜ™Q\œ›Üˆ\È^Î‚ˆÙXÜ™]Ù\œ›ÜˆHİŠ^ÊBˆY\ÜØYÙHHˆÜ›İšY\—Ü™\Ù]
Ù[‹˜Xİ]™WØ\WÜ›İšY\ŠK›X™[H9£©ycèùcëù.éy«hùn.9/oùå*8à ˆ‚ˆYˆÙ[‹œ™[Y[X™\—Ø\WÚÙ^K™Ù]

H[™›İÙXÜ™]Ù\œ›Ü‚ˆY\ÜØYÙH
ÏH—THÙ^H9mì¹k¢yaj:+¬9/cøà ˆ‚ˆ[YˆÙXÜ™]Ù\œ›Ü‚ˆY\ÜØYÙH
ÏHˆ—¹/aˆTHÙ^H9/çykf9i,z-){ï&ÜÙXÜ™]Ù\œ›ÜŸH‚ˆY\ÜØYÙX›ŞœÚİÚ[™›Êº/ç¹£©y¢$9b§È‹Y\ÜØYÙJBˆ[Yˆ]™[OH˜ZWİ\İÙ\œ›Üˆ‚ˆÙ[‹š\×Ø\ŞHH˜[ÙBˆÙ[‹˜ZWİ\İÜİ]\Ë˜ÛÛ™šYİ\™J^Hº/ç¹£©yi,z-){ï#:+íù¨à9§éHÙ^xà yª(yg¢ùd#yd£9ïdyîç‹™ÏQT”“ÔŠBˆY\ÜØYÙX›ŞœÚİÙ\œ›ÜŠº/ç¹£©yi,z-)H‹İŠ^[ØY
JB‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKH][]Y\ÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆYˆØÛÜWİ^
Ù[‹˜[YNˆİŠHOˆ›Û™N‚ˆÙ[‹œ›Ûİ˜Û\›Ø\™ØÛX\Š
BˆÙ[‹œ›Ûİ˜Û\›Ø\™Ø\[™
˜[YJB‚ˆYˆÜØ]™WØİ\œ™[ÙY]ÜœÊÙ[ŠHOˆ›Û™N‚ˆYˆÙ[‹˜İ\œ™[ÜYÙHOHšY[Èˆ[™Ù[‹œÜİÙY]Ü‚ˆÙ[‹—ÜŞ[˜×İšY[×Üİ]J
Bˆ[YˆÙ[‹˜İ\œ™[ÜYÙHOH››İ™[‚ˆÙ[‹—ÜŞ[˜×Û›İ™[Ü[\Ê
BˆÙ[‹—ÜØ]™WØÚ\\—ÙY]ÜœÊ
BˆÙ[‹œİÜ™KœØ]™JÙ[‹œİ]JBˆ[YˆÙ[‹˜İ\œ™[ÜYÙHOH˜ÛÛZXÈ‚ˆÙ[‹œØ]™WØÛÛZX×ÜÙ][™ÜÊÚ[[UYJB‚ˆYˆÙ˜Z[—Ø\ÊÙ[ŠHOˆ›Û™N‚ˆN‚ˆÚ[HYN‚ˆ]™[^[ØYHÙ[‹˜\Ë™Ù]Û›İØZ]

BˆYˆÙ[‹˜\×Ú[™\‚ˆÙ[‹˜\×Ú[™\Š]™[^[ØY
Bˆ^Ù\]Y]YK‘[\N‚ˆ\ÜÂˆÙ[‹œ›Ûİ˜Y\ŠLŒÙ[‹—Ù˜Z[—Ø\ÊB‚ˆYˆÛ—ØÛÜÙJÙ[ŠHOˆ›Û™N‚ˆYˆÙ[‹š\×Ø\ŞH[™›İY\ÜØYÙX›Ş˜\ÚŞY\Û›Ê¹.îùb¨y.ãyg*:/æú(c‹¹alúeëyn¥9å*9/&¹.+y¥«yodùbcy.îùb¨{ï#9èk¹k¦º` 9aî¹d%ûï'ÈŠN‚ˆ™]\›‚ˆÙ[‹—ÜØ]™WØİ\œ™[ÙY]ÜœÊ
BˆYˆ\Ø]ŠÙ[‹œ™[Y[X™\—Ø\WÚÙ^HŠN‚ˆÙ[‹œİ]VÈœÙ][™ÜÈ—VÈœ™[Y[X™\—Ø\WÚÙ^H—HHÙ[‹œ™[Y[X™\—Ø\WÚÙ^K™Ù]

BˆN‚ˆÙ[‹—Ü\œÚ\İØİ\œ™[Ø\WÚÙ^J
Bˆ^Ù\ÙXÜ™]İÜ™Q\œ›Ü‚ˆ\ÜÂˆYˆ\Ø]ŠÙ[‹œ™[Y[X™\—Ø\š×Ø\WÚÙ^HŠN‚ˆÙ[‹œİ]VÈœÙ][™ÜÈ—VÈœ™[Y[X™\—Ø\š×Ø\WÚÙ^H—HHÙ[‹œ™[Y[X™\—Ø\š×Ø\WÚÙ^K™Ù]

BˆN‚ˆYˆÙ[‹œ™[Y[X™\—Ø\š×Ø\WÚÙ^K™Ù]

H[™Ù[‹˜\š×Ø\WÚÙ^K™Ù]

Kœİš\

N‚ˆØ]™WØ\WÚÙ^J˜\šÈ‹Ù[‹˜\š×Ø\WÚÙ^K™Ù]

Kœİš\

JBˆ[ÙN‚ˆ[]WØ\WÚÙ^J˜\šÈŠBˆ^Ù\ÙXÜ™]İÜ™Q\œ›Ü‚ˆ\ÜÂˆÙ[‹œİÜ™KœØ]™JÙ[‹œİ]JBˆÙ[‹œİÜ™Kœ™[X\ÙWÚ[œİ[˜ÙWÛØÚÊ
BˆÙ[‹œ›Ûİ™\İ›ŞJ
B‚‚™YˆXZ[Š
HOˆ›Û™N‚ˆ›ÛİHÊ
BˆN‚ˆİY[Ğ\
›Ûİ
Bˆ^Ù\İY[Ò[œİ[˜ÙT[›š[™Ñ\œ›Üˆ\È^Î‚ˆ›ÛİÚ]˜]Ê
BˆY\ÜØYÙX›ŞœÚİİØ\›š[™Ê¹ê"ùn£ùmì¹g*:/ä:(c‹İŠ^ÊK\™[\›Ûİ
Bˆ›Ûİ™\İ›ŞJ
Bˆ™]\›‚ˆ›Ûİ›XZ[›ÛÜ

B‚‚™YˆXÚØYÙYÜÙ[—İ\İ

HOˆ›Û™N‚ˆˆˆ‘^\˜Ú\ÙH˜]]™HYYXH\œÚ[™È[™šX[Z[™È˜Y\ÜÙ]È[ˆHXÚØYÙKˆˆˆ‚ˆ[\Ü[\š[B‚ˆœ›ÛH[YYXZ[™›È[\ÜYYXR[™›Âˆ[\ÜRšX[–Z[™Ñ˜Y\È˜Y‚ˆYˆ›İØ[X›JÙ]]ŠYYXR[™›Ëœ\œÙH‹›Û™JJN‚ˆ˜Z\ÙH[[YQ\œ›ÜŠ“YYXR[™›È\œÙ\ˆ[˜]˜Z[X›HŠBˆÚ][\š[K•[\Ü˜\Q\™XİÜJ
H\È[\‚ˆØÜš\H˜Y‘˜Y›Û\Š[\
K˜Ü™X]WÙ˜Y
œÙ[‹]\İ‹LNLŒÌ
BˆØÜš\œØ]™J
BˆYˆ›İ
]
[\
HÈœÙ[‹]\İˆÈ™˜YØÛÛ[šœÛÛˆŠKš\×Ùš[J
N‚ˆ˜Z\ÙH[[YQ\œ›ÜŠ’šX[Z[™È˜Y\ÜÙ]È[˜]˜Z[X›HŠB‚‚šYˆ×Û˜[YW×ÈOH—×ÛXZ[—×È‚ˆYˆ‹K\Ù[‹]\İˆ[ˆŞ\Ë˜\™İ‚ˆXÚØYÙYÜÙ[—İ\İ

Bˆ[ÙN‚ˆXZ[Š
B