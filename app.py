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
    SEEDREAM_MODEL,
    DoubaoSeedreamClient,
    SeedreamConfig,
)
from core.comic_video_engine import allocate_shot_durations, build_comic_video_command, load_srt, probe_audio_duration
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
APP_VERSION = "1.0"
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
    "éŸ©ç³»å”¯ç¾æ¼«ç”»ï¼Œç²¾è‡´äº”å®˜ï¼ŒæŸ”å…‰æ°›å›´",
    "ç°ä»£éƒ½å¸‚éŸ©æ¼«ï¼Œç²¾è‡´é«˜é¢œå€¼äººç‰©ï¼Œä¿®é•¿è‡ªç„¶æ¯”ä¾‹ï¼Œæ¸…æ™°çº¿ç¨¿ï¼ŒæŸ”å’Œæ¸å˜ä¸Šè‰²ï¼Œç»†è…»çš®è‚¤è´¨æ„Ÿï¼Œæˆå‰§åŒ–è¡¨æƒ…ä¸æš§æ˜§æ°›å›´å…‰ï¼Œç«–å±ç½‘æ¼«æ„å›¾",
)


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
        self.bind("<Configure>", self._draw, add="+")
        self.bind("<Enter>", lambda _event: self._set_hover(True), add="+")
        self.bind("<Leave>", lambda _event: self._set_hover(False), add="+÷6ÚÚ$z{-®éÜj×âÂ¶–æCÒ&v†÷7B"’ç6²‡6–FSÔÄTeB¢6VÆbåö'WGFöâ†ÖöFVÅö7F–öç2Â.kˆ^™šN[{.KùŞZÙ‚¶W’"Â6VÆbæ6ÆV%÷6fVEö•ö¶W’Â¶–æCÒ&v†÷7B"’ç6²‡6–FSÔÄTeBÂGƒÒƒrÂ’¢6VÆbåö'WGFöâ†ÖöFVÅö7F–öç2Â.KùŞZÙjŠYè¾Šëî{Úâ"Â6VÆbç6fU÷6WGF–æw2Â¶–æCÒ'&–Ö'’"’ç6²‡6–FSÕ$”t…B¢6VÆbå÷WFFU÷&÷f–FW%ö†VÇ‚¢6VÆbæ'W5ö†æFÆW"Ò6VÆbåö†æFÆU÷6WGF–æw5öWfVç@ ¢FööÅö÷WFW"Ò6VÆbåö6&B†&öG’ÂGƒÓ#BÂG“Ó#"¢FööÅö÷WFW"æw&–B‡&÷sÓÂ6öÇVÖãÓÂ7F–6·“Ò&æWr"ÂGƒÒƒ’Â’¢FööÂÒFööÅö÷WFW"çv–æfõö6†–ÆG&Vâ‚•³Ğ¢Æ&VÂ‡FööÂÂFW‡CÒ.Xš®iŠˆØz‹şKˆîŠxnš)[z^X[r"Â&sÕ5U$d4RÂfsÔ”ä²ÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"ÂBÂ&&öÆB"’’ç6²†æ6†÷#Ò'r"¢Æ&VÂ‡FööÂÂFW‡CÒ.™ÙhkÊ¾KÉ®y»Nhê^yIşh‰Xúş{Én‹éXš®iŠˆØz‹şûÉ´df×VrK¸^yJK¨îXúş˜y¨BÕBš(NŠxZûÎX{®8""Â&sÕ5U$d4RÂfsÔÕUDTBÂw&ÆVæwFƒÓC3Â§W7F–g“ÔÄTeBÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"Â’’’ç6²†æ6†÷#Ò'r"ÂG“ÒƒBÂ‚’¢6VÆbæff×Vu÷f"Ò7G&–æuf"‡fÇVS×6WGF–æw2ævWB‚&ff×Vu÷F‚"Â""’¢6VÆbæfg&ö&U÷f"Ò7G&–æuf"‡fÇVS×6WGF–æw2ævWB‚&fg&ö&U÷F‚"Â""’¢6VÆbæ¦–ç––æu÷f"Ò7G&–æuf"‡fÇVS×6WGF–æw2ævWB‚&¦–ç––æuöW†R"Â""’÷"FWFV7Eö¦–ç––æuöW†V7WF&ÆR‚""’÷"""¢6VÆbæ¦–ç––æuöG&gG5÷f"Ò7G&–æuf"‡fÇVS×6WGF–æw2ævWB‚&¦–ç––æuöG&gG5÷F‚"Â""’÷"FWFV7Eö¦–ç––æuöG&gG5÷F‚‚""’÷"""¢W†V7WF&ÆU÷7Vff—‚Ò""–b7—2çÆFf÷&ÒÓÒ&F'v–â"VÇ6R"æW†R ¢6VÆbå÷F…öf–VÆB‡FööÂÂb&ff×Vw¶W†V7WF&ÆU÷7Vff—‡Ò"Â6VÆbæff×Vu÷f"Â&ff×Vr"¢6VÆbå÷F…öf–VÆB‡FööÂÂb&fg&ö&W¶W†V7WF&ÆU÷7Vff—‡Ò"Â6VÆbæfg&ö&U÷f"Â&fg&ö&R"¢6VÆbå÷F…öf–VÆB‡FööÂÂ.Xš®iŠK‰>K‰®x˜‚"Â6VÆbæ¦–ç––æu÷f"Â$¦–ç––æu&ò"¢6VÆbåöF—&V7F÷'•öf–VÆB‡FööÂÂ.Xš®iŠiÊÎYËˆØz‹şyºî[ÙR"Â6VÆbæ¦–ç––æuöG&gG5÷f"¢FWFV7FVBÒf–æEöW†V7WF&ÆR‡6VÆbæff×Vu÷f"ævWB‚’Â&ff×Vr"¢6VÆbæff×Vu÷7FGW2ÒÆ&VÂ‡FööÂÂFW‡CÒ†b.[{.h›îX‹ûÉ§¶FWFV7FVGÒ"–bFWFV7FVBVÇ6R.[	®iÊ®h›îX‹df×V~ûÉ¾K¸ŞXúşX‹nKÙÎY»îx˜~ûÈÎKØnKˆŞˆ;ŞYh‰™ÙhkÊ¾Šxnš)8""’Â&sÕ5U$d4RÂfsÔ44TåEôD$²–bFWFV7FVBVÇ6Rt$ÒÂw&ÆVæwFƒÓC3Â§W7F–g“ÔÄTeBÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"Â’’¢6VÆbæff×Vu÷7FGW2ç6²†æ6†÷#Ò'r"ÂG“Òƒ‚Â’¢6VÆbåö'WGFöâ‡FööÂÂ.KùŞZÙ[z^X[~Šëî{Úâ"Â6VÆbç6fU÷6WGF–æw2Â¶–æCÒ'&–Ö'’"’ç6²†æ6†÷#Ò&R"ÂG“Òƒ#"Â’ ¢æ÷FUö÷WFW"Ò6VÆbåö6&B†&öG’Â&sÔ4ôÔ”5ôÔ”åBÂGƒÓ#"ÂG“Ób¢æ÷FUö÷WFW"æw&–B‡&÷sÓÂ6öÇVÖãÓÂ6öÇVÖç7ãÓ"Â7F–6·“Ò&Wr"ÂG“Òƒ‚Â’¢æ÷FRÒæ÷FUö÷WFW"çv–æfõö6†–ÆG&Vâ‚•³Ğ¢æ÷FU÷F—FÆRÒ$Ö2X[ÎZëKˆâdf×VrŠûNiˆâ"–b7—2çÆFf÷&ÒÓÒ&F'v–â"VÇ6R$df×Vr˜XŞ{ÚîŠûNiˆâ ¢–b7—2çÆFf÷&ÒÓÒ&F'v–â# ¢æ÷FU÷FW‡BÒ$Ö2[©NyJKÉ®ˆz®Xªj8iúRD‚KŠŞy¨Bff×Vröfg&ö&^ûÉ¾K™şXúşKº^YÊ‹ù˜xÎh˜¾Xª˜hºXúşhš~ŠÎih~K»n8" ¢VÇ6S ¢æ÷FU÷FW‡BÒ.ZèŠ8^ZèÎh‰Yî˜hº’&–âih~K»nZKKŠŞy¨Bff×VræW†RKˆâfg&ö&RæW†^8.[©NyJK™şKÉ®ˆz®Xªj8iúRD8v–ävWBÆ–æ·2Y(Î[‹ŠxZèŠ8^yºî[Ù^8" ¢Æ&VÂ†æ÷FRÂFW‡CÖæ÷FU÷F—FÆRÂ&sÔ4ôÔ”5ôÔ”åBÂfsÔ44TåEôD$²ÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"ÂÂ&&öÆB"’’ç6²†æ6†÷#Ò'r"¢Æ&VÂ†æ÷FRÂFW‡CÖæ÷FU÷FW‡BÂ&sÔ4ôÔ”5ôÔ”åBÂfsÔÕUDTBÂw&ÆVæwFƒÓ“Â§W7F–g“ÔÄTeBÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"Â’’’ç6²†æ6†÷#Ò'r"ÂG“ÒƒRÂ’ ¢FVb÷6WGF–æw5öVçG'’‡6VÆbÂ&VçBÂÆ&VÃ¢7G"Âf&–&ÆS¢7G&–æuf"’ÓâæöæS ¢6VÆbåöf–VÆEöÆ&VÂ‡&VçBÂÆ&VÂ’ç6²†æ6†÷#Ò'r"ÂG“Òƒ2ÂR’¢6VÆbåöVçG'’‡&VçBÂf&–&ÆR’ç6²†f–ÆÃÕ‚Â—G“Ór ¢FVböÇ•÷&÷f–FW%÷6VÆV7F–öâ‡6VÆbÂöWfVçCÔæöæR’ÓâæöæS ¢&Wf–÷W2Ò6VÆbæ7F—fUö•÷&÷f–FW ¢6VÆbæ•ö¶W—5·&Wf–÷W5ÒÒ6VÆbæ•ö¶W’ævWB‚’ç7G&—‚¢6VÆV7FVBÒ6VÆbç&÷f–FW%ö–G5ö'•öÆ&VÂævWB‡6VÆbç&÷f–FW%÷f"ævWB‚’Â&7W7FöÒ"¢6VÆbæ7F—fUö•÷&÷f–FW"Ò6VÆV7FV@¢&W6WBÒ&÷f–FW%÷&W6WB‡6VÆV7FVB¢–b6VÆV7FVBÒ&7W7FöÒ# ¢6VÆbæ&6U÷W&Å÷f"ç6WB‡&W6WBæ&6U÷W&Â¢6VÆbæÖöFVÅöæÖU÷f"ç6WB‡&W6WBæÖöFVÂ¢6VÆbæ•ö¶W’ç6WB‡6VÆbåöÆöE÷&÷f–FW%ö•ö¶W’‡6VÆV7FVB’¢6VÆbå÷WFFU÷&÷f–FW%ö†VÇ‚¢–b†6GG"‡6VÆbÂ&•÷FW7E÷7FGW2"“ ¢6VÆbæ•÷FW7E÷7FGW2æ6öæf–wW&R‡FW‡CÒ.Xˆ~hÚ.iÈŞXªYXnYîŠû~˜xŞikkX¾Šù^‹ùîhêR"ÂfsÔÕUDTB ¢FVb÷WFFU÷&÷f–FW%ö†VÇ‡6VÆb’ÓâæöæS ¢&W6WBÒ&÷f–FW%÷&W6WB‡6VÆbæ7F—fUö•÷&÷f–FW"¢6VÆbç&÷f–FW%ö†VÇæ6öæf–wW&R‡FW‡C×&W6WBæFW67&—F–öâ¢æÖW2Ò"ò"æ¦ö–â‡&W6WBæVçf—&öæÖVçEö¶W—2¢6VÆbæ•ö¶W•ö†–çBæ6öæf–wW&R‡FW‡CÖb.K™şXúşYÊY
şXªX˜ŞŠëî{ÚîxêşZ(>Xù˜xşûÉ§¶æÖW7Ò" ¢FVböÆöE÷&÷f–FW%ö•ö¶W’‡6VÆbÂ&÷f–FW%ö–C¢7G"’Óâ7G# ¢–b&÷f–FW%ö–B–â6VÆbæ•ö¶W—3 ¢&WGW&â6VÆbæ•ö¶W—5·&÷f–FW%ö–EĞ¢fÇVRÒ" ¢–b6VÆbç&VÖVÖ&W%ö•ö¶W’ævWB‚“ ¢G'“ ¢fÇVRÒÆöEö•ö¶W’‡&÷f–FW%ö–B¢W†6WB6V7&WE7F÷&TW'&÷# ¢fÇVRÒ" ¢fÇVRÒfÇVR÷"•ö¶W•ög&öÕöVçf—&öæÖVçB‡&÷f–FW%ö–B¢6VÆbæ•ö¶W—5·&÷f–FW%ö–EÒÒfÇVP¢&WGW&âfÇVP ¢FVb÷W'6—7Eö7W'&VçEö•ö¶W’‡6VÆb’ÓâæöæS ¢&÷f–FW%ö–BÒ6VÆbæ7F—fUö•÷&÷f–FW ¢fÇVRÒ6VÆbæ•ö¶W’ævWB‚’ç7G&—‚¢6VÆbæ•ö¶W—5·&÷f–FW%ö–EÒÒfÇVP¢–b6VÆbç&VÖVÖ&W%ö•ö¶W’ævWB‚’æBfÇVS ¢6fUö•ö¶W’‡&÷f–FW%ö–BÂfÇVR¢VÇ6S ¢FVÆWFUö•ö¶W’‡&÷f–FW%ö–B ¢FVb6ÆV%÷6fVEö•ö¶W’‡6VÆb’ÓâæöæS ¢&÷f–FW%ö–BÒ6VÆbæ7F—fUö•÷&÷f–FW ¢G'“ ¢FVÆWFUö•ö¶W’‡&÷f–FW%ö–B¢W†6WB6V7&WE7F÷&TW'&÷"2W†3 ¢ÖW76vV&÷‚ç6†÷vW'&÷"‚.kˆ^™šNZK‹JR"Â7G"†W†2’¢&WGW&à¢6VÆbæ•ö¶W—5·&÷f–FW%ö–EÒÒ" ¢6VÆbæ•ö¶W’ç6WB‚""¢ÖW76vV&÷‚ç6†÷v–æfò‚.[{.kˆ^™šB"Âb'·&÷f–FW%÷&W6WB‡&÷f–FW%ö–B’æÆ&VÇÒy¨N[{.KùŞZÙ‚’¶W’[{.K¸î{;¾{¹şXzŞhÚîKŠŞXŠ™šN8"" ¢FVb÷F…öf–VÆB‡6VÆbÂ&VçBÂÆ&VÃ¢7G"Âf&–&ÆS¢7G&–æuf"ÂW†V7WF&ÆUöæÖS¢7G"’ÓâæöæS ¢6VÆbåöf–VÆEöÆ&VÂ‡&VçBÂÆ&VÂ’ç6²†æ6†÷#Ò'r"ÂG“Òƒ2ÂR’¢&÷rÒg&ÖR‡&VçBÂ&sÕ5U$d4R¢&÷rç6²†f–ÆÃÕ‚¢6VÆbåöVçG'’‡&÷rÂf&–&ÆR’ç6²‡6–FSÔÄTeBÂf–ÆÃÕ‚ÂW‡æCÕG'VRÂ—G“Ór ¢FVb'&÷w6R‚’ÓâæöæS ¢–b7—2çÆFf÷&ÒÓÒ&F'v–â"æBW†V7WF&ÆUöæÖRÓÒ$¦–ç––æu&ò# ¢F‚Òf–ÆVF–Æöræ6¶÷Væf–ÆVæÖR€¢F—FÆSÒ.˜hºXš®iŠK‰>K‰®x˜‚æûÈ˜	®[‹KØŞK¨î(	Î[©NyJzˆ¾[¨ş(	ŞûÈ’"À¢–æ—F–ÆF—#Ò"ôÆ–6F–öç2"À¢f–ÆWG—W3Õ²‚$Ö2[©NyJ‚"Â"¢æ"’Â‚.h˜iÈih~K»b"Â"¢â¢"•ÒÀ¢¢VÆ–b7—2çÆFf÷&ÒÓÒ&F'v–â# ¢F‚Òf–ÆVF–Æöræ6¶÷Væf–ÆVæÖR‡F—FÆSÖb.˜hº’¶W†V7WF&ÆUöæÖWÒ"¢VÇ6S ¢F‚Òf–ÆVF–Æöræ6¶÷Væf–ÆVæÖR‡F—FÆSÖb.˜hº’¶W†V7WF&ÆUöæÖWÒæW†R"Âf–ÆWG—W3Õ²‚.Xúşhš~ŠÎih~K»b"Â"¢æW†R"’Â‚.h˜iÈih~K»b"Â"¢â¢"•Ò¢–bFƒ ¢f&–&ÆRç6WB‡F‚ ¢6VÆbåö'WGFöâ‡&÷rÂ.˜hº’"Â'&÷w6RÂ¶–æCÒ&v†÷7B"’ç6²‡6–FSÕ$”t…BÂGƒÒƒrÂ’ ¢FVböF—&V7F÷'•öf–VÆB‡6VÆbÂ&VçBÂÆ&VÃ¢7G"Âf&–&ÆS¢7G&–æuf"’ÓâæöæS ¢6VÆbåöf–VÆEöÆ&VÂ‡&VçBÂÆ&VÂ’ç6²†æ6†÷#Ò'r"ÂG“Òƒ2ÂR’¢&÷rÒg&ÖR‡&VçBÂ&sÕ5U$d4R¢&÷rç6²†f–ÆÃÕ‚¢6VÆbåöVçG'’‡&÷rÂf&–&ÆR’ç6²‡6–FSÔÄTeBÂf–ÆÃÕ‚ÂW‡æCÕG'VRÂ—G“Ór ¢FVb'&÷w6R‚’ÓâæöæS ¢F‚Òf–ÆVF–Æöræ6¶F—&V7F÷'’‡F—FÆSÒ.˜hºXš®iŠˆØz‹şyºî[ÙR"¢–bFƒ ¢f&–&ÆRç6WB‡F‚ ¢6VÆbåö'WGFöâ‡&÷rÂ.˜hº’"Â'&÷w6RÂ¶–æCÒ&v†÷7B"’ç6²‡6–FSÕ$”t…BÂGƒÒƒrÂ’ ¢FVb6fU÷6WGF–æw2‡6VÆb’ÓâæöæS ¢6WGF–æw2Ò6VÆbç7FFU²'6WGF–æw2%Ğ¢6V7&WEöW'&÷"Ò" ¢–b†6GG"‡6VÆbÂ&&6U÷W&Å÷f""“ ¢6WGF–æw5²'&÷f–FW"%ÒÒ6VÆbæ7F—fUö•÷&÷f–FW ¢6WGF–æw5²&&6U÷W&Â%ÒÒ6VÆbæ&6U÷W&Å÷f"ævWB‚’ç7G&—‚’ç'7G&—‚"ò"¢6WGF–æw5²&ÖöFVÂ%ÒÒ6VÆbæÖöFVÅöæÖU÷f"ævWB‚’ç7G&—‚¢6WGF–æw5²'&VÖVÖ&W%ö•ö¶W’%ÒÒ6VÆbç&VÖVÖ&W%ö•ö¶W’ævWB‚¢G'“ ¢6VÆbå÷W'6—7Eö7W'&VçEö•ö¶W’‚¢W†6WB6V7&WE7F÷&TW'&÷"2W†3 ¢6V7&WEöW'&÷"Ò7G"†W†2¢6WGF–æw5²&ff×Vu÷F‚%ÒÒ6VÆbæff×Vu÷f"ævWB‚’ç7G&—‚¢6WGF–æw5²&fg&ö&U÷F‚%ÒÒ6VÆbæfg&ö&U÷f"ævWB‚’ç7G&—‚¢6WGF–æw5²&¦–ç––æuöW†R%ÒÒ6VÆbæ¦–ç––æu÷f"ævWB‚’ç7G&—‚¢6WGF–æw5²&¦–ç––æuöG&gG5÷F‚%ÒÒ6VÆbæ¦–ç––æuöG&gG5÷f"ævWB‚’ç7G&—‚¢6VÆbç7F÷&Rç6fR‡6VÆbç7FFR¢6VÆbå÷&Vg&W6…÷FööÅ÷7FGW2‚¢–b†6GG"‡6VÆbÂ&ff×Vu÷7FGW2"“ ¢FWFV7FVBÒf–æEöW†V7WF&ÆR‡6WGF–æw5²&ff×Vu÷F‚%ÒÂ&ff×Vr"¢6VÆbæff×Vu÷7FGW2æ6öæf–wW&R‡FW‡CÒ†b.[{.h›îX‹ûÉ§¶FWFV7FVGÒ"–bFWFV7FVBVÇ6R.[	®iÊ®h›îX‹df×V~ûÉ¾K¸ŞXúşX‹nKÙÎY»îx˜~ûÈÎKØnKˆŞˆ;ŞYh‰™ÙhkÊ¾Šxnš)8""’ÂfsÔ44TåEôD$²–bFWFV7FVBVÇ6Rt$Ò¢–b6V7&WEöW'&÷# ¢ÖW76vV&÷‚ç6†÷wv&æ–ær‚.Šëî{Úî[{.KùŞZÙ‚"Âb.jŠYè¾Y(Î[z^X[~Šëî{Úî[{.KùŞZÙûÈÎKØb’¶W’iÊ®ˆ;ŞZèXZKùŞZÙûÉ¥Æç·6V7&WEöW'&÷'Ò"¢VÆ–b6VÆbç&VÖVÖ&W%ö•ö¶W’ævWB‚“ ¢ÖW76vV&÷‚ç6†÷v–æfò‚.[{.KùŞZÙ‚"Â.jŠYè¾8Xš®iŠˆØz‹şKˆîŠxnš)[z^X[~Šëî{Úî[{.KùŞZÙ8$’¶W’[{.yK{;¾{¹şZèXZKùŞzêûÈÎKˆ¾jÊh™>[ÈKÉ®ˆz®XªZ¾XZ^8""¢VÇ6S ¢ÖW76vV&÷‚ç6†÷v–æfò‚.[{.KùŞZÙ‚"Â.jŠYè¾8Xš®iŠˆØz‹şKˆîŠxnš)[z^X[~Šëî{Úî[{.KùŞZÙ8$’¶W’iÊ®Š*¾ŠëKØş8"" ¢FVbö•ö6Æ–VçB‡6VÆbÂW6Uöf÷&Ó¢&ööÂÒfÇ6R’Óâ÷Vä”6ö×F–&ÆT6Æ–VçC ¢6WGF–æw2Ò6VÆbç7FFU²'6WGF–æw2%Ğ¢–bW6Uöf÷&ÒæB†6GG"‡6VÆbÂ&&6U÷W&Å÷f""“ ¢&6U÷W&ÂÒ6VÆbæ&6U÷W&Å÷f"ævWB‚¢ÖöFVÂÒ6VÆbæÖöFVÅöæÖU÷f"ævWB‚¢&÷f–FW%ö–BÒ6VÆbæ7F—fUö•÷&÷f–FW ¢VÇ6S ¢&6U÷W&ÂÒ6WGF–æw2ævWB‚&&6U÷W&Â"Â""¢ÖöFVÂÒ6WGF–æw2ævWB‚&ÖöFVÂ"Â""¢&÷f–FW%ö–BÒ6WGF–æw2ævWB‚'&÷f–FW""’÷"–æfW%÷&÷f–FW"†&6U÷W&ÂÂÖöFVÂ¢6öæf–rÒ”6öæf–r†&6U÷W&ÂÂÖöFVÂÂ6VÆbæ•ö¶W’ævWB‚’Â&÷f–FW#×&÷f–FW%ö–B¢–bæ÷B6öæf–ræ&6U÷W&Ã ¢&—6R”6Æ–VçDW'&÷"‚.Šû~XXZ¾XijŠYè²&6RU$Î8""¢–bæ÷B6öæf–ræ•ö¶W“ ¢&—6R”6Æ–VçDW'&÷"‚.Šû~XXYÊ(	ÎjŠYè¾Kˆî[z^X[~(	ŞKŠŞZ¾Xi’’¶W8""¢&WGW&â÷Vä”6ö×F–&ÆT6Æ–VçB†6öæf–r ¢FVbFW7Eö•ö6öææV7F–öâ‡6VÆb’ÓâæöæS ¢–b6VÆbæ—5ö'W7“ ¢&WGW&à¢G'“ ¢6Æ–VçBÒ6VÆbåö•ö6Æ–VçB‡W6Uöf÷&ÓÕG'VR¢W†6WB”6Æ–VçDW'&÷"2W†3 ¢ÖW76vV&÷‚ç6†÷wv&æ–ær‚.izk9^kX¾ŠùR"Â7G"†W†2’¢&WGW&à¢6VÆbæ—5ö'W7’ÒG'VP¢6VÆbæ•÷FW7E÷7FGW2æ6öæf–wW&R‡FW‡CÖb.jÚ>YÊ‹ùîhêR·&÷f–FW%÷&W6WB‡6VÆbæ7F—fUö•÷&÷f–FW"’æÆ&VÇŞ(
b"ÂfsÔ44TåEôD$² ¢FVbv÷&¶W"‚’ÓâæöæS ¢G'“ ¢&WÇ’Ò6Æ–VçBæ6ö×ÆWFR‚.KÚiŠşhê^Xú>‹ùî˜	®h
~kX¾Šù^Xªh˜¾8""Â.Šû~Xú®Y¹îZHŞûÉ®‹ùîhê^h‰X©ò"ÂFV×W&GW&SÓã¢6VÆbæ'W2çWB‚‚&•÷FW7Eö6ö×ÆWFR"Â&WÇ’’¢W†6WBW†6WF–öâ2W†3¢2æ÷¢$ÄSÒF—7Æ’&÷f–FW"W'&÷"–âF†RT¢6VÆbæ'W2çWB‚‚&•÷FW7EöW'&÷""ÂW†2’ ¢F‡&VF–æråF‡&VB‡F&vWC×v÷&¶W"ÂFVÖöãÕG'VR’ç7F'B‚ ¢FVbö†æFÆU÷6WGF–æw5öWfVçB‡6VÆbÂWfVçC¢7G"Â–ÆöC¢ö&¦V7B’ÓâæöæS ¢–bWfVçBÓÒ&•÷FW7Eö6ö×ÆWFR# ¢6VÆbæ—5ö'W7’ÒfÇ6P¢6VÆbæ•÷FW7E÷7FGW2æ6öæf–wW&R‡FW‡CÖb.‹ùîhê^h‰X©şûÉ§·7G"‡–ÆöB•³£S×Ò"ÂfsÔ44TåEôD$²¢6V7&WEöW'&÷"Ò" ¢–b6VÆbç&VÖVÖ&W%ö•ö¶W’ævWB‚“ ¢G'“ ¢6VÆbå÷W'6—7Eö7W'&VçEö•ö¶W’‚¢W†6WB6V7&WE7F÷&TW'&÷"2W†3 ¢6V7&WEöW'&÷"Ò7G"†W†2¢ÖW76vRÒb'·&÷f–FW%÷&W6WB‡6VÆbæ7F—fUö•÷&÷f–FW"’æÆ&VÇÒhê^Xú>XúşKº^jÚ>[‹KÛşyJ8" ¢–b6VÆbç&VÖVÖ&W%ö•ö¶W’ævWB‚’æBæ÷B6V7&WEöW'&÷# ¢ÖW76vR³Ò%Æä’¶W’[{.ZèXZŠëKØş8" ¢VÆ–b6V7&WEöW'&÷# ¢ÖW76vR³Òb%ÆîKØb’¶W’KùŞZÙZK‹J^ûÉ§·6V7&WEöW'&÷'Ò ¢ÖW76vV&÷‚ç6†÷v–æfò‚.‹ùîhê^h‰X©ò"ÂÖW76vR¢VÆ–bWfVçBÓÒ&•÷FW7EöW'&÷"# ¢6VÆbæ—5ö'W7’ÒfÇ6P¢6VÆbæ•÷FW7E÷7FGW2æ6öæf–wW&R‡FW‡CÒ.‹ùîhê^ZK‹J^ûÈÎŠû~j8iúR¶W8jŠYè¾YŞY(Î{Ù{¹Â"ÂfsÔU%$õ"¢ÖW76vV&÷‚ç6†÷vW'&÷"‚.‹ùîhê^ZK‹JR"Â7G"‡–ÆöB’ ¢2ÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒWF–Æ—F–W2ÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒĞ¢FVbö6÷•÷FW‡B‡6VÆbÂfÇVS¢7G"’ÓâæöæS ¢6VÆbç&ö÷Bæ6Æ—&ö&Eö6ÆV"‚¢6VÆbç&ö÷Bæ6Æ—&ö&EöVæB‡fÇVR ¢FVb÷6fUö7W'&VçEöVF—F÷'2‡6VÆb’ÓâæöæS ¢–b6VÆbæ7W'&VçE÷vRÓÒ'f–FVò"æB6VÆbç÷7EöVF—F÷# ¢6VÆbå÷7–æ5÷f–FVõ÷7FFR‚¢VÆ–b6VÆbæ7W'&VçE÷vRÓÒ&æ÷fVÂ# ¢6VÆbå÷7–æ5öæ÷fVÅ÷'VÆW2‚¢6VÆbå÷6fUö6†FW%öVF—F÷'2‚¢6VÆbç7F÷&Rç6fR‡6VÆbç7FFR¢VÆ–b6VÆbæ7W'&VçE÷vRÓÒ&6öÖ–2# ¢6VÆbç6fUö6öÖ–5÷6WGF–æw2‡6–ÆVçCÕG'VR ¢FVböG&–åö'W2‡6VÆb’ÓâæöæS ¢G'“ ¢v†–ÆRG'VS ¢WfVçBÂ–ÆöBÒ6VÆbæ'W2ævWEöæ÷v—B‚¢–b6VÆbæ'W5ö†æFÆW# ¢6VÆbæ'W5ö†æFÆW"†WfVçBÂ–ÆöB¢W†6WBVWVRäV×G“ ¢70¢6VÆbç&ö÷BægFW"ƒ#Â6VÆbåöG&–åö'W2 ¢FVböåö6Æ÷6R‡6VÆb’ÓâæöæS ¢–b6VÆbæ—5ö'W7’æBæ÷BÖW76vV&÷‚æ6·–W6æò‚.K»¾XªK¸ŞYÊ‹ù¾ŠÂ"Â.X[>™zŞ[©NyJKÉ®KŠŞijŞ[Ù>X˜ŞK»¾XªûÈÎzîZé®˜X{®Y	~ûÉò"“ ¢&WGW&à¢6VÆbå÷6fUö7W'&VçEöVF—F÷'2‚¢–b†6GG"‡6VÆbÂ'&VÖVÖ&W%ö•ö¶W’"“ ¢6VÆbç7FFU²'6WGF–æw2%Õ²'&VÖVÖ&W%ö•ö¶W’%ÒÒ6VÆbç&VÖVÖ&W%ö•ö¶W’ævWB‚¢G'“ ¢6VÆbå÷W'6—7Eö7W'&VçEö•ö¶W’‚¢W†6WB6V7&WE7F÷&TW'&÷# ¢70¢–b†6GG"‡6VÆbÂ'&VÖVÖ&W%ö&µö•ö¶W’"“ ¢6VÆbç7FFU²'6WGF–æw2%Õ²'&VÖVÖ&W%ö&µö•ö¶W’%ÒÒ6VÆbç&VÖVÖ&W%ö&µö•ö¶W’ævWB‚¢G'“ ¢–b6VÆbç&VÖVÖ&W%ö&µö•ö¶W’ævWB‚’æB6VÆbæ&µö•ö¶W’ævWB‚’ç7G&—‚“ ¢6fUö•ö¶W’‚&&²"Â6VÆbæ&µö•ö¶W’ævWB‚’ç7G&—‚’¢VÇ6S ¢FVÆWFUö•ö¶W’‚&&²"¢W†6WB6V7&WE7F÷&TW'&÷# ¢70¢6VÆbç7F÷&Rç6fR‡6VÆbç7FFR¢6VÆbç7F÷&Rç&VÆV6Uö–ç7Fæ6UöÆö6²‚¢6VÆbç&ö÷BæFW7G&÷’‚  ¦FVbÖ–â‚’ÓâæöæS ¢&ö÷BÒF²‚¢G'“ ¢7GVF–ô‡&ö÷B¢W†6WB7GVF–ô–ç7Fæ6U'Vææ–ætW'&÷"2W†3 ¢&ö÷Bçv—F†G&r‚¢ÖW76vV&÷‚ç6†÷wv&æ–ær‚.zˆ¾[¨ş[{.YÊ‹ùŠÂ"Â7G"†W†2’Â&VçC×&ö÷B¢&ö÷BæFW7G&÷’‚¢&WGW&à¢&ö÷BæÖ–æÆö÷‚  ¦FVb6¶vVE÷6VÆe÷FW7B‚’ÓâæöæS ¢""$W†W&6—6RæF—fRÖVF–'6–æræB¦–ç––ærG&gB76WG2–â6¶vRâ"" ¢–×÷'BFV×f–ÆP ¢g&öÒ–ÖVF––æfò–×÷'BÖVF––æfğ¢–×÷'B”¦–å––ætG&gB2G&g@ ¢–bæ÷B6ÆÆ&ÆR†vWFGG"„ÖVF––æfòÂ''6R"ÂæöæR’“ ¢&—6R'VçF–ÖTW'&÷"‚$ÖVF––æfò'6W"Væf–Æ&ÆR"¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2FV× ¢67&—BÒG&gBäG&gDföÆFW"‡FV×’æ7&VFUöG&gB‚'6VÆb×FW7B"ÂƒÂ“#Â3¢67&—Bç6fR‚¢–bæ÷B…F‚‡FV×’ò'6VÆb×FW7B"ò&G&gEö6öçFVçBæ§6öâ"’æ—5öf–ÆR‚“ ¢&—6R'VçF–ÖTW'&÷"‚$¦–ç––ærG&gB76WG2Væf–Æ&ÆR"  ¦–bõöæÖUõòÓÒ%õöÖ–åõò# ¢–b"Ò×6VÆb×FW7B"–â7—2æ&wc ¢6¶vVE÷6VÆe÷FW7B‚¢VÇ6S ¢Ö–â‚ 