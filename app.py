from __future__ import annotations

import os
import queue
import sys
import threading
import zipfile
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
    DoubleVar,
    Frame,
    IntVar,
    Label,
    Listbox,
    Menu,
    StringVar,
    Text,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    ttk,
)
from xml.etree import ElementTree

from core.ai_client import (
    PROVIDER_PRESETS,
    AIClientError,
    AIConfig,
    OpenAICompatibleClient,
    api_key_from_environment,
    infer_provider,
    provider_preset,
)
from core.jianying_engine import (
    JianyingEngineError,
    create_jianying_draft,
    detect_jianying_drafts_path,
    detect_jianying_executable,
    open_jianying,
    probe_audio_duration,
)
from core.novel_engine import build_post_prompt, build_rewrite_prompt, chapter_records
from core.storage import StateStore
from core.video_engine import (
    VideoClip,
    VideoProject,
    build_export_command,
    find_executable,
    probe_duration,
    run_export,
)


APP_NAME = "è§£å‹åˆ›ä½œå·¥åŠ"
APP_VERSION = "0.2.0"
BG = "#F2F4F1"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#E9EFEA"
INK = "#18201C"
MUTED = "#69746D"
SIDEBAR = "#14231C"
SIDEBAR_MUTED = "#9FB1A7"
ACCENT = "#55D08B"
ACCENT_DARK = "#167450"
WARM = "#F2A55F"
ERROR = "#C44D56"
BORDER = "#DDE4DF"


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


class StudioApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.store = StateStore()
        self.state = self.store.load()
        settings = self.state["settings"]
        initial_provider = settings.get("provider") or infer_provider(settings.get("base_url", ""), settings.get("model", ""))
        self.active_api_provider = initial_provider if initial_provider in {item.id for item in PROVIDER_PRESETS} else "custom"
        self.api_keys: dict[str, str] = {}
        self.api_key = StringVar(value=api_key_from_environment(self.active_api_provider))
        self.current_page = "dashboard"
        self.nav_buttons: dict[str, object] = {}
        self.bus: queue.Queue[tuple[str, object]] = queue.Queue()
        self.bus_handler = None
        self.video_tree: ttk.Treeview | None = None
        self.novel_list: Listbox | None = None
        self.source_editor: Text | None = None
        self.result_editor: Text | None = None
        self.post_editor: Text | None = None
        self.current_chapter_index: int | None = None
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
        style.configure("Studio.Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=INK, rowheight=34, borderwidth=0, font=("Microsoft YaHei UI", 10))
        style.configure("Studio.Treeview.Heading", background=SURFACE_ALT, foreground=MUTED, relief="flat", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Studio.Treeview", background=[("selected", "#DDF6E8")], foreground=[("selected", INK)])
        style.configure("Studio.TCombobox", padding=7, fieldbackground=SURFACE, background=SURFACE, foreground=INK)
        style.configure("Studio.Horizontal.TProgressbar", background=ACCENT, troughcolor=SURFACE_ALT, borderwidth=0)
        style.configure("Studio.TPanedwindow", background=BG)

    def _build_shell(self) -> None:
        self.sidebar = Frame(self.root, bg=SIDEBAR, width=236)
        self.sidebar.pack(side=LEFT, fill=Y)
        self.sidebar.pack_propagate(False)

        brand = Frame(self.sidebar, bg=SIDEBAR)
        brand.pack(fill=X, padx=24, pady=(28, 34))
        Label(brand, text="â—‰", bg=SIDEBAR, fg=ACCENT, font=("Segoe UI Symbol", 26)).pack(anchor="w")
        Label(brand, text=APP_NAME, bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", pady=(8, 2))
        Label(brand, text="VIDEO Ã— STORY STUDIO", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")

        nav_items = [
            ("dashboard", "âŒ‚  åˆ›ä½œé¦–é¡µ"),
            ("video", "â–¶  è§†é¢‘æ··å‰ª"),
            ("novel", "æ–‡  å°è¯´æ”¹æ–‡"),
            ("settings", "âš™  æ¨¡å‹ä¸å·¥å…·"),
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
        Label(footer, text=f"ç‰ˆæœ¬ {APP_VERSION}", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 6))
        Label(footer, text="æœ¬åœ°é¡¹ç›®è‡ªåŠ¨ä¿å­˜", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        self.tool_status = Label(footer, text="æ­£åœ¨æ£€æŸ¥å·¥å…·â€¦", bg=SIDEBAR, fg=WARM, font=("Microsoft YaHei UI", 9))
        self.tool_status.pack(anchor="w", pady=(6, 0))
        self._refresh_tool_status()

        self.main = Frame(self.root, bg=BG)
        self.main.pack(side=LEFT, fill=BOTH, expand=True)

    def _refresh_tool_status(self) -> None:
        settings = self.state["settings"]
        ready = bool(
            detect_jianying_executable(settings.get("jianying_exe", ""))
            and detect_jianying_drafts_path(settings.get("jianying_drafts_path", ""))
        )
        self.tool_status.configure(text="â— å‰ªæ˜ è¿æ¥å·²å°±ç»ª" if ready else "â— å‰ªæ˜ è·¯å¾„å¾…é…ç½®", fg=ACCENT if ready else WARM)

    def navigate(self, page: str) -> None:
        if self.is_busy and page != self.current_page:
            messagebox.showinfo("ä»»åŠ¡è¿›è¡Œä¸­", "å½“å‰ä»»åŠ¡å®Œæˆåå†åˆ‡æ¢å·¥ä½œå°ã€‚")
            return
        self._save_current_editors()
        self.current_page = page
        for key, button in self.nav_buttons.items():
            button.configure(bg="#20372D" if key == page else SIDEBAR, fg="white" if key == page else SIDEBAR_MUTED)
        if page == "dashboard":
            self.show_dashboard()
        elif page == "video":
            self.show_video()
        elif page == "novel":
            self.show_novel()
        else:
            self.show_settings()

    def _clear_main(self) -> None:
        for child in self.main.winfo_children():
            child.destroy()
        self.video_tree = None
        self.novel_list = None
        self.source_editor = None
        self.result_editor = None
        self.post_editor = None
        self.bus_handler = None

    def _page_header(self, title: str, subtitle: str, actions: list[tuple[str, object, str]] | None = None) -> Frame:
        header = Frame(self.main, bg=BG)
        header.pack(fill=X, padx=34, pady=(28, 18))
        text_area = Frame(header, bg=BG)
        text_area.pack(side=LEFT)
        Label(text_area, text=title, bg=BG, fg=INK, font=("Microsoft YaHei UI", 23, "bold")).pack(anchor="w")
        Label(text_area, text=subtitle, bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(5, 0))
        if actions:
            action_area = Frame(header, bg=BG)
            action_area.pack(side=RIGHT, pady=5)
            for label, command, kind in actions:
                self._button(action_area, label, command, kind=kind).pack(side=LEFT, padx=(8, 0))
        return header

    def _card(self, parent, *, bg: str = SURFACE, padx: int = 20, pady: int = 18) -> Frame:
        outer = Frame(parent, bg=BORDER, padx=1, pady=1)
        inner = Frame(outer, bg=bg, padx=padx, pady=pady)
        inner.pack(fill=BOTH, expand=True)
        return outer

    def _button(self, parent, text: str, command, *, kind: str = "primary", width: int | None = None):
        palette = {
            "primary": (ACCENT_DARK, "white", "#105F42"),
            "accent": (ACCENT, SIDEBAR, "#45BE7A"),
            "ghost": (SURFACE_ALT, INK, "#DCE7DF"),
            "danger": ("#FBEAEC", ERROR, "#F4DADD"),
            "dark": (SIDEBAR, "white", "#20372D"),
        }
        bg, fg, active = palette[kind]
        return __import__("tkinter").Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground=fg,
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=9,
            width=width,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    def _entry(self, parent, variable: StringVar | DoubleVar | IntVar, width: int | None = None):
        return __import__("tkinter").Entry(
            parent,
            textvariable=variable,
            width=width,
            bg=SURFACE,
            fg=INK,
            insertbackground=INK,
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 10),
        )

    def _field_label(self, parent, text: str) -> Label:
        return Label(parent, text=text, bg=parent.cget("bg"), fg=MUTED, font=("Microsoft YaHei UI", 9))

    def show_dashboard(self) -> None:
        self._clear_main()
        self.current_page = "dashboard"
        self.navigate_highlight("dashboard")
        self._page_header("ä»Šå¤©æƒ³åˆ›ä½œä»€ä¹ˆï¼Ÿ", "ä»ç´ æåˆ°æˆç‰‡ï¼Œä»åŸæ–‡åˆ°æ–°ç¨¿ï¼Œæ‰€æœ‰è¿›åº¦éƒ½ä¿å­˜åœ¨æœ¬æœºã€‚")

        body = Frame(self.main, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=34, pady=(2, 28))

        hero_outer = self._card(body, bg=SIDEBAR, padx=28, pady=26)
        hero_outer.pack(fill=X)
        hero = hero_outer.winfo_children()[0]
        left = Frame(hero, bg=SIDEBAR)
        left.pack(side=LEFT, fill=X, expand=True)
        Label(left, text="ä¸€ä¸ªå®‰é™ã€å®Œæ•´çš„æœ¬åœ°åˆ›ä½œæµç¨‹", bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        Label(left, text="å¯¼å…¥ç´ æ Â· è®¾å®šè§„åˆ™ Â· AI è¾…åŠ© Â· æ£€æŸ¥ç»“æœ Â· æœ¬åœ°å¯¼å‡º", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(8, 0))
        self._button(hero, "å¼€å§‹è§†é¢‘æ··å‰ª  â†’", lambda: self.navigate("video"), kind="accent").pack(side=RIGHT, padx=(18, 0))

        cards = Frame(body, bg=BG)
        cards.pack(fill=BOTH, expand=True, pady=(18, 0))
        cards.grid_columnconfigure(0, weight=1)
        cards.grid_columnconfigure(1, weight=1)
        cards.grid_rowconfigure(0, weight=1)

        video_card = self._card(cards, padx=26, pady=24)
        video_card.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        v = video_card.winfo_children()[0]
        Label(v, text="VIDEO MIX", bg=SURFACE, fg=ACCENT_DARK, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        Label(v, text="è§£å‹è§†é¢‘æ··å‰ª", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w", pady=(8, 6))
        Label(v, text="æ‰¹é‡æ’åˆ—ç´ æã€ç»Ÿä¸€æ¯”ä¾‹ã€æ·»åŠ è½¬åœºä¸éŸ³ä¹ï¼Œå¹¶ç”Ÿæˆå¹³å°å‘å¸ƒæ–‡æ¡ˆã€‚", bg=SURFACE, fg=MUTED, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        video = self.state["video"]
        self._metric_row(v, [("ç´ æ", len(video["clips"])), ("ç”»å¹…", video["aspect"]), ("é¢„è®¡æ—¶é•¿", f"{self._video_duration():.1f}s")])
        self._button(v, "è¿›å…¥å·¥ä½œå°", lambda: self.navigate("video"), kind="ghost").pack(anchor="w", pady=(22, 0))

        novel_card = self._card(cards, padx=26, pady=24)
        novel_card.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
        n = novel_card.winfo_children()[0]
        Label(n, text="STORY REWRITE", bg=SURFACE, fg="#B66B2E", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        Label(n, text="å°è¯´æ”¹æ–‡", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w", pady=(8, 6))
        Label(n, text="è‡ªåŠ¨æ‹†ç« ã€ç®¡ç†äººç‰©è®¾å®šã€é€ç« æˆ–æ‰¹é‡æ”¹å†™ï¼Œå¹¶ä¿ç•™åŸæ–‡å¯¹ç…§ã€‚", bg=SURFACE, fg=MUTED, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        novel = self.state["novel"]
        self._metric_row(n, [("ç« èŠ‚", len(novel["chapters"])), ("å·²å®Œæˆ", len(novel["results"])), ("æ¨¡å¼", novel["mode"])])
        self._button(n, "è¿›å…¥å·¥ä½œå°", lambda: self.navigate("novel"), kind="ghost").pack(anchor="w", pady=(22, 0))

        tips_outer = self._card(body, bg="#FFF8F0", padx=22, pady=16)
        tips_outer.pack(fill=X, pady=(18, 0))
        tips = tips_outer.winfo_children()[0]
        Label(tips, text="ä½¿ç”¨æç¤º", bg="#FFF8F0", fg="#8A5527", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        Label(tips, text="AI Key åªä¿ç•™åœ¨æœ¬æ¬¡è¿è¡Œçš„å†…å­˜ä¸­ï¼Œä¸ä¼šå†™å…¥é¡¹ç›®æ–‡ä»¶ï¼›è¯·åªæ”¹å†™ä½ æ‹¥æœ‰ç‰ˆæƒæˆ–å·²è·å¾—æˆæƒçš„å†…å®¹ã€‚", bg="#FFF8F0", fg="#8A6A4E", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(5, 0))

    def _metric_row(self, parent, metrics: list[tuple[str, object]]) -> None:
        row = Frame(parent, bg=SURFACE)
        row.pack(fill=X, pady=(24, 0))
        for label, value in metrics:
            block = Frame(row, bg=SURFACE)
            block.pack(side=LEFT, padx=(0, 38))
            Label(block, text=str(value), bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
            Label(block, text=label, bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w")

    def navigate_highlight(self, page: str) -> None:
        for key, button in self.nav_buttons.items():
            butt×tîÚ$z{-®éÜj×[z^X[r"Â.Xúşy»Nhê^˜hº’FVW6VV¾8XØ>™zî8i›®‹tÄŞ8¶–ÖûÉ´’¶W’KˆŞKÉ®XiXZ^z8y¹8""¢&öG’Òg&ÖR‡6VÆbæÖ–âÂ&sÔ$r¢&öG’ç6²†f–ÆÃÔ$õD‚ÂW‡æCÕG'VRÂGƒÓ3BÂG“ÒƒÂ#‚’¢&öG’æw&–Eö6öÇVÖæ6öæf–wW&RƒÂvV–v‡CÓ¢&öG’æw&–Eö6öÇVÖæ6öæf–wW&RƒÂvV–v‡CÓ ¢ÖöFVÅö÷WFW"Ò6VÆbåö6&B†&öG’ÂGƒÓ#BÂG“Ó#"¢ÖöFVÅö÷WFW"æw&–B‡&÷sÓÂ6öÇVÖãÓÂ7F–6·“Ò&æWr"ÂGƒÒƒÂ’’¢ÖöFVÂÒÖöFVÅö÷WFW"çv–æfõö6†–ÆG&Vâ‚•³Ğ¢Æ&VÂ†ÖöFVÂÂFW‡CÒ$’jŠYè²"Â&sÕ5U$d4RÂfsÔ”ä²ÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"ÂBÂ&&öÆB"’’ç6²†æ6†÷#Ò'r"¢Æ&VÂ†ÖöFVÂÂFW‡CÒ.˜hºiÈŞXªYXnYîKÉ®ˆz®XªZ¾XiZéikhê^Xú>KˆîhêˆÙjŠYè¾ûÈÎK™şXúş{º~{ºŞh˜¾XªKúîiK8""Â&sÕ5U$d4RÂfsÔÕUDTBÂw&ÆVæwFƒÓC3Â§W7F–g“ÔÄTeBÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"Â’’’ç6²†æ6†÷#Ò'r"ÂG“ÒƒBÂ‚’¢6WGF–æw2Ò6VÆbç7FFU²'6WGF–æw2%Ğ¢&÷f–FW%ö–BÒ6WGF–æw2ævWB‚'&÷f–FW""’÷"–æfW%÷&÷f–FW"‡6WGF–æw2ævWB‚&&6U÷W&Â"Â""’Â6WGF–æw2ævWB‚&ÖöFVÂ"Â""’¢–b&÷f–FW%ö–Bæ÷B–â¶—FVÒæ–Bf÷"—FVÒ–â$õd”DU%õ$U4UE7Ó ¢&÷f–FW%ö–BÒ&7W7FöÒ ¢6VÆbæ7F—fUö•÷&÷f–FW"Ò&÷f–FW%ö–@¢–bæ÷B6VÆbæ•ö¶W’ævWB‚’ç7G&—‚“ ¢6VÆbæ•ö¶W’ç6WB‡6VÆbæ•ö¶W—2ævWB‡&÷f–FW%ö–BÂ""’÷"•ö¶W•ög&öÕöVçf—&öæÖVçB‡&÷f–FW%ö–B’¢6VÆbç&÷f–FW%ö–G5ö'•öÆ&VÂÒ¶—FVÒæÆ&VÃ¢—FVÒæ–Bf÷"—FVÒ–â$õd”DU%õ$U4UE7Ğ¢6VÆbç&÷f–FW%öÆ&VÇ5ö'•ö–BÒ¶—FVÒæ–C¢—FVÒæÆ&VÂf÷"—FVÒ–â$õd”DU%õ$U4UE7Ğ¢6VÆbç&÷f–FW%÷f"Ò7G&–æuf"‡fÇVS×6VÆbç&÷f–FW%öÆ&VÇ5ö'•ö–E·&÷f–FW%ö–EÒ¢6VÆbæ&6U÷W&Å÷f"Ò7G&–æuf"‡fÇVS×6WGF–æw5²&&6U÷W&Â%Ò¢6VÆbæÖöFVÅöæÖU÷f"Ò7G&–æuf"‡fÇVS×6WGF–æw5²&ÖöFVÂ%Ò¢6VÆbåöf–VÆEöÆ&VÂ†ÖöFVÂÂ.jŠYè¾iÈŞXªYXb"’ç6²†æ6†÷#Ò'r"ÂG“Òƒ"ÂR’¢&÷f–FW%ö&÷‚ÒGF²ä6öÖ&ö&÷‚€¢ÖöFVÂÀ¢FW‡Gf&–&ÆS×6VÆbç&÷f–FW%÷f"À¢fÇVW3Õ¶—FVÒæÆ&VÂf÷"—FVÒ–â$õd”DU%õ$U4UE5ÒÀ¢7FFSÒ'&VFöæÇ’"À¢7G–ÆSÒ%7GVF–òåD6öÖ&ö&÷‚"À¢¢&÷f–FW%ö&÷‚ç6²†f–ÆÃÕ‚¢&÷f–FW%ö&÷‚æ&–æB‚#ÃÄ6öÖ&ö&÷…6VÆV7FVCãâ"Â6VÆbåöÇ•÷&÷f–FW%÷6VÆV7F–öâ¢6VÆbç&÷f–FW%ö†VÇÒÆ&VÂ†ÖöFVÂÂ&sÕ5U$d4RÂfsÔ44TåEôD$²Âw&ÆVæwFƒÓC3Â§W7F–g“ÔÄTeBÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"Â‚’¢6VÆbç&÷f–FW%ö†VÇç6²†æ6†÷#Ò'r"ÂG“ÒƒbÂ’¢6VÆbå÷6WGF–æw5öVçG'’†ÖöFVÂÂ$&6RU$Â"Â6VÆbæ&6U÷W&Å÷f"¢6VÆbå÷6WGF–æw5öVçG'’†ÖöFVÂÂ.jŠYè¾YŞz{"Â6VÆbæÖöFVÅöæÖU÷f"¢6VÆbåöf–VÆEöÆ&VÂ†ÖöFVÂÂ$’¶WûÈK¸^iÊÎjÊ‹ùŠÎiÈiXûÈ’"’ç6²†æ6†÷#Ò'r"ÂG“Òƒ2ÂR’¢¶W•öVçG'’Ò6VÆbåöVçG'’†ÖöFVÂÂ6VÆbæ•ö¶W’¢¶W•öVçG'’æ6öæf–wW&R‡6†÷sÒ.(
""¢¶W•öVçG'’ç6²†f–ÆÃÕ‚Â—G“Ór¢6VÆbæ•ö¶W•ö†–çBÒÆ&VÂ†ÖöFVÂÂ&sÕ5U$d4RÂfsÔÕUDTBÂw&ÆVæwFƒÓC3Â§W7F–g“ÔÄTeBÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"Â‚’¢6VÆbæ•ö¶W•ö†–çBç6²†æ6†÷#Ò'r"ÂG“ÒƒRÂ’¢6VÆbæ•÷FW7E÷7FGW2ÒÆ&VÂ†ÖöFVÂÂFW‡CÒ.[	®iÊ®kX¾Šù^‹ùîhêR"Â&sÕ5U$d4RÂfsÔÕUDTBÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"Â‚’¢6VÆbæ•÷FW7E÷7FGW2ç6²†æ6†÷#Ò'r"ÂG“Òƒ"Â’¢ÖöFVÅö7F–öç2Òg&ÖR†ÖöFVÂÂ&sÕ5U$d4R¢ÖöFVÅö7F–öç2ç6²†f–ÆÃÕ‚ÂG“ÒƒBÂ’¢6VÆbåö'WGFöâ†ÖöFVÅö7F–öç2Â.kX¾Šù^‹ùîhêR"Â6VÆbçFW7Eö•ö6öææV7F–öâÂ¶–æCÒ&v†÷7B"’ç6²‡6–FSÔÄTeB¢6VÆbåö'WGFöâ†ÖöFVÅö7F–öç2Â.KùŞZÙjŠYè¾Šëî{Úâ"Â6VÆbç6fU÷6WGF–æw2Â¶–æCÒ'&–Ö'’"’ç6²‡6–FSÕ$”t…B¢6VÆbå÷WFFU÷&÷f–FW%ö†VÇ‚¢6VÆbæ'W5ö†æFÆW"Ò6VÆbåö†æFÆU÷6WGF–æw5öWfVç@ ¢FööÅö÷WFW"Ò6VÆbåö6&B†&öG’ÂGƒÓ#BÂG“Ó#"¢FööÅö÷WFW"æw&–B‡&÷sÓÂ6öÇVÖãÓÂ7F–6·“Ò&æWr"ÂGƒÒƒ’Â’¢FööÂÒFööÅö÷WFW"çv–æfõö6†–ÆG&Vâ‚•³Ğ¢Æ&VÂ‡FööÂÂFW‡CÒ.Šxnš)[É^i8â"Â&sÕ5U$d4RÂfsÔ”ä²ÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"ÂBÂ&&öÆB"’’ç6²†æ6†÷#Ò'r"¢Æ&VÂ‡FööÂÂFW‡CÒ.Xš®iŠˆØz‹şXúşy»Nhê^{º~{ºŞ{Én‹éûÉ´df×VrK¸^yJK¨îš)ŞZInZûÎX{¢ÕN8""Â&sÕ5U$d4RÂfsÔÕUDTBÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"Â’’’ç6²†æ6†÷#Ò'r"ÂG“ÒƒBÂ‚’¢6VÆbæff×Vu÷f"Ò7G&–æuf"‡fÇVS×6WGF–æw2ævWB‚&ff×Vu÷F‚"Â""’¢6VÆbæfg&ö&U÷f"Ò7G&–æuf"‡fÇVS×6WGF–æw2ævWB‚&fg&ö&U÷F‚"Â""’¢FWFV7FVEö¦–ç––ærÒFWFV7Eö¦–ç––æuöW†V7WF&ÆR‡6WGF–æw2ævWB‚&¦–ç––æuöW†R"Â""’’÷"6WGF–æw2ævWB‚&¦–ç––æuöW†R"Â""¢FWFV7FVEöG&gG2ÒFWFV7Eö¦–ç––æuöG&gG5÷F‚‡6WGF–æw2ævWB‚&¦–ç––æuöG&gG5÷F‚"Â""’’÷"6WGF–æw2ævWB‚&¦–ç––æuöG&gG5÷F‚"Â""¢6VÆbæ¦–ç––æuöW†U÷f"Ò7G&–æuf"‡fÇVSÖFWFV7FVEö¦–ç––ær¢6VÆbæ¦–ç––æuöG&gG5÷f"Ò7G&–æuf"‡fÇVSÖFWFV7FVEöG&gG2¢¦–ç––æuöÆ&VÂÒ.Xš®iŠK‰>K‰®x˜[©NyJûÈ‚æûÈ’"–b7—2çÆFf÷&ÒÓÒ&F'v–â"VÇ6R.Xš®iŠK‰>K‰®x˜zˆ¾[¨ò ¢6VÆbå÷F…öf–VÆB‡FööÂÂ¦–ç––æuöÆ&VÂÂ6VÆbæ¦–ç––æuöW†U÷f"Â$¦–ç––æu&ò"¢6VÆbåöF—&V7F÷'•öf–VÆB‡FööÂÂ.Xš®iŠˆØz‹şyºî[ÙR"Â6VÆbæ¦–ç––æuöG&gG5÷f"¢W†V7WF&ÆU÷7Vff—‚Ò""–b7—2çÆFf÷&ÒÓÒ&F'v–â"VÇ6R"æW†R ¢6VÆbå÷F…öf–VÆB‡FööÂÂb&ff×Vw¶W†V7WF&ÆU÷7Vff—‡Ò"Â6VÆbæff×Vu÷f"Â&ff×Vr"¢6VÆbå÷F…öf–VÆB‡FööÂÂb&fg&ö&W¶W†V7WF&ÆU÷7Vff—‡Ò"Â6VÆbæfg&ö&U÷f"Â&fg&ö&R"¢FWFV7FVBÒf–æEöW†V7WF&ÆR‡6VÆbæff×Vu÷f"ævWB‚’Â&ff×Vr"¢6VÆbæff×Vu÷7FGW2ÒÆ&VÂ‡FööÂÂFW‡CÒ†b.[{.h›îX‹ûÉ§¶FWFV7FVGÒ"–bFWFV7FVBVÇ6R.[	®iÊ®h›îX‹df×V~ûÉ¾Šxnš){Én‹éXúşKùŞZÙûÈÎKØnKˆŞˆ;ŞyIşh‰h‰x˜~8""’Â&sÕ5U$d4RÂfsÔ44TåEôD$²–bFWFV7FVBVÇ6Rt$ÒÂw&ÆVæwFƒÓC3Â§W7F–g“ÔÄTeBÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"Â’’¢6VÆbæff×Vu÷7FGW2ç6²†æ6†÷#Ò'r"ÂG“Òƒ‚Â’¢6VÆbåö'WGFöâ‡FööÂÂ.KùŞZÙ[z^X[~Šëî{Úâ"Â6VÆbç6fU÷6WGF–æw2Â¶–æCÒ'&–Ö'’"’ç6²†æ6†÷#Ò&R"ÂG“Òƒ#"Â’ ¢æ÷FUö÷WFW"Ò6VÆbåö6&B†&öG’Â&sÒ"4ddc„c"ÂGƒÓ#"ÂG“Ób¢æ÷FUö÷WFW"æw&–B‡&÷sÓÂ6öÇVÖãÓÂ6öÇVÖç7ãÓ"Â7F–6·“Ò&Wr"ÂG“Òƒ‚Â’¢æ÷FRÒæ÷FUö÷WFW"çv–æfõö6†–ÆG&Vâ‚•³Ğ¢æ÷FU÷F—FÆRÒ$Ö2X[ÎZëKˆâdf×VrŠûNiˆâ"–b7—2çÆFf÷&ÒÓÒ&F'v–â"VÇ6R$df×Vr˜XŞ{ÚîŠûNiˆâ ¢–b7—2çÆFf÷&ÒÓÒ&F'v–â# ¢æ÷FU÷FW‡BÒ$Ö2[©NyJKÉ®ˆz®Xªj8iúRD‚KŠŞy¨Bff×Vröfg&ö&^8.ˆØz‹şXúşKº^yIşh‰ûÈÎKØnXš®iŠÖ2ikx˜Xúşˆ;ŞhùzK®Xh^ZëhÙşYØşûÉ¾˜~X‹i{nŠû~[nˆØz‹şKªN{¹’v–æF÷w2x˜Xš®iŠh™>[ÈY(ÎZûÎX{®8" ¢VÇ6S ¢æ÷FU÷FW‡BÒ.ZèŠ8^ZèÎh‰Yî˜hº’&–âih~K»nZKKŠŞy¨Bff×VræW†RKˆâfg&ö&RæW†^8.[©NyJK™şKÉ®ˆz®Xªj8iúRD8v–ävWBÆ–æ·2Y(Î[‹ŠxZèŠ8^yºî[Ù^8" ¢Æ&VÂ†æ÷FRÂFW‡CÖæ÷FU÷F—FÆRÂ&sÒ"4ddc„c"ÂfsÒ"3„SS#r"ÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"ÂÂ&&öÆB"’’ç6²†æ6†÷#Ò'r"¢Æ&VÂ†æ÷FRÂFW‡CÖæ÷FU÷FW‡BÂ&sÒ"4ddc„c"ÂfsÒ"3„dDR"Âw&ÆVæwFƒÓ“Â§W7F–g“ÔÄTeBÂföçCÒ‚$Ö–7&÷6ögB–†V’T’"Â’’’ç6²†æ6†÷#Ò'r"ÂG“ÒƒRÂ’ ¢FVb÷6WGF–æw5öVçG'’‡6VÆbÂ&VçBÂÆ&VÃ¢7G"Âf&–&ÆS¢7G&–æuf"’ÓâæöæS ¢6VÆbåöf–VÆEöÆ&VÂ‡&VçBÂÆ&VÂ’ç6²†æ6†÷#Ò'r"ÂG“Òƒ2ÂR’¢6VÆbåöVçG'’‡&VçBÂf&–&ÆR’ç6²†f–ÆÃÕ‚Â—G“Ór ¢FVböÇ•÷&÷f–FW%÷6VÆV7F–öâ‡6VÆbÂöWfVçCÔæöæR’ÓâæöæS ¢&Wf–÷W2Ò6VÆbæ7F—fUö•÷&÷f–FW ¢6VÆbæ•ö¶W—5·&Wf–÷W5ÒÒ6VÆbæ•ö¶W’ævWB‚’ç7G&—‚¢6VÆV7FVBÒ6VÆbç&÷f–FW%ö–G5ö'•öÆ&VÂævWB‡6VÆbç&÷f–FW%÷f"ævWB‚’Â&7W7FöÒ"¢6VÆbæ7F—fUö•÷&÷f–FW"Ò6VÆV7FV@¢&W6WBÒ&÷f–FW%÷&W6WB‡6VÆV7FVB¢–b6VÆV7FVBÒ&7W7FöÒ# ¢6VÆbæ&6U÷W&Å÷f"ç6WB‡&W6WBæ&6U÷W&Â¢6VÆbæÖöFVÅöæÖU÷f"ç6WB‡&W6WBæÖöFVÂ¢6VÆbæ•ö¶W’ç6WB‡6VÆbæ•ö¶W—2ævWB‡6VÆV7FVBÂ""’÷"•ö¶W•ög&öÕöVçf—&öæÖVçB‡6VÆV7FVB’¢6VÆbå÷WFFU÷&÷f–FW%ö†VÇ‚¢–b†6GG"‡6VÆbÂ&•÷FW7E÷7FGW2"“ ¢6VÆbæ•÷FW7E÷7FGW2æ6öæf–wW&R‡FW‡CÒ.Xˆ~hÚ.iÈŞXªYXnYîŠû~˜xŞikkX¾Šù^‹ùîhêR"ÂfsÔÕUDTB ¢FVb÷WFFU÷&÷f–FW%ö†VÇ‡6VÆb’ÓâæöæS ¢&W6WBÒ&÷f–FW%÷&W6WB‡6VÆbæ7F—fUö•÷&÷f–FW"¢6VÆbç&÷f–FW%ö†VÇæ6öæf–wW&R‡FW‡C×&W6WBæFW67&—F–öâ¢æÖW2Ò"ò"æ¦ö–â‡&W6WBæVçf—&öæÖVçEö¶W—2¢6VÆbæ•ö¶W•ö†–çBæ6öæf–wW&R‡FW‡CÖb.K™şXúşYÊY
şXªX˜ŞŠëî{ÚîxêşZ(>Xù˜xşûÉ§¶æÖW7Ò" ¢FVb÷F…öf–VÆB‡6VÆbÂ&VçBÂÆ&VÃ¢7G"Âf&–&ÆS¢7G&–æuf"ÂW†V7WF&ÆUöæÖS¢7G"’ÓâæöæS ¢6VÆbåöf–VÆEöÆ&VÂ‡&VçBÂÆ&VÂ’ç6²†æ6†÷#Ò'r"ÂG“Òƒ2ÂR’¢&÷rÒg&ÖR‡&VçBÂ&sÕ5U$d4R¢&÷rç6²†f–ÆÃÕ‚¢6VÆbåöVçG'’‡&÷rÂf&–&ÆR’ç6²‡6–FSÔÄTeBÂf–ÆÃÕ‚ÂW‡æCÕG'VRÂ—G“Ór ¢FVb'&÷w6R‚’ÓâæöæS ¢–b7—2çÆFf÷&ÒÓÒ&F'v–â"æBW†V7WF&ÆUöæÖRÓÒ$¦–ç––æu&ò# ¢F‚Òf–ÆVF–Æöræ6¶÷Væf–ÆVæÖR€¢F—FÆSÒ.˜hºXš®iŠK‰>K‰®x˜‚æûÈ˜	®[‹KØŞK¨î(	Î[©NyJzˆ¾[¨ş(	ŞûÈ’"À¢–æ—F–ÆF—#Ò"ôÆ–6F–öç2"À¢f–ÆWG—W3Õ²‚$Ö2[©NyJ‚"Â"¢æ"’Â‚.h˜iÈih~K»b"Â"¢â¢"•ÒÀ¢¢VÆ–b7—2çÆFf÷&ÒÓÒ&F'v–â# ¢F‚Òf–ÆVF–Æöræ6¶÷Væf–ÆVæÖR‡F—FÆSÖb.˜hº’¶W†V7WF&ÆUöæÖWÒ"¢VÇ6S ¢F‚Òf–ÆVF–Æöræ6¶÷Væf–ÆVæÖR‡F—FÆSÖb.˜hº’¶W†V7WF&ÆUöæÖWÒæW†R"Âf–ÆWG—W3Õ²‚.Xúşhš~ŠÎih~K»b"Â"¢æW†R"’Â‚.h˜iÈih~K»b"Â"¢â¢"•Ò¢–bFƒ ¢f&–&ÆRç6WB‡F‚ ¢6VÆbåö'WGFöâ‡&÷rÂ.˜hº’"Â'&÷w6RÂ¶–æCÒ&v†÷7B"’ç6²‡6–FSÕ$”t…BÂGƒÒƒrÂ’ ¢FVböF—&V7F÷'•öf–VÆB‡6VÆbÂ&VçBÂÆ&VÃ¢7G"Âf&–&ÆS¢7G&–æuf"’ÓâæöæS ¢6VÆbåöf–VÆEöÆ&VÂ‡&VçBÂÆ&VÂ’ç6²†æ6†÷#Ò'r"ÂG“Òƒ2ÂR’¢&÷rÒg&ÖR‡&VçBÂ&sÕ5U$d4R¢&÷rç6²†f–ÆÃÕ‚¢6VÆbåöVçG'’‡&÷rÂf&–&ÆR’ç6²‡6–FSÔÄTeBÂf–ÆÃÕ‚ÂW‡æCÕG'VRÂ—G“Ór ¢FVb'&÷w6R‚’ÓâæöæS ¢F‚Òf–ÆVF–Æöræ6¶F—&V7F÷'’‡F—FÆSÒ.˜hºXš®iŠˆØz‹şyºî[ÙR"¢–bFƒ ¢f&–&ÆRç6WB‡F‚ ¢6VÆbåö'WGFöâ‡&÷rÂ.˜hº’"Â'&÷w6RÂ¶–æCÒ&v†÷7B"’ç6²‡6–FSÕ$”t…BÂGƒÒƒrÂ’ ¢FVb6fU÷6WGF–æw2‡6VÆb’ÓâæöæS ¢6WGF–æw2Ò6VÆbç7FFU²'6WGF–æw2%Ğ¢–b†6GG"‡6VÆbÂ&&6U÷W&Å÷f""“ ¢6WGF–æw5²'&÷f–FW"%ÒÒ6VÆbæ7F—fUö•÷&÷f–FW ¢6WGF–æw5²&&6U÷W&Â%ÒÒ6VÆbæ&6U÷W&Å÷f"ævWB‚’ç7G&—‚’ç'7G&—‚"ò"¢6WGF–æw5²&ÖöFVÂ%ÒÒ6VÆbæÖöFVÅöæÖU÷f"ævWB‚’ç7G&—‚¢6VÆbæ•ö¶W—5·6VÆbæ7F—fUö•÷&÷f–FW%ÒÒ6VÆbæ•ö¶W’ævWB‚’ç7G&—‚¢6WGF–æw5²&ff×Vu÷F‚%ÒÒ6VÆbæff×Vu÷f"ævWB‚’ç7G&—‚¢6WGF–æw5²&fg&ö&U÷F‚%ÒÒ6VÆbæfg&ö&U÷f"ævWB‚’ç7G&—‚¢6WGF–æw5²&¦–ç––æuöW†R%ÒÒ6VÆbæ¦–ç––æuöW†U÷f"ævWB‚’ç7G&—‚¢6WGF–æw5²&¦–ç––æuöG&gG5÷F‚%ÒÒ6VÆbæ¦–ç––æuöG&gG5÷f"ævWB‚’ç7G&—‚¢6VÆbç7F÷&Rç6fR‡6VÆbç7FFR¢6VÆbå÷&Vg&W6…÷FööÅ÷7FGW2‚¢–b†6GG"‡6VÆbÂ&ff×Vu÷7FGW2"“ ¢FWFV7FVBÒf–æEöW†V7WF&ÆR‡6WGF–æw5²&ff×Vu÷F‚%ÒÂ&ff×Vr"¢6VÆbæff×Vu÷7FGW2æ6öæf–wW&R‡FW‡CÒ†b.[{.h›îX‹ûÉ§¶FWFV7FVGÒ"–bFWFV7FVBVÇ6R.[	®iÊ®h›îX‹df×V~ûÉ¾Šxnš){Én‹éXúşKùŞZÙûÈÎKØnKˆŞˆ;ŞyIşh‰h‰x˜~8""’ÂfsÔ44TåEôD$²–bFWFV7FVBVÇ6Rt$Ò¢ÖW76vV&÷‚ç6†÷v–æfò‚.[{.KùŞZÙ‚"Â.jŠYè¾8Xš®iŠY(ÎŠxnš)[z^X[~‹zş[èN[{.KùŞZÙ8$’¶W’K¸^KùŞyYYÊXh^ZÙKŠŞ8"" ¢FVbö•ö6Æ–VçB‡6VÆbÂW6Uöf÷&Ó¢&ööÂÒfÇ6R’Óâ÷Vä”6ö×F–&ÆT6Æ–VçC ¢6WGF–æw2Ò6VÆbç7FFU²'6WGF–æw2%Ğ¢–bW6Uöf÷&ÒæB†6GG"‡6VÆbÂ&&6U÷W&Å÷f""“ ¢&6U÷W&ÂÒ6VÆbæ&6U÷W&Å÷f"ævWB‚¢ÖöFVÂÒ6VÆbæÖöFVÅöæÖU÷f"ævWB‚¢&÷f–FW%ö–BÒ6VÆbæ7F—fUö•÷&÷f–FW ¢VÇ6S ¢&6U÷W&ÂÒ6WGF–æw2ævWB‚&&6U÷W&Â"Â""¢ÖöFVÂÒ6WGF–æw2ævWB‚&ÖöFVÂ"Â""¢&÷f–FW%ö–BÒ6WGF–æw2ævWB‚'&÷f–FW""’÷"–æfW%÷&÷f–FW"†&6U÷W&ÂÂÖöFVÂ¢6öæf–rÒ”6öæf–r†&6U÷W&ÂÂÖöFVÂÂ6VÆbæ•ö¶W’ævWB‚’Â&÷f–FW#×&÷f–FW%ö–B¢–bæ÷B6öæf–ræ&6U÷W&Ã ¢&—6R”6Æ–VçDW'&÷"‚.Šû~XXZ¾XijŠYè²&6RU$Î8""¢–bæ÷B6öæf–ræ•ö¶W“ ¢&—6R”6Æ–VçDW'&÷"‚.Šû~XXYÊ(	ÎjŠYè¾Kˆî[z^X[~(	ŞKŠŞZ¾Xi’’¶W8""¢&WGW&â÷Vä”6ö×F–&ÆT6Æ–VçB†6öæf–r ¢FVbFW7Eö•ö6öææV7F–öâ‡6VÆb’ÓâæöæS ¢–b6VÆbæ—5ö'W7“ ¢&WGW&à¢G'“ ¢6Æ–VçBÒ6VÆbåö•ö6Æ–VçB‡W6Uöf÷&ÓÕG'VR¢W†6WB”6Æ–VçDW'&÷"2W†3 ¢ÖW76vV&÷‚ç6†÷wv&æ–ær‚.izk9^kX¾ŠùR"Â7G"†W†2’¢&WGW&à¢6VÆbæ—5ö'W7’ÒG'VP¢6VÆbæ•÷FW7E÷7FGW2æ6öæf–wW&R‡FW‡CÖb.jÚ>YÊ‹ùîhêR·&÷f–FW%÷&W6WB‡6VÆbæ7F—fUö•÷&÷f–FW"’æÆ&VÇŞ(
b"ÂfsÔ44TåEôD$² ¢FVbv÷&¶W"‚’ÓâæöæS ¢G'“ ¢&WÇ’Ò6Æ–VçBæ6ö×ÆWFR‚.KÚiŠşhê^Xú>‹ùî˜	®h
~kX¾Šù^Xªh˜¾8""Â.Šû~Xú®Y¹îZHŞûÉ®‹ùîhê^h‰X©ò"ÂFV×W&GW&SÓã¢6VÆbæ'W2çWB‚‚&•÷FW7Eö6ö×ÆWFR"Â&WÇ’’¢W†6WBW†6WF–öâ2W†3¢2æ÷¢$ÄSÒF—7Æ’&÷f–FW"W'&÷"–âF†RT¢6VÆbæ'W2çWB‚‚&•÷FW7EöW'&÷""ÂW†2’ ¢F‡&VF–æråF‡&VB‡F&vWC×v÷&¶W"ÂFVÖöãÕG'VR’ç7F'B‚ ¢FVbö†æFÆU÷6WGF–æw5öWfVçB‡6VÆbÂWfVçC¢7G"Â–ÆöC¢ö&¦V7B’ÓâæöæS ¢–bWfVçBÓÒ&•÷FW7Eö6ö×ÆWFR# ¢6VÆbæ—5ö'W7’ÒfÇ6P¢6VÆbæ•÷FW7E÷7FGW2æ6öæf–wW&R‡FW‡CÖb.‹ùîhê^h‰X©şûÉ§·7G"‡–ÆöB•³£S×Ò"ÂfsÔ44TåEôD$²¢ÖW76vV&÷‚ç6†÷v–æfò‚.‹ùîhê^h‰X©ò"Âb'·&÷f–FW%÷&W6WB‡6VÆbæ7F—fUö•÷&÷f–FW"’æÆ&VÇÒhê^Xú>XúşKº^jÚ>[‹KÛşyJ8""¢VÆ–bWfVçBÓÒ&•÷FW7EöW'&÷"# ¢6VÆbæ—5ö'W7’ÒfÇ6P¢6VÆbæ•÷FW7E÷7FGW2æ6öæf–wW&R‡FW‡CÒ.‹ùîhê^ZK‹J^ûÈÎŠû~j8iúR¶W8jŠYè¾YŞY(Î{Ù{¹Â"ÂfsÔU%$õ"¢ÖW76vV&÷‚ç6†÷vW'&÷"‚.‹ùîhê^ZK‹JR"Â7G"‡–ÆöB’ ¢2ÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒWF–Æ—F–W2ÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒÒĞ¢FVbö6÷•÷FW‡B‡6VÆbÂfÇVS¢7G"’ÓâæöæS ¢6VÆbç&ö÷Bæ6Æ—&ö&Eö6ÆV"‚¢6VÆbç&ö÷Bæ6Æ—&ö&EöVæB‡fÇVR ¢FVb÷6fUö7W'&VçEöVF—F÷'2‡6VÆb’ÓâæöæS ¢–b6VÆbæ7W'&VçE÷vRÓÒ'f–FVò"æB6VÆbç÷7EöVF—F÷# ¢6VÆbå÷7–æ5÷f–FVõ÷7FFR‚¢VÆ–b6VÆbæ7W'&VçE÷vRÓÒ&æ÷fVÂ# ¢6VÆbå÷7–æ5öæ÷fVÅ÷'VÆW2‚¢6VÆbå÷6fUö6†FW%öVF—F÷'2‚¢6VÆbç7F÷&Rç6fR‡6VÆbç7FFR ¢FVböG&–åö'W2‡6VÆb’ÓâæöæS ¢G'“ ¢v†–ÆRG'VS ¢WfVçBÂ–ÆöBÒ6VÆbæ'W2ævWEöæ÷v—B‚¢–b6VÆbæ'W5ö†æFÆW# ¢6VÆbæ'W5ö†æFÆW"†WfVçBÂ–ÆöB¢W†6WBVWVRäV×G“ ¢70¢6VÆbç&ö÷BægFW"ƒ#Â6VÆbåöG&–åö'W2 ¢FVböåö6Æ÷6R‡6VÆb’ÓâæöæS ¢–b6VÆbæ—5ö'W7’æBæ÷BÖW76vV&÷‚æ6·–W6æò‚.K»¾XªK¸ŞYÊ‹ù¾ŠÂ"Â.X[>™zŞ[©NyJKÉ®KŠŞijŞ[Ù>X˜ŞK»¾XªûÈÎzîZé®˜X{®Y	~ûÉò"“ ¢&WGW&à¢6VÆbå÷6fUö7W'&VçEöVF—F÷'2‚¢6VÆbç7F÷&Rç6fR‡6VÆbç7FFR¢6VÆbç&ö÷BæFW7G&÷’‚  ¦FVbÖ–â‚’ÓâæöæS ¢&ö÷BÒF²‚¢7GVF–ô‡&ö÷B¢&ö÷BæÖ–æÆö÷‚  ¦FVb6¶vVE÷6VÆe÷FW7B‚’ÓâæöæS ¢""$W†W&6—6R&W6÷W&6W2æBæF—fRÖVF–'6–ær–ç6–FR6¶vVBFW6·F÷â"" ¢g&öÒ6÷&R–×÷'B¦–ç––æuöVæv–æP¢g&öÒ–ÖVF––æfò–×÷'BÖVF––æfğ ¢–b¦–ç––æuöVæv–æRæG&gB—2æöæS ¢&—6R'VçF–ÖTW'&÷"‡7G"†¦–ç––æuöVæv–æRäE$eEô”Õõ%EôU%$õ"’¢–bæ÷BÖVF––æfòæ6å÷'6R‚“ ¢&—6R'VçF–ÖTW'&÷"‚$ÖVF––æfòæF—fRÆ–'&'’Væf–Æ&ÆR"¢67&—BÒ¦–ç––æuöVæv–æRæG&gBå67&—Df–ÆRƒƒÂ“#Â3ÂG'VR¢–b67&—Bçv–GF‚Òƒ÷"67&—Bæ†V–v‡BÒ“# ¢&—6R'VçF–ÖTW'&÷"‚&G&gBFV×ÆFRVæf–Æ&ÆR"  ¦–bõöæÖUõòÓÒ%õöÖ–åõò# ¢–b"Ò×6VÆb×FW7B"–â7—2æ&wc ¢6¶vVE÷6VÆe÷FW7B‚¢VÇ6S ¢Ö–â‚