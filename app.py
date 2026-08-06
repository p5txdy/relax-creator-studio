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


APP_NAME = "解压创作工坊"
APP_VERSION = "0.2.1"
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
        Label(brand, text="◉", bg=SIDEBAR, fg=ACCENT, font=("Segoe UI Symbol", 26)).pack(anchor="w")
        Label(brand, text=APP_NAME, bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", pady=(8, 2))
        Label(brand, text="VIDEO × STORY STUDIO", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")

        nav_items = [
            ("dashboard", "⌂  创作首页"),
            ("video", "▶  视频混剪"),
            ("novel", "文  小说改文"),
            ("settings", "⚙  模型与工具"),
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
        Label(footer, text="本地项目自动保存", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        self.tool_status = Label(footer, text="正在检查工具…", bg=SIDEBAR, fg=WARM, font=("Microsoft YaHei UI", 9))
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
        self.tool_status.configure(text="● 剪映连接已就绪" if ready else "● 剪映路径待配置", fg=ACCENT if ready else WARM)

    def navigate(self, page: str) -> None:
        if self.is_busy and page != self.current_page:
            messagebox.showinfo("任务进行中", "当前任务完成后再切换工作台。")
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
        self._page_header("今天想创作什么？", "从素材到成片，从原文到新稿，所有进度都保存在本机。")

        body = Frame(self.main, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=34, pady=(2, 28))

        hero_outer = self._card(body, bg=SIDEBAR, padx=28, pady=26)
        hero_outer.pack(fill=X)
        hero = hero_outer.winfo_children()[0]
        left = Frame(hero, bg=SIDEBAR)
        left.pack(side=LEFT, fill=X, expand=True)
        Label(left, text="一个安静、完整的本地创作流程", bg=SIDEBAR, fg="white", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        Label(left, text="导入素材 · 设定规则 · AI 辅助 · 检查结果 · 本地导出", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(8, 0))
        self._button(hero, "开始视频混剪  →", lambda: self.navigate("video"), kind="accent").pack(side=RIGHT, padx=(18, 0))

        cards = Frame(body, bg=BG)
        cards.pack(fill=BOTH, expand=True, pady=(18, 0))
        cards.grid_columnconfigure(0, weight=1)
        cards.grid_columnconfigure(1, weight=1)
        cards.grid_rowconfigure(0, weight=1)

        video_card = self._card(cards, padx=26, pady=24)
        video_card.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        v = video_card.winfo_children()[0]
        Label(v, text="VIDEO MIX", bg=SURFACE, fg=ACCENT_DARK, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        Label(v, text="解压视频混剪", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w", pady=(8, 6))
        Label(v, text="批量排列素材、统一比例、添加转场与音乐，并生成平台发布文案。", bg=SURFACE, fg=MUTED, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        video = self.state["video"]
        self._metric_row(v, [("素材", len(video["clips"])), ("画幅", video["aspect"]), ("预计时长", f"{self._video_duration():.1f}s")])
        self._button(v, "进入工作台", lambda: self.navigate("video"), kind="ghost").pack(anchor="w", pady=(22, 0))

        novel_card = self._card(cards, padx=26, pady=24)
        novel_card.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
        n = novel_card.winfo_children()[0]
        Label(n, text="STORY REWRITE", bg=SURFACE, fg="#B66B2E", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        Label(n, text="小说改文", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w", pady=(8, 6))
        Label(n, text="自动拆章、管理人物设定、逐章或批量改写，并保留原文对照。", bg=SURFACE, fg=MUTED, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        novel = self.state["novel"]
        self._metric_row(n, [("章节", len(novel["chapters"])), ("已完成", len(novel["results"])), ("模式", novel["mode"])])
        self._button(n, "进入工作台", lambda: self.navigate("novel"), kind="ghost").pack(anchor="w", pady=(22, 0))

        tips_outer = self._card(body, bg="#FFF8F0", padx=22, pady=16)
        tips_outer.pack(fill=X, pady=(18, 0))
        tips = tips_outer.winfo_children()[0]
        Label(tips, text="使用提示", bg="#FFF8F0", fg="#8A5527", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        Label(tips, text="AI Key 只保留在本次运行的内存中，不会写入项目文件；请只改写你拥有版权或已获得授权的内容。", bg="#FFF8F0", fg="#8A6A4E", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(5, 0))

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
            button.configure(bg="#20372D" if key == page else SIDEBAR, fg="white" if key == page else SIDEBAR_MUTED)

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
        self.video_tree = ttk.Treeview(left, columns=columns, show="headings", style="Studio.Treeview", selectmode="browse")
        self.video_tree.heading("order", text="#")
        self.video_tree.heading("name", text="素材文件")
        self.video_tree.heading("start", text="起点")
        self.video_tree.heading("duration", text="取用时长")
        self.video_tree.column("order", width=50, anchor="center", stretch=False)
        self.video_tree.column("name", width=430, anchor="w")
        self.video_tree.column("start", width=90, anchor="center", stretch=False)
        self.video_tree.column("duration", width=100, anchor="center", stretch=False)
        self.video_tree.pack(fill=BOTH, expand=True, padx=1)
        self.video_tree.bind("<<TreeviewSelect>>", self.on_video_select)

        edit_bar = Frame(left, bg=SURFACE_ALT, padx=16, pady=12)
        edit_bar.pack(fill=X)
        self.clip_start_var = DoubleVar(value=0.0)
        self.clip_duration_var = DoubleVar(value=5.0)
        self._field_label(edit_bar, "起点(秒)").pack(side=LEFT)
        self._entry(edit_bar, self.clip_start_var, 7).pack(side=LEFT, padx=(7, 16), ipady=5)
        self._field_label(edit_bar, "时长(秒)").pack(side=LEFT)
        self._entry(edit_bar, self.clip_duration_var, 7).pack(side=LEFT, padx=(7, 16), ipady=5)
        self._button(edit_bar, "应用", self.update_selected_clip, kind="dark").pack(side=LEFT)
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
        self.post_editor = Text(post, height=5, wrap="word", bg="#F7F9F7", fg=INK, insertbackground=INK, relief="flat", padx=12, pady=10, font=("Microsoft YaHei UI", 10))
        self.post_editor.pack(fill=X, pady=(10, 0))
        self.post_editor.insert("1.0", self.state["video"].get("post_copy", ""))

        settings_outer = self._card(body, padx=20, pady=18)
        settings_outer.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
        settings = settings_outer.winfo_children()[0]
        Label(settings, text="成片参数", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        Label(settings, text="所有素材会自动铺满目标画幅", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 18))

        video = self.state["video"]
        self.aspect_var = StringVar(value=video["aspect"])
        self.fps_var = StringVar(value=str(video["fps"]))
        self.transition_var = StringVar(value=video["transition"])
        self.transition_duration_var = DoubleVar(value=video["transition_duration"])
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
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", style="Studio.TCombobox")
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
        total = sum(float(clip.get("duration", 5)) for clip in clips)
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
            self.video_tree.insert("", END, iid=str(index), values=(index + 1, Path(clip["path"]).name, f"{clip['start']:.1f}s", f"{clip['duration']:.1f}s"))
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
        for path in paths:
            duration = probe_duration(path, ffprobe) if ffprobe else None
            self.state["video"]["clips"].append({"path": path, "start": 0.0, "duration": round(min(duration or 5.0, 8.0), 2)})
        self._refresh_video_tree(len(self.state["video"]["clips"]) - 1)

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
        self.state["video"]["clips"][index].update(start=start, duration=duration)
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
            clips=[VideoClip(item["path"], float(item["start"]), float(item["duration"])) for item in video["clips"]],
            aspect=video["aspect"],
            fps=int(video["fps"]),
            transition=video["transition"],
            transition_duration=float(video["transition_duration"]),
            voice_path=video.get("voice_path", ""),
            subtitles_path=video.get("subtitles_path", ""),
            target_duration=float(video.get("voice_duration", 0.0)) if video.get("voice_path") else 0.0,
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
            [("导入小说", self.import_novel, "ghost"), ("改写当前章", self.rewrite_current, "primary")],
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
        self._novel_combo(control, "改写模式", self.mode_var, ["轻度润色", "深度改写", "扩写细节", "精简提速", "影视化改写"])
        self._novel_combo(control, "目标风格", self.style_var, ["节奏紧凑、画面感强", "自然细腻、情绪充足", "简洁爽快、对白突出", "悬念强、章节钩子明显", "轻松幽默"])
        self._novel_combo(control, "叙事视角", self.perspective_var, ["保持原视角", "第一人称", "第三人称限知", "第三人称全知"])
        self._novel_combo(control, "目标篇幅", self.length_var, ["与原文接近", "缩短约20%", "扩写约30%", "只保留主线"])
        self._field_label(control, "自定义规则").pack(anchor="w", pady=(10, 5))
        self.rules_editor = Text(control, height=4, wrap="word", bg="#F7F9F7", fg=INK, relief="flat", padx=8, pady=8, font=("Microsoft YaHei UI", 9))
        self.rules_editor.pack(fill=X)
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
        self.novel_list = Listbox(control, exportselection=False, bg="#F7F9F7", fg=INK, selectbackground="#DDF6E8", selectforeground=INK, relief="flat", highlightthickness=0, font=("Microsoft YaHei UI", 9), activestyle="none")
        self.novel_list.pack(fill=BOTH, expand=True, pady=(8, 10))
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
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", style="Studio.TCombobox").pack(fill=X)

    def _editor_panel(self, parent, title: str, subtitle: str, result: bool = False) -> Frame:
        panel = Frame(parent, bg=SURFACE)
        header = Frame(panel, bg=SURFACE_ALT, padx=16, pady=12)
        header.pack(fill=X)
        Label(header, text=title, bg=SURFACE_ALT, fg=INK, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        Label(header, text=subtitle, bg=SURFACE_ALT, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(2, 0))
        editor = Text(panel, wrap="word", undo=True, bg=SURFACE, fg=INK, insertbackground=INK, relief="flat", padx=18, pady=16, spacing1=2, spacing3=5, font=("Microsoft YaHei UI", 11))
        editor.pack(fill=BOTH, expand=True)
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

    def preview_prompt(self) -> None:
        self._save_chapter_editors()
        if self.current_chapter_index is None and not self._accept_pasted_source():
            messagebox.showinfo("没有章节", "请在“原文章节”中粘贴正文，或导入小说文件。")
            return
        assert self.current_chapter_index is not None
        system, user = self._chapter_prompt(self.current_chapter_index)
        dialog = Toplevel(self.root)
        dialog.title("本章提示词预览")
        dialog.geometry("820x660")
        dialog.configure(bg=BG)
        editor = Text(dialog, wrap="word", bg=SURFACE, fg=INK, padx=18, pady=18, font=("Microsoft YaHei UI", 10))
        editor.pack(fill=BOTH, expand=True, padx=18, pady=(18, 8))
        editor.insert("1.0", f"【系统提示】\n{system}\n\n【用户提示】\n{user}")
        row = Frame(dialog, bg=BG)
        row.pack(fill=X, padx=18, pady=(0, 18))
        self._button(row, "复制全部", lambda: self._copy_text(editor.get("1.0", "end-1c")), kind="primary").pack(side=RIGHT)

    def edit_story_bible(self) -> None:
        dialog = Toplevel(self.root)
        dialog.title("人物与世界观设定库")
        dialog.geometry("760x620")
        dialog.configure(bg=BG)
        Label(dialog, text="人物与世界观设定库", bg=BG, fg=INK, font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w", padx=22, pady=(20, 4))
        Label(dialog, text="记录人物称谓、关系、能力、禁改项和剧情时间线，模型每章都会参考。", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=22)
        editor = Text(dialog, wrap="word", bg=SURFACE, fg=INK, padx=18, pady=18, relief="flat", font=("Microsoft YaHei UI", 10))
        editor.pack(fill=BOTH, expand=True, padx=22, pady=16)
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

    # ----------------------------- Settings page -----------------------------
    def show_settings(self) -> None:
        self._clear_main()
        self.navigate_highlight("settings")
        self._page_header("模型与工具", "可直接选择 DeepSeek、千问、智谱 GLM、Kimi；API Key 不会写入磁盘。")
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
            self.api_key.set(self.api_keys.get(provider_id, "") or api_key_from_environment(provider_id))
        self.provider_ids_by_label = {item.label: item.id for item in PROVIDER_PRESETS}
        self.provider_labels_by_id = {item.id: item.label for item in PROVIDER_PRESETS}
        self.provider_var = StringVar(value=self.provider_labels_by_id[provider_id])
        self.base_url_var = StringVar(value=settings["base_url"])
        self.model_name_var = StringVar(value=settings["model"])
        self._field_label(model, "模型服务商").pack(anchor="w", pady=(2, 5))
        provider_box = ttk.Combobox(
            model,
            textvariable=self.provider_var,
            values=[item.label for item in PROVIDER_PRESETS],
            state="readonly",
            style="Studio.TCombobox",
        )
        provider_box.pack(fill=X)
        provider_box.bind("<<ComboboxSelected>>", self._apply_provider_selection)
        self.provider_help = Label(model, bg=SURFACE, fg=ACCENT_DARK, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 8))
        self.provider_help.pack(anchor="w", pady=(6, 0))
        self._settings_entry(model, "Base URL", self.base_url_var)
        self._settings_entry(model, "模型名称", self.model_name_var)
        self._field_label(model, "API Key（仅本次运行有效）").pack(anchor="w", pady=(13, 5))
        key_entry = self._entry(model, self.api_key)
        key_entry.configure(show="•")
        key_entry.pack(fill=X, ipady=7)
        self.api_key_hint = Label(model, bg=SURFACE, fg=MUTED, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 8))
        self.api_key_hint.pack(anchor="w", pady=(5, 0))
        self.ai_test_status = Label(model, text="尚未测试连接", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 8))
        self.ai_test_status.pack(anchor="w", pady=(12, 0))
        model_actions = Frame(model, bg=SURFACE)
        model_actions.pack(fill=X, pady=(14, 0))
        self._button(model_actions, "测试连接", self.test_ai_connection, kind="ghost").pack(side=LEFT)
        self._button(model_actions, "保存模型设置", self.save_settings, kind="primary").pack(side=RIGHT)
        self._update_provider_help()
        self.bus_handler = self._handle_settings_event

        tool_outer = self._card(body, padx=24, pady=22)
        tool_outer.grid(row=0, column=1, sticky="new", padx=(9, 0))
        tool = tool_outer.winfo_children()[0]
        Label(tool, text="视频引擎", bg=SURFACE, fg=INK, font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        Label(tool, text="剪映草稿可直接继续编辑；FFmpeg 仅用于额外导出 MP4。", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 18))
        self.ffmpeg_var = StringVar(value=settings.get("ffmpeg_path", ""))
        self.ffprobe_var = StringVar(value=settings.get("ffprobe_path", ""))
        detected_jianying = detect_jianying_executable(settings.get("jianying_exe", "")) or settings.get("jianying_exe", "")
        detected_drafts = detect_jianying_drafts_path(settings.get("jianying_drafts_path", "")) or settings.get("jianying_drafts_path", "")
        self.jianying_exe_var = StringVar(value=detected_jianying)
        self.jianying_drafts_var = StringVar(value=detected_drafts)
        jianying_label = "剪映专业版应用（.app）" if sys.platform == "darwin" else "剪映专业版程序"
        self._path_field(tool, jianying_label, self.jianying_exe_var, "JianyingPro")
        self._directory_field(tool, "剪映草稿目录", self.jianying_drafts_var)
        executable_suffix = "" if sys.platform == "darwin" else ".exe"
        self._path_field(tool, f"ffmpeg{executable_suffix}", self.ffmpeg_var, "ffmpeg")
        self._path_field(tool, f"ffprobe{executable_suffix}", self.ffprobe_var, "ffprobe")
        detected = find_executable(self.ffmpeg_var.get(), "ffmpeg")
        self.ffmpeg_status = Label(tool, text=(f"已找到：{detected}" if detected else "尚未找到 FFmpeg；视频编辑可保存，但不能生成成片。"), bg=SURFACE, fg=ACCENT_DARK if detected else WARM, wraplength=430, justify=LEFT, font=("Microsoft YaHei UI", 9))
        self.ffmpeg_status.pack(anchor="w", pady=(18, 0))
        self._button(tool, "保存工具设置", self.save_settings, kind="primary").pack(anchor="e", pady=(22, 0))

        note_outer = self._card(body, bg="#FFF8F0", padx=22, pady=16)
        note_outer.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        note = note_outer.winfo_children()[0]
        note_title = "Mac 兼容与 FFmpeg 说明" if sys.platform == "darwin" else "FFmpeg 配置说明"
        if sys.platform == "darwin":
            note_text = "Mac 应用会自动检查 PATH 中的 ffmpeg/ffprobe。草稿可以生成，但剪映 Mac 新版可能提示内容损坏；遇到时请将草稿交给 Windows 版剪映打开和导出。"
        else:
            note_text = "安装完成后选择 bin 文件夹中的 ffmpeg.exe 与 ffprobe.exe。应用也会自动检查 PATH、WinGet Links 和常见安装目录。"
        Label(note, text=note_title, bg="#FFF8F0", fg="#8A5527", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        Label(note, text=note_text, bg="#FFF8F0", fg="#8A6A4E", wraplength=900, justify=LEFT, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(5, 0))

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
        self.api_key.set(self.api_keys.get(selected, "") or api_key_from_environment(selected))
        self._update_provider_help()
        if hasattr(self, "ai_test_status"):
            self.ai_test_status.configure(text="切换服务商后请重新测试连接", fg=MUTED)

    def _update_provider_help(self) -> None:
        preset = provider_preset(self.active_api_provider)
        self.provider_help.configure(text=preset.description)
        names = " / ".join(preset.environment_keys)
        self.api_key_hint.configure(text=f"也可在启动前设置环境变量：{names}")

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
        if hasattr(self, "base_url_var"):
            settings["provider"] = self.active_api_provider
            settings["base_url"] = self.base_url_var.get().strip().rstrip("/")
            settings["model"] = self.model_name_var.get().strip()
            self.api_keys[self.active_api_provider] = self.api_key.get().strip()
            settings["ffmpeg_path"] = self.ffmpeg_var.get().strip()
            settings["ffprobe_path"] = self.ffprobe_var.get().strip()
            settings["jianying_exe"] = self.jianying_exe_var.get().strip()
            settings["jianying_drafts_path"] = self.jianying_drafts_var.get().strip()
        self.store.save(self.state)
        self._refresh_tool_status()
        if hasattr(self, "ffmpeg_status"):
            detected = find_executable(settings["ffmpeg_path"], "ffmpeg")
            self.ffmpeg_status.configure(text=(f"已找到：{detected}" if detected else "尚未找到 FFmpeg；视频编辑可保存，但不能生成成片。"), fg=ACCENT_DARK if detected else WARM)
        messagebox.showinfo("已保存", "模型、剪映和视频工具路径已保存。API Key 仅保留在内存中。")

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
            messagebox.showinfo("连接成功", f"{provider_preset(self.active_api_provider).label} 接口可以正常使用。")
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
        self.store.save(self.state)
        self.root.destroy()


def main() -> None:
    root = Tk()
    StudioApp(root)
    root.mainloop()


def packaged_self_test() -> None:
    """Exercise resources and native media parsing inside a packaged desktop app."""
    from core import jianying_engine
    from pymediainfo import MediaInfo

    if jianying_engine.draft is None:
        raise RuntimeError(str(jianying_engine.DRAFT_IMPORT_ERROR))
    if not callable(getattr(MediaInfo, "parse", None)):
        raise RuntimeError("MediaInfo parser unavailable")
    script = jianying_engine.draft.ScriptFile(1080, 1920, 30, True)
    if script.width != 1080 or script.height != 1920:
        raise RuntimeError("draft template unavailable")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        packaged_self_test()
    else:
        main()
