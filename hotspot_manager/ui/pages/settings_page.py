"""设置：外观 / 统计方式 / 开机自启 / 演示数据 / 诊断 / 关于。"""
from __future__ import annotations

import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import List

from ...core.config import TRAFFIC_CHOICES
from ...core import pshell
from ...core.storage import human_bytes
from ...core.traffic import DEMO_CLIENTS, DEMO_MACS
from ...core.paths import APP_TITLE, APP_VERSION, DATA_DIR, LOG_DIR
from ..widgets import Card, ScrollFrame, Toggle, labeled_switch
from .base import Page

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "WifiHotspotManager"


class SettingsPage(Page):
    key = "settings"
    title = "设置"
    nav_label = "设置"

    def build(self) -> None:
        wrap = ScrollFrame(self, self.p)
        wrap.pack(fill="both", expand=True)
        wrap.body.columnconfigure(0, weight=1)
        self.scroll = wrap

        # ---------- 外观 ----------
        look = Card(wrap.body, self.p, title="外观", padx=16, pady=14)
        look.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 10))
        row = ttk.Frame(look.body, style="Surface.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="主题", style="H3.TLabel").pack(side="left")
        self.theme_var = tk.StringVar(value=self.app.cfg.theme)
        for val, text in (("dark", "深色"), ("light", "浅色")):
            ttk.Radiobutton(row, text=text, value=val, variable=self.theme_var,
                            command=self.on_theme).pack(side="left", padx=(16, 0))
        ttk.Label(look.body,
                  text="侧边栏可用左上角的 ☰ 按钮折叠/展开。",
                  style="Dim.TLabel").pack(anchor="w", pady=(8, 0))

        # ---------- 统计 ----------
        stat = Card(wrap.body, self.p, title="流量统计", padx=16, pady=14)
        stat.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        sb = stat.body
        sb.columnconfigure(1, weight=1)
        ttk.Label(sb, text="统计方式", style="H3.TLabel").grid(row=0, column=0, sticky="w",
                                                              pady=6)
        self.traffic_var = tk.StringVar(value=self.app.cfg.traffic_backend)
        self.traffic_combo = ttk.Combobox(
            sb, textvariable=self.traffic_var, state="readonly", width=34,
            values=[f"{k}  —  {label}" for k, label in TRAFFIC_CHOICES])
        self.traffic_combo.grid(row=0, column=1, sticky="w", padx=(12, 0), pady=6)
        for k, label in TRAFFIC_CHOICES:
            if k == self.app.cfg.traffic_backend:
                self.traffic_combo.set(f"{k}  —  {label}")
                break
        self.traffic_combo.bind("<<ComboboxSelected>>", self.on_traffic_backend)
        self.traffic_hint = ttk.Label(
            sb,
            text="精确统计需要安装 Npcap(https://npcap.com) + pip install scapy；"
                 "未安装时自动降级为「网卡总量统计」，只能看到整体速率。",
            style="Dim.TLabel", wraplength=560, justify="left")
        self.traffic_hint.grid(row=1, column=1, sticky="w", padx=(12, 0))
        self.traffic_state = ttk.Label(sb, text="", style="Accent.TLabel")
        self.traffic_state.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(6, 0))

        ttk.Label(sb, text="刷新间隔", style="H3.TLabel").grid(row=3, column=0, sticky="w",
                                                              pady=6)
        pr = ttk.Frame(sb, style="Surface.TFrame")
        pr.grid(row=3, column=1, sticky="w", padx=(12, 0), pady=6)
        self.poll_var = tk.DoubleVar(value=self.app.cfg.poll_interval)
        ttk.Spinbox(pr, from_=1, to=30, increment=0.5, width=6,
                    textvariable=self.poll_var,
                    command=self.on_poll).grid(row=0, column=0)
        ttk.Label(pr, text="秒（越小越实时，系统开销越大）",
                  style="Dim.TLabel").grid(row=0, column=1, padx=(8, 0))

        demo_row, self.demo_tg = labeled_switch(
            sb, self.p, "演示数据", self.app.cfg.demo_mode,
            on_change=self.on_demo)
        demo_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        # ---------- 启动 ----------
        boot = Card(wrap.body, self.p, title="启动与权限", padx=16, pady=14)
        boot.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        bb = boot.body
        bb.columnconfigure(0, weight=1)
        auto_row, self.autostart_tg = labeled_switch(
            bb, self.p, "随 Windows 开机启动", self._read_autostart(),
            on_change=self.on_autostart)
        auto_row.grid(row=0, column=0, sticky="ew")
        adm_row = ttk.Frame(bb, style="Surface.TFrame")
        adm_row.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        is_admin = pshell.is_admin()
        self.admin_label = ttk.Label(
            adm_row,
            text=("当前以管理员身份运行 ✓" if is_admin else
                  "当前非管理员：启动/停止热点、DNS 重定向可能失败"),
            style="Success.TLabel" if is_admin else "Warn.TLabel")
        self.admin_label.pack(side="left")
        if not is_admin:
            ttk.Button(adm_row, text="以管理员身份重启", style="Primary.TButton",
                       command=self.relaunch_admin).pack(side="left", padx=(14, 0))

        # ---------- 诊断 ----------
        diag = Card(wrap.body, self.p, title="环境诊断", padx=16, pady=14)
        diag.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 10))
        ttk.Button(diag.body, text="重新检测", command=self.run_diagnose).pack(anchor="w",
                                                                              pady=(0, 8))
        self.diag_text = tk.Text(diag.body, height=10, wrap="word", bg=self.p.input_bg,
                                 fg=self.p.text, bd=0, highlightthickness=1,
                                 highlightbackground=self.p.border,
                                 font=("Cascadia Mono", 9), insertbackground=self.p.text)
        self.diag_text.pack(fill="x")
        self.diag_text.insert("1.0", "点击「重新检测」查看本机热点能力诊断。")
        self.diag_text.configure(state="disabled")

        # ---------- 数据 ----------
        data = Card(wrap.body, self.p, title="数据与维护", padx=16, pady=14)
        data.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 10))
        db = data.body
        ttk.Label(db, text=f"数据目录：{DATA_DIR}", style="Muted.TLabel",
                  wraplength=640, justify="left").pack(anchor="w")
        btn_row = ttk.Frame(db, style="Surface.TFrame")
        btn_row.pack(anchor="w", pady=(10, 0))
        ttk.Button(btn_row, text="打开数据目录", command=self.open_data).pack(side="left")
        ttk.Button(btn_row, text="打开日志目录", command=self.open_logs).pack(side="left",
                                                                            padx=8)
        ttk.Button(btn_row, text="清除演示数据", style="Danger.TButton",
                   command=self.clear_demo).pack(side="left")
        ttk.Button(btn_row, text="清空全部统计", style="Danger.TButton",
                   command=self.clear_all).pack(side="left", padx=8)

        # ---------- 关于 ----------
        about = Card(wrap.body, self.p, title="关于", padx=16, pady=14)
        about.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 20))
        about_text = (
            f"{APP_TITLE} v{APP_VERSION}\n"
            "基于 Windows 移动热点(WinRT) / 承载网络(netsh) 的图形化管理工具。\n\n"
            "功能：热点开关与配置、频段与容量、已连接设备识别与管理、\n"
            "每台设备的实时速率与历史用量、欢迎页(Captive Portal)。\n\n"
            "说明：Windows 未开放「最大连接数写入」「踢出设备」「加密方式选择」的公共接口，\n"
            "本工具会在界面上明确标注这些受限项，不会给出虚假的成功提示。"
        )
        ttk.Label(about.body, text=about_text, style="Muted.TLabel",
                  justify="left").pack(anchor="w")

        wrap.bind_children_wheel()

    # ------------------------------------------------------------------ #
    #                                外观                                 #
    # ------------------------------------------------------------------ #
    def on_theme(self) -> None:
        dark = self.theme_var.get() != "light"
        if dark == (self.app.cfg.theme != "light"):
            return
        self.app.apply_theme(dark)

    # ------------------------------------------------------------------ #
    #                                统计                                 #
    # ------------------------------------------------------------------ #
    def on_traffic_backend(self, _e=None) -> None:
        val = str(self.traffic_var.get()).split("—")[0].strip()
        self.app.cfg.traffic_backend = val
        self.app.save_config()

        def work():
            try:
                self.app.traffic.restart(traffic_backend=val,
                                         demo_mode=self.app.cfg.demo_mode)
            except Exception:
                import logging

                logging.exception("重启流量统计失败")

        threading.Thread(target=work, daemon=True).start()
        self.app.notify("正在切换统计方式…", "info")
        self.after_safe(1500, self.refresh)

    def on_poll(self) -> None:
        try:
            val = float(self.poll_var.get())
        except (ValueError, tk.TclError):
            return
        val = max(1.0, min(30.0, val))
        self.app.cfg.poll_interval = val
        self.app.save_config()

    def on_demo(self, value: bool) -> None:
        self.app.cfg.demo_mode = bool(value)
        self.app.save_config()

        def work():
            try:
                self.app.traffic.restart(demo_mode=bool(value))
            except Exception:
                import logging

                logging.exception("切换演示模式失败")

        threading.Thread(target=work, daemon=True).start()
        self.app.request_refresh(True)
        self.app.notify("已开启演示数据" if value else "已关闭演示数据", "success")
        self.after_safe(1200, self.refresh)

    def clear_demo(self) -> None:
        if not messagebox.askyesno("确认", "清除演示设备及其全部统计记录？"):
            return
        for mac in DEMO_MACS:
            self.app.db.reset_device(mac)
            self.app.devman.forget(mac)
        self.app.notify("演示数据已清除", "success")
        self.app.request_refresh(True)

    def clear_all(self) -> None:
        if not messagebox.askyesno(
                "确认", "清空所有设备的历史用量记录？\n设备名称、图标等档案会保留。"):
            return
        for rec in self.app.store.all():
            self.app.db.reset_device(rec.get("mac", ""))
        self.app.db.reset_device("__total__")
        self.app.notify("已清空全部统计", "success")

    # ------------------------------------------------------------------ #
    #                             开机自启                                 #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _read_autostart() -> bool:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                val, _ = winreg.QueryValueEx(key, RUN_NAME)
                return bool(val)
        except OSError:
            return False
        except Exception:
            return False

    @staticmethod
    def _write_autostart(enable: bool) -> bool:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                if enable:
                    main_py = str(Path(sys.argv[0]).resolve())
                    cmd = f'"{sys.executable}" "{main_py}"'
                    winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, RUN_NAME)
                    except OSError:
                        pass
            return True
        except Exception:
            return False

    def on_autostart(self, value: bool) -> None:
        ok = self._write_autostart(bool(value))
        if ok:
            self.app.notify("开机自启已" + ("开启" if value else "关闭"), "success")
        else:
            self.autostart_tg.set(not value, notify=False)
            self.app.notify("写入注册表失败（权限不足？）", "error")

    def relaunch_admin(self) -> None:
        if messagebox.askyesno("确认", "以管理员身份重启本程序？当前窗口会关闭。"):
            if pshell.relaunch_as_admin():
                self.winfo_toplevel().destroy()
            else:
                self.app.notify("提权失败或被取消", "error")

    # ------------------------------------------------------------------ #
    #                                诊断                                 #
    # ------------------------------------------------------------------ #
    def run_diagnose(self) -> None:
        self.diag_text.configure(state="normal")
        self.diag_text.delete("1.0", "end")
        self.diag_text.insert("1.0", "检测中…")
        self.diag_text.configure(state="disabled")

        def work():
            try:
                lines: List[str] = self.app.controller.diagnose()
            except Exception as exc:
                lines = [f"诊断失败：{exc}"]
            self.app.after_main(0, self._show_diag, lines)

        threading.Thread(target=work, daemon=True).start()

    def _show_diag(self, lines: List[str]) -> None:
        try:
            if not self.diag_text.winfo_exists():
                return
            self.diag_text.configure(state="normal")
            self.diag_text.delete("1.0", "end")
            self.diag_text.insert("1.0", "\n".join(lines))
            self.diag_text.configure(state="disabled")
        except (tk.TclError, RuntimeError):
            pass

    # ------------------------------------------------------------------ #
    def open_data(self) -> None:
        try:
            subprocess.Popen(["explorer", str(DATA_DIR)])
        except Exception as exc:
            self.app.notify(f"打开失败：{exc}", "error")

    def open_logs(self) -> None:
        try:
            subprocess.Popen(["explorer", str(LOG_DIR)])
        except Exception as exc:
            self.app.notify(f"打开失败：{exc}", "error")

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        self.traffic_state.configure(
            text=f"当前：{self.app.traffic.status_text}")
