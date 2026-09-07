"""已连接设备：设备卡片列表 + 详情弹窗（改名 / 换图标 / 用量统计）。"""
from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Dict, List, Optional

from ...core.deviceman import DeviceInfo
from ...core.storage import human_bytes, human_rate
from ..icons import ICON_KEYS, ICON_LABELS, draw_icon, load_custom_icon
from ..widgets import BarChart, Card, ScrollFrame, Toggle
from .base import Page


def _fmt_time(ts: float) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


class DeviceCard(tk.Frame):
    """一行设备卡片。"""

    def __init__(self, master, page: "DevicesPage", info: DeviceInfo) -> None:
        self.page = page
        self.p = page.p
        self.info = info
        super().__init__(master, bg=self.p.surface, highlightthickness=1,
                         highlightbackground=self.p.border, cursor="hand2")
        self.columnconfigure(1, weight=1)
        self._photos: List = []

        self.icon_canvas = tk.Canvas(self, width=44, height=44, bg=self.p.surface,
                                     highlightthickness=0, bd=0)
        self.icon_canvas.grid(row=0, column=0, rowspan=2, padx=(14, 12), pady=12)
        self._draw_icon()

        top = tk.Frame(self, bg=self.p.surface)
        top.grid(row=0, column=1, sticky="ew", pady=(12, 0))
        top.columnconfigure(0, weight=1)
        self.name_label = tk.Label(top, text=info.name, bg=self.p.surface, fg=self.p.text,
                                   font=("Microsoft YaHei UI", 11, "bold"), anchor="w")
        self.name_label.grid(row=0, column=0, sticky="w")

        self.dot = tk.Canvas(top, width=10, height=10, bg=self.p.surface,
                             highlightthickness=0, bd=0)
        self.dot.grid(row=0, column=1, padx=(8, 0))
        self.state_label = tk.Label(top, text="", bg=self.p.surface, fg=self.p.muted,
                                    font=("Microsoft YaHei UI", 9))
        self.state_label.grid(row=0, column=2, padx=(4, 14))

        bottom = tk.Frame(self, bg=self.p.surface)
        bottom.grid(row=1, column=1, sticky="ew", pady=(2, 12))
        self.meta_label = tk.Label(bottom, text="", bg=self.p.surface, fg=self.p.muted,
                                   font=("Microsoft YaHei UI", 9), anchor="w")
        self.meta_label.pack(side="left")

        self.rate_label = tk.Label(self, text="", bg=self.p.surface, fg=self.p.teal,
                                   font=("Cascadia Mono", 10))
        self.rate_label.grid(row=0, column=2, rowspan=2, padx=(10, 6), sticky="e")

        self.usage_label = tk.Label(self, text="", bg=self.p.surface, fg=self.p.muted,
                                    font=("Microsoft YaHei UI", 9), anchor="e")
        self.usage_label.grid(row=0, column=3, rowspan=2, padx=(0, 16), sticky="e")
        self.columnconfigure(3, minsize=130)

        for w in (self, self.icon_canvas, self.name_label, self.meta_label,
                  self.rate_label, self.usage_label, top, bottom):
            w.bind("<Button-1>", lambda _e: self.page.open_detail(self.info.mac))
            w.bind("<Enter>", lambda _e: self._hover(True))
            w.bind("<Leave>", lambda _e: self._hover(False))

        self.update_data(info)

    # ---------- 渲染 ----------
    def _hover(self, entering: bool) -> None:
        bg = self.p.hover if entering else self.p.surface
        self.configure(bg=bg, highlightbackground=self.p.accent if entering else self.p.border)
        for w in (self.icon_canvas, self.name_label, self.meta_label, self.rate_label,
                  self.usage_label, self.state_label, self.dot):
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass

    def _draw_icon(self) -> None:
        self.icon_canvas.delete("all")
        info = self.info
        key = info.effective_icon
        color = self.p.accent
        if info.limited:
            color = self.p.danger
        elif not info.online:
            color = self.p.dim
        if key == "custom" and info.custom_icon:
            photo = load_custom_icon(info.custom_icon, 34)
            if photo is not None:
                self._photos.append(photo)
                self.icon_canvas.create_image(22, 22, image=photo)
                return
        draw_icon(self.icon_canvas, key, 5, 5, 34, color=color, bg=self.p.surface)

    def update_data(self, info: DeviceInfo) -> None:
        self.info = info
        self.name_label.configure(text=info.name)
        self.dot.delete("all")
        color = self.p.success if info.online else self.p.dim
        self.dot.create_oval(2, 2, 9, 9, fill=color, outline="")
        self.state_label.configure(text="在线" if info.online else "离线")
        meta = f"{info.type_label}   {info.mac}"
        if info.ip:
            meta += f"   {info.ip}"
        if info.vendor:
            meta += f"   {info.vendor}"
        if info.limited:
            meta += "   ⚠ 已标记受限"
        self.meta_label.configure(text=meta)
        self.rate_label.configure(
            text=f"↓{human_rate(info.rate_down)} ↑{human_rate(info.rate_up)}")
        self.usage_label.configure(
            text=f"本次 {human_bytes(info.session_rx + info.session_tx)}\n"
                 f"累计 {human_bytes(info.total_rx + info.total_tx)}")
        self._draw_icon()


class DevicesPage(Page):
    key = "devices"
    title = "已连接设备"
    nav_label = "已连接设备"

    def build(self) -> None:
        self.cards: Dict[str, DeviceCard] = {}
        self._hash = ""
        self._filter = tk.StringVar(value="")
        self._online_only = tk.BooleanVar(value=False)

        head = ttk.Frame(self, style="TFrame")
        head.pack(fill="x", padx=20, pady=(18, 8))
        ttk.Label(head, text="已连接设备", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="点击设备可改名 / 换图标 / 查看用量统计",
                  foreground=self.p.muted, background=self.p.bg,
                  font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(12, 4), pady=(6, 0))

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.pack(fill="x", padx=20, pady=(0, 8))
        self.search = ttk.Entry(toolbar, textvariable=self._filter, width=26)
        self.search.pack(side="left")
        self.search.insert(0, "")
        self._filter.trace_add("write", lambda *_: self._rebuild())
        ttk.Button(toolbar, text="搜索", command=self._rebuild).pack(side="left", padx=6)
        ttk.Checkbutton(toolbar, text="仅显示在线", variable=self._online_only,
                        command=self._rebuild).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="刷新", command=lambda: self.app.request_refresh(True)).pack(
            side="left", padx=(14, 0))
        ttk.Button(toolbar, text="清空搜索", command=self._clear_search).pack(
            side="left", padx=6)
        self.count_label = ttk.Label(toolbar, text="", foreground=self.p.muted,
                                     background=self.p.bg, font=("Microsoft YaHei UI", 9))
        self.count_label.pack(side="right")

        self.scroll = ScrollFrame(self, self.p)
        self.scroll.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self.scroll.body.columnconfigure(0, weight=1)

    def _clear_search(self) -> None:
        self._filter.set("")
        self._rebuild()

    # ------------------------------------------------------------------ #
    def _visible(self) -> List[DeviceInfo]:
        kw = (self._filter.get() or "").strip().lower()
        items = self.app.devman.all()
        if self._online_only.get():
            items = [d for d in items if d.online]
        if kw:
            items = [d for d in items if kw in (d.name or "").lower()
                     or kw in d.mac.lower() or kw in (d.ip or "")
                     or kw in (d.vendor or "").lower()
                     or kw in d.type_label]
        return items

    def _signature(self, items: List[DeviceInfo]) -> str:
        parts = []
        for d in items:
            parts.append(
                f"{d.mac}|{d.ip}|{d.online}|{d.name}|{int(d.rate_down)}|{int(d.rate_up)}"
                f"|{d.session_rx}|{d.session_tx}|{d.effective_icon}|{d.custom_icon}|{d.limited}")
        return "|".join(parts)

    def _rebuild(self) -> None:
        items = self._visible()
        sig = self._signature(items)
        if sig == self._hash:
            return
        self._hash = sig
        self.scroll.clear()
        self.cards.clear()
        if not items:
            ttk.Label(self.scroll.body,
                      text="暂无设备。\n\n开启热点后有设备连入即会显示在这里；\n"
                           "也可以在「设置」页打开演示数据查看界面效果。",
                      style="Muted.TLabel", justify="center").pack(pady=60)
            self.count_label.configure(text="0 台设备")
            self.scroll.bind_children_wheel()
            return
        for i, info in enumerate(items):
            card = DeviceCard(self.scroll.body, self, info)
            card.grid(row=i, column=0, sticky="ew", pady=(0, 8))
            self.cards[info.mac] = card
        online = sum(1 for d in items if d.online)
        self.count_label.configure(text=f"共 {len(items)} 台（在线 {online}）")
        self.scroll.bind_children_wheel()

    def refresh(self) -> None:
        items = self._visible()
        sig = self._signature(items)
        if sig != self._hash:
            self._rebuild()
        else:
            for mac, card in self.cards.items():
                info = self.app.devman.get(mac)
                if info:
                    card.update_data(info)

    # ------------------------------------------------------------------ #
    def open_detail(self, mac: str) -> None:
        info = self.app.devman.get(mac)
        if info is None:
            return
        DeviceDialog(self.winfo_toplevel(), self, info)


# --------------------------------------------------------------------------- #
#                              设备详情弹窗                                   #
# --------------------------------------------------------------------------- #
class DeviceDialog(tk.Toplevel):
    def __init__(self, master, page: "DevicesPage", info: DeviceInfo) -> None:
        super().__init__(master)
        self.page = page
        self.app = page.app
        self.p = page.p
        self.info = info
        self.mac = info.mac
        self._photos: List = []
        self._icon_var = tk.StringVar(value=info.effective_icon)
        self._type_var = tk.StringVar(value=info.dev_type)
        self._after_ids: set = set()

        self.title(f"设备详情 — {info.name}")
        self.configure(bg=self.p.bg)
        self.transient(master)
        self.geometry("760x620")
        self.minsize(680, 560)
        self.resizable(True, True)
        self._build()
        self.grab_set()
        self.focus_force()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        wrap = ScrollFrame(self, self.p)
        wrap.pack(fill="both", expand=True)
        body = wrap.body
        body.columnconfigure(0, weight=1)

        # --- 头部 ---
        head = ttk.Frame(body, style="Surface.TFrame")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        self.head_canvas = tk.Canvas(head, width=52, height=52, bg=self.p.surface,
                                     highlightthickness=0, bd=0)
        self.head_canvas.pack(side="left")
        txt = ttk.Frame(head, style="Surface.TFrame")
        txt.pack(side="left", padx=(14, 0))
        ttk.Label(txt, text=self.info.name, font=("Microsoft YaHei UI", 14, "bold"),
                  style="Surface.TLabel").pack(anchor="w")
        ttk.Label(txt, text=f"{self.info.type_label} · {self.info.mac}",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        self._draw_head_icon()

        # --- 基本信息 ---
        info_card = Card(body, self.p, title="基本信息", padx=14, pady=12)
        info_card.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        rows = [
            ("MAC 地址", self.info.mac),
            ("IP 地址", self.info.ip or "—"),
            ("主机名", self.info.hostname or "—"),
            ("厂商 / 识别", self.info.vendor or "—"),
            ("状态", "在线" if self.info.online else "离线"),
            ("首次发现", _fmt_time(self.info.first_seen)),
            ("最后在线", _fmt_time(self.info.last_seen)),
            ("来源", {"winrt": "移动热点 API", "netsh": "承载网络", "arp": "ARP 表",
                     "demo": "演示数据"}.get(self.info.source, self.info.source or "—")),
        ]
        for i, (k, v) in enumerate(rows):
            ttk.Label(info_card.body, text=k, style="Muted.TLabel").grid(
                row=i, column=0, sticky="w", pady=2)
            val = ttk.Label(info_card.body, text=str(v), style="Surface.TLabel",
                            font=("Cascadia Mono", 9))
            val.grid(row=i, column=1, sticky="w", padx=(14, 0), pady=2)
            if k == "主机名":
                self._hostname_label = val

        # --- 命名与图标 ---
        edit = Card(body, self.p, title="名称与图标", padx=14, pady=12)
        edit.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        e = edit.body
        e.columnconfigure(1, weight=1)
        ttk.Label(e, text="自定义名称", style="H3.TLabel").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value=self.info.custom_name)
        ttk.Entry(e, textvariable=self.name_var).grid(row=0, column=1, sticky="ew",
                                                      padx=(12, 0))
        ttk.Label(e, text="留空则自动使用主机名或「厂商+MAC尾号」",
                  style="Dim.TLabel").grid(row=1, column=1, sticky="w", padx=(12, 0))

        ttk.Label(e, text="设备类型", style="H3.TLabel").grid(row=2, column=0, sticky="w",
                                                             pady=(10, 0))
        type_row = ttk.Frame(e, style="Surface.TFrame")
        type_row.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(10, 0))
        from ...core.oui import DEVICE_TYPES

        self.type_combo = ttk.Combobox(
            type_row, textvariable=self._type_var, state="readonly", width=16,
            values=[f"{k}  —  {v}" for k, v in DEVICE_TYPES.items()])
        self.type_combo.pack(side="left")
        for k, v in DEVICE_TYPES.items():
            if k == self.info.dev_type:
                self.type_combo.set(f"{k}  —  {v}")
                break
        self.type_combo.bind("<<ComboboxSelected>>", self._on_type_change)

        ttk.Label(e, text="预设图标", style="H3.TLabel").grid(row=3, column=0, sticky="nw",
                                                             pady=(12, 0))
        grid = ttk.Frame(e, style="Surface.TFrame")
        grid.grid(row=3, column=1, sticky="w", padx=(12, 0), pady=(12, 0))
        self.icon_buttons: Dict[str, tk.Canvas] = {}
        keys = ["auto"] + ICON_KEYS + ["custom"]
        labels = {"auto": "自动识别", "custom": "自定义图片"}
        for i, key in enumerate(keys):
            cell = tk.Frame(grid, bg=self.p.surface)
            cell.grid(row=i // 8, column=i % 8, padx=4, pady=4)
            cv = tk.Canvas(cell, width=40, height=40, bg=self.p.surface2,
                           highlightthickness=1, highlightbackground=self.p.border,
                           cursor="hand2")
            cv.pack()
            name = labels.get(key, ICON_LABELS.get(key, key))
            tk.Label(cell, text=name, bg=self.p.surface, fg=self.p.muted,
                     font=("Microsoft YaHei UI", 8)).pack()
            if key == "auto":
                cv.create_text(20, 20, text="AUTO", fill=self.p.accent,
                               font=("Microsoft YaHei UI", 8, "bold"))
            elif key == "custom":
                cv.create_text(20, 20, text="图片", fill=self.p.teal,
                               font=("Microsoft YaHei UI", 9))
            else:
                draw_icon(cv, key, 6, 6, 28, color=self.p.accent, bg=self.p.surface2)
            cv.bind("<Button-1>", lambda _e, k=key: self._pick_icon(k))
            self.icon_buttons[key] = cv
        self.custom_path_label = ttk.Label(e, text=self.info.custom_icon or "未选择",
                                           style="Dim.TLabel", wraplength=420)
        self.custom_path_label.grid(row=4, column=1, sticky="w", padx=(12, 0))

        # --- 备注 ---
        note_card = Card(body, self.p, title="备注", padx=14, pady=10)
        note_card.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 10))
        self.note_text = tk.Text(note_card.body, height=3, wrap="word",
                                 bg=self.p.input_bg, fg=self.p.text, bd=0,
                                 highlightthickness=1, highlightbackground=self.p.border,
                                 font=("Microsoft YaHei UI", 9),
                                 insertbackground=self.p.text)
        self.note_text.pack(fill="x")
        self.note_text.insert("1.0", self.info.note or "")

        # --- 用量统计 ---
        use = Card(body, self.p, title="流量用量", padx=14, pady=12)
        use.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 10))
        u = use.body
        u.columnconfigure(0, weight=1)
        u.columnconfigure(1, weight=1)
        self.use_labels = {}
        for i, (k, val) in enumerate(
                (("本次会话下行", human_bytes(self.info.session_rx)),
                 ("本次会话上行", human_bytes(self.info.session_tx)),
                 ("累计下行", human_bytes(self.info.total_rx)),
                 ("累计上行", human_bytes(self.info.total_tx)))):
            f = ttk.Frame(u, style="Surface.TFrame")
            f.grid(row=0, column=i, sticky="w", padx=(0, 18))
            ttk.Label(f, text=k, style="Muted.TLabel").pack(anchor="w")
            lab = ttk.Label(f, text=val, style="Surface.TLabel",
                            font=("Cascadia Mono", 11, "bold"))
            lab.pack(anchor="w")
            self.use_labels[k] = lab
        self.rate_label = ttk.Label(u, text="", style="Accent.TLabel")
        self.rate_label.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.chart = BarChart(u, self.p, height=130)
        self.chart.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self.chart.set_data(self.app.db.daily(self.mac, 14))

        # --- 选项 ---
        opt = Card(body, self.p, title="选项", padx=14, pady=10)
        opt.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 10))
        row = ttk.Frame(opt.body, style="Surface.TFrame")
        row.pack(fill="x")
        self.limit_tg = Toggle(row, self.p, value=self.info.limited)
        self.limit_tg.pack(side="left")
        ttk.Label(row, text="标记为受限设备（超出最大连接数时提醒）",
                  style="Muted.TLabel").pack(side="left", padx=(10, 0))

        # --- 按钮 ---
        bar = ttk.Frame(body, style="TFrame")
        bar.grid(row=6, column=0, sticky="ew", padx=16, pady=(0, 20))
        ttk.Button(bar, text="保存", style="Primary.TButton", command=self.save).pack(
            side="right")
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right", padx=8)
        self.resolve_btn = ttk.Button(bar, text="重新识别主机名", command=self.resolve_name)
        self.resolve_btn.pack(side="left")
        ttk.Button(bar, text="清除该设备用量", style="Danger.TButton",
                   command=self.reset_usage).pack(side="left", padx=8)
        ttk.Button(bar, text="删除设备档案", style="Danger.TButton",
                   command=self.forget).pack(side="left")

        wrap.bind_children_wheel()
        self._highlight_icon()
        self._after_ids.add(self.after(800, self._tick))

    # ------------------------------------------------------------------ #
    def _draw_head_icon(self) -> None:
        self.head_canvas.delete("all")
        key = self._icon_var.get()
        if key == "custom" and self.info.custom_icon:
            photo = load_custom_icon(self.info.custom_icon, 40)
            if photo is not None:
                self._photos.append(photo)
                self.head_canvas.create_image(26, 26, image=photo)
                return
        color = self.p.accent if self.info.online else self.p.dim
        draw_icon(self.head_canvas, key if key != "custom" else "unknown",
                  6, 6, 40, color=color, bg=self.p.surface)

    def _highlight_icon(self) -> None:
        cur = self._icon_var.get()
        for key, cv in self.icon_buttons.items():
            on = (key == cur)
            cv.configure(highlightbackground=self.p.accent if on else self.p.border,
                         highlightthickness=2 if on else 1,
                         bg=self.p.elevated if on else self.p.surface2)

    def _pick_icon(self, key: str) -> None:
        if key == "custom":
            from tkinter import filedialog

            path = filedialog.askopenfilename(
                title="选择设备图标图片",
                filetypes=[("图片", "*.png *.gif *.jpg *.jpeg *.bmp *.ico"), ("所有文件", "*.*")])
            if not path:
                return
            self.info.custom_icon = path
            self.custom_path_label.configure(text=path)
        self._icon_var.set(key)
        self._highlight_icon()
        self._draw_head_icon()

    def _on_type_change(self, _e=None) -> None:
        val = str(self._type_var.get()).split("—")[0].strip()
        self._icon_var.set(val)
        self._highlight_icon()
        self._draw_head_icon()

    def resolve_name(self) -> None:
        # 主机名识别要走网络(DNS 反查 + NBNS 查询)，可能耗时数秒，
        # 必须放到后台线程，否则会卡住整个界面。
        if getattr(self, "resolve_btn", None) is not None:
            self.resolve_btn.configure(state="disabled", text="识别中…")
        mac = self.mac

        def work():
            try:
                name = self.app.devman.resolve_hostname(mac, use_nbt=True)
            except Exception:
                name = ""
            self.app.after_main(0, self._after_resolve, name)

        threading.Thread(target=work, daemon=True).start()

    def _after_resolve(self, name: str) -> None:
        if getattr(self, "resolve_btn", None) is not None:
            self.resolve_btn.configure(state="normal", text="重新识别主机名")
        if name:
            self.info.hostname = name
            if getattr(self, "_hostname_label", None) is not None:
                self._hostname_label.configure(text=name)
            self.app.notify(f"识别到主机名：{name}", "success")
        else:
            self.app.notify("未能识别主机名（设备可能未开启网络发现）", "warning")

    def reset_usage(self) -> None:
        from tkinter import messagebox

        if not messagebox.askyesno("确认", "确定清除该设备的历史用量记录？此操作不可撤销。"):
            return
        self.app.db.reset_device(self.mac)
        self.chart.set_data(self.app.db.daily(self.mac, 14))
        self.app.notify("已清除该设备用量记录", "success")

    def forget(self) -> None:
        from tkinter import messagebox

        if not messagebox.askyesno("确认", "删除该设备的档案（名称/图标/备注）？\n"
                                           "历史用量记录会一并清除。"):
            return
        self.app.devman.forget(self.mac)
        self.app.db.reset_device(self.mac)
        self.destroy()
        self.page._hash = ""
        self.app.notify("已删除设备档案", "success")

    def save(self) -> None:
        name = self.name_var.get().strip()
        self.app.devman.rename(self.mac, name)
        icon = self._icon_var.get()
        self.app.devman.set_icon(self.mac, icon,
                                 self.info.custom_icon if icon == "custom" else "")
        dtype = str(self._type_var.get()).split("—")[0].strip()
        if dtype:
            self.app.devman.set_type(self.mac, dtype, lock=True)
        self.app.devman.set_note(self.mac, self.note_text.get("1.0", "end").strip())
        self.app.devman.set_limited(self.mac, self.limit_tg.value)
        self.page._hash = ""
        self.app.notify("已保存", "success")
        self.destroy()

    def _tick(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        host = self.master
        info = self.app.devman.get(self.mac)
        if info:
            self.info = info
            self.rate_label.configure(
                text=f"实时：↓ {human_rate(info.rate_down)}    ↑ {human_rate(info.rate_up)}")
            self.use_labels["本次会话下行"].configure(text=human_bytes(info.session_rx))
            self.use_labels["本次会话上行"].configure(text=human_bytes(info.session_tx))
            self.use_labels["累计下行"].configure(text=human_bytes(info.total_rx))
            self.use_labels["累计上行"].configure(text=human_bytes(info.total_tx))
        try:
            self._after_ids.add(self.after(1000, self._tick))
        except Exception:
            pass

    def destroy(self) -> None:
        for jid in list(self._after_ids):
            try:
                self.after_cancel(jid)
            except Exception:
                pass
        self._after_ids.clear()
        try:
            super().destroy()
        except Exception:
            pass
