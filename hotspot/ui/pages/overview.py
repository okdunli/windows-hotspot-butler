"""概览：热点状态总览、实时速率、本机网卡能力、快捷操作。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import List

from ...core.storage import human_bytes, human_rate
from ..widgets import Card, ScrollFrame, Sparkline
from .base import Page


class OverviewPage(Page):
    key = "overview"
    title = "概览"
    nav_label = "概览"

    def build(self) -> None:
        wrap = ScrollFrame(self, self.p)
        wrap.pack(fill="both", expand=True)
        wrap.body.columnconfigure(0, weight=1)
        self.scroll = wrap

        # ---------- 状态大卡 ----------
        self.status_card = Card(wrap.body, self.p, padx=18, pady=16)
        self.status_card.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 10))
        self.status_card.body.columnconfigure(0, weight=1)

        left = ttk.Frame(self.status_card.body, style="Surface.TFrame")
        left.grid(row=0, column=0, sticky="w")
        self.state_dot = tk.Canvas(left, width=18, height=18, bg=self.p.surface,
                                   highlightthickness=0, bd=0)
        self.state_dot.pack(side="left", pady=(4, 0))
        self.state_text = ttk.Label(left, text="检测中…", font=("Microsoft YaHei UI", 20, "bold"),
                                    style="Surface.TLabel")
        self.state_text.pack(side="left", padx=(10, 0))
        self.state_sub = ttk.Label(self.status_card.body, text="", style="Muted.TLabel")
        self.state_sub.grid(row=1, column=0, sticky="w", pady=(6, 0))

        btns = ttk.Frame(self.status_card.body, style="Surface.TFrame")
        btns.grid(row=0, column=1, rowspan=2, sticky="e")
        self.btn_toggle = ttk.Button(btns, text="开启热点", style="Primary.TButton",
                                     command=self.toggle_hotspot)
        self.btn_toggle.pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="刷新", command=lambda: self.app.request_refresh(True)).pack(
            side="right")
        ttk.Button(btns, text="复制密码", command=self.copy_pass).pack(side="right", padx=(0, 8))
        self.status_card.body.columnconfigure(1, weight=1)

        # ---------- 统计行 ----------
        stat_row = ttk.Frame(wrap.body, style="TFrame")
        stat_row.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        for i in range(4):
            stat_row.columnconfigure(i, weight=1)
        self.stats: List[tk.Label] = []
        for i, (label, _unit) in enumerate(
                (("在线设备", ""), ("已保存设备", ""), ("本次会话用量", ""), ("累计用量（全部设备）", ""))):
            card = ttk.Frame(stat_row, style="Card.TFrame", padding=(14, 12))
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            ttk.Label(card, text=label, style="Muted.TLabel").pack(anchor="w")
            val = ttk.Label(card, text="—", style="Surface.TLabel",
                            font=("Microsoft YaHei UI", 15, "bold"))
            val.pack(anchor="w", pady=(4, 0))
            self.stats.append(val)

        # ---------- 实时速率 ----------
        rate_card = Card(wrap.body, self.p, title="实时速率", padx=16, pady=12)
        rate_card.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        rate_card.body.columnconfigure(0, weight=1)
        self.rate_line = ttk.Label(rate_card.body, text="↓ 0 B/s   ↑ 0 B/s", style="Accent.TLabel")
        self.rate_line.grid(row=0, column=0, sticky="w")
        self.spark = Sparkline(rate_card.body, self.p, height=78)
        self.spark.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.rate_note = ttk.Label(rate_card.body, text="", style="Dim.TLabel")
        self.rate_note.grid(row=2, column=0, sticky="w", pady=(6, 0))

        # ---------- 本机能力 ----------
        self.cap_card = Card(wrap.body, self.p, title="本机无线网卡能力",
                             subtitle="（选项按实际探测结果启用/禁用）", padx=16, pady=12)
        self.cap_card.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.cap_text = tk.Text(self.cap_card.body, height=6, wrap="word",
                                bg=self.p.surface, fg=self.p.text, bd=0,
                                highlightthickness=0, font=("Microsoft YaHei UI", 9),
                                insertbackground=self.p.text)
        self.cap_text.pack(fill="x")
        self.cap_text.configure(state="disabled")

        # ---------- 快捷操作 ----------
        quick = Card(wrap.body, self.p, title="快捷操作", padx=16, pady=12)
        quick.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 20))
        row = ttk.Frame(quick.body, style="Surface.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="打开系统「移动热点」设置",
                   command=self.open_system_settings).pack(side="left")
        ttk.Button(row, text="打开「网络连接」",
                   command=self.open_ncpa).pack(side="left", padx=8)
        ttk.Button(row, text="打开欢迎页（本机预览）",
                   command=self.open_portal_local).pack(side="left")
        ttk.Button(row, text="打开数据目录",
                   command=self.open_data_dir).pack(side="left", padx=8)

        wrap.bind_children_wheel()

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        app = self.app
        st = app.status
        color = {"on": self.p.success, "off": self.p.dim,
                 "transition": self.p.warning}.get(st.state, self.p.warning)
        self.state_dot.delete("all")
        self.state_dot.create_oval(4, 4, 16, 16, fill=color, outline="")
        self.state_text.configure(text=st.state_text)
        sub = []
        if st.ssid:
            sub.append(f"网络名称：{st.ssid}")
        if st.band:
            sub.append(f"频段：{ {'2.4': '2.4 GHz', '5': '5 GHz', 'auto': '自动'}.get(st.band, st.band) }")
        sub.append(f"客户端：{max(st.client_count, len(app.clients))}"
                   + (f" / {st.max_clients}" if st.max_clients else ""))
        if st.message:
            sub.append(f"提示：{st.message}")
        self.state_sub.configure(text="    ".join(sub))
        self.btn_toggle.configure(text="停止热点" if st.active else "开启热点",
                                  style="Danger.TButton" if st.active else "Primary.TButton")

        online, total, srx, stx = app.devman.stats_summary()
        grx, gtx = app.db.grand_total()
        self.stats[0].configure(text=str(online), foreground=self.p.text)
        self.stats[1].configure(text=str(total), foreground=self.p.text)
        self.stats[2].configure(text=f"↓{human_bytes(srx)}  ↑{human_bytes(stx)}",
                                foreground=self.p.text)
        self.stats[3].configure(text=f"↓{human_bytes(grx)}  ↑{human_bytes(gtx)}",
                                foreground=self.p.text)

        down, up = app.traffic.total_rate()
        self.rate_line.configure(text=f"↓ {human_rate(down)}    ↑ {human_rate(up)}")
        self.spark.set_data([(d, u) for _t, d, u in app.traffic.history()])
        self.rate_note.configure(text=f"统计方式：{app.traffic.status_text}")

        self._render_caps()

    def on_caps(self, caps) -> None:
        self._render_caps(caps)

    def _render_caps(self, caps=None) -> None:
        caps = caps if caps is not None else self.app.capabilities
        if not caps:
            return
        lines = [
            f"无线网卡：{caps.get('driver') or '未知'}   无线电类型：{caps.get('radios')}",
            f"当前后端：{caps.get('backend_label')}"
            + ("（承载网络不可用，已自动切换）" if caps.get("backend") == "netsh"
               and not caps.get("hosted_supported") else ""),
            f"频段：2.4 GHz {'可用' if caps.get('band_24') else '不可用'}"
            f"    5 GHz {caps.get('band_5_text')}"
            f"    可写频段：{'是' if caps.get('band') else '否（当前后端不支持）'}",
            f"加密：驱动 WPA2 {'√' if caps.get('wpa2') else '×'} / WPA3 {'√' if caps.get('wpa3') else '×'}"
            f"    系统 API 可设置：{'仅 netsh 后端' if caps.get('security_choice') else '固定 WPA2'}",
            f"最大连接数：{'系统上报 ' + str(caps.get('max_clients_system')) if caps.get('max_clients_readable') else '系统未上报'}"
            "（Windows 未开放写入接口，本工具按软上限告警）",
        ]
        for note in caps.get("notes", []) or []:
            lines.append(f"· {note}")
        if not caps.get("admin"):
            lines.append("· 当前非管理员运行，开启/停止热点可能失败，建议在「设置」页以管理员重启。")
        self.cap_text.configure(state="normal")
        self.cap_text.delete("1.0", "end")
        self.cap_text.insert("1.0", "\n".join(lines))
        self.cap_text.configure(state="disabled")

    # ------------------------------------------------------------------ #
    def toggle_hotspot(self) -> None:
        import threading

        def work():
            if self.app.status.active:
                ok, msg = self.app.controller.stop()
            else:
                ok, msg = self.app.controller.start()
            self.app.notify(msg, "success" if ok else "error")
            self.app.request_refresh(True)

        self.btn_toggle.configure(state="disabled")
        threading.Thread(target=work, daemon=True).start()
        self.after_safe(1200, lambda: self.btn_toggle.configure(state="normal"))

    def copy_pass(self) -> None:
        pwd = self.app.status.passphrase or self.app.cfg.hotspot.passphrase
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(pwd)
            self.app.notify("密码已复制到剪贴板", "success")
        except Exception as exc:
            self.app.notify(f"复制失败：{exc}", "error")

    @property
    def root(self):
        return self.winfo_toplevel()

    def open_system_settings(self) -> None:
        import subprocess

        try:
            subprocess.Popen(["start", "ms-settings:network-mobilehotspot"], shell=True)
        except Exception as exc:
            self.app.notify(f"打开失败：{exc}", "error")

    def open_ncpa(self) -> None:
        import subprocess

        try:
            subprocess.Popen(["ncpa.cpl"], shell=True)
        except Exception as exc:
            self.app.notify(f"打开失败：{exc}", "error")

    def open_portal_local(self) -> None:
        self.app.open_url(self.app.portal.local_url())

    def open_data_dir(self) -> None:
        import subprocess
        from ...core.paths import DATA_DIR

        try:
            subprocess.Popen(["explorer", str(DATA_DIR)])
        except Exception as exc:
            self.app.notify(f"打开失败：{exc}", "error")
