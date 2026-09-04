"""欢迎页（Captive Portal）：模板选择 / 自定义 HTML / DNS 重定向 / 访问记录。"""
from __future__ import annotations

import tempfile
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Dict, List

from ...core import portal as portal_core
from ...core.portal import TEMPLATES, copy_custom_html
from ...core.paths import CUSTOM_PORTAL_DIR
from ..widgets import Card, ScrollFrame, Toggle, labeled_switch
from .base import Page


class PortalPage(Page):
    key = "portal"
    title = "欢迎页"
    nav_label = "欢迎页"

    def build(self) -> None:
        self.template_buttons: Dict[str, tk.Frame] = {}
        self.template_var = tk.StringVar(value=self.app.cfg.portal.template)

        wrap = ScrollFrame(self, self.p)
        wrap.pack(fill="both", expand=True)
        wrap.body.columnconfigure(0, weight=1)
        self.scroll = wrap

        # ---------- 开关 ----------
        top = Card(wrap.body, self.p, title="欢迎页服务", padx=16, pady=14)
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 10))
        top.body.columnconfigure(0, weight=1)
        row, self.enable_tg = labeled_switch(
            top.body, self.p, "启用欢迎页", self.app.cfg.portal.enabled,
            "设备连上 WiFi 后打开任意网页会先看到欢迎页，点击按钮后才放行上网",
            on_change=self.on_toggle_enable)
        row.grid(row=0, column=0, sticky="ew")
        self.status_label = ttk.Label(top.body, text="", style="Muted.TLabel")
        self.status_label.grid(row=1, column=0, sticky="w", pady=(8, 0))

        # ---------- 模板 ----------
        tpl = Card(wrap.body, self.p, title="预制模板", subtitle="点击卡片切换",
                   padx=16, pady=14)
        tpl.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        grid = ttk.Frame(tpl.body, style="Surface.TFrame")
        grid.pack(fill="x")
        for i, (key, name, desc) in enumerate(TEMPLATES):
            card = tk.Frame(grid, bg=self.p.surface2, highlightthickness=1,
                            highlightbackground=self.p.border, cursor="hand2")
            card.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0), sticky="nsew")
            grid.columnconfigure(i, weight=1)
            tk.Label(card, text=name, bg=self.p.surface2, fg=self.p.text,
                     font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=12,
                                                                   pady=(12, 2))
            tk.Label(card, text=desc, bg=self.p.surface2, fg=self.p.muted,
                     font=("Microsoft YaHei UI", 8), wraplength=118,
                     justify="left").pack(anchor="w", padx=12, pady=(0, 10))
            card.bind("<Button-1>", lambda _e, k=key: self.pick_template(k))
            for child in card.winfo_children():
                child.bind("<Button-1>", lambda _e, k=key: self.pick_template(k))
            self.template_buttons[key] = card
        self._highlight_templates()

        # ---------- 自定义 HTML ----------
        self.custom_card = Card(wrap.body, self.p, title="自定义 HTML 文件", padx=16, pady=14)
        self.custom_card.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.custom_card.body.columnconfigure(1, weight=1)
        c = self.custom_card.body
        ttk.Label(c, text="HTML 文件", style="H3.TLabel").grid(row=0, column=0, sticky="w")
        brow = ttk.Frame(c, style="Surface.TFrame")
        brow.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        brow.columnconfigure(0, weight=1)
        self.custom_var = tk.StringVar(value=self.app.cfg.portal.custom_html)
        self.custom_entry = ttk.Entry(brow, textvariable=self.custom_var)
        self.custom_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(brow, text="选择文件…", command=self.pick_custom).grid(
            row=0, column=1, padx=(8, 0))
        self.custom_hint = ttk.Label(
            c, text="支持 {{ssid}} {{title}} {{subtitle}} {{notice}} {{notice_li}} "
                    "{{button}} {{footer}} {{gateway}} {{clients}} {{time}} 占位符；"
                    "同目录下的 css/js/图片可通过 /assets/文件名 引用。",
            style="Dim.TLabel", wraplength=620, justify="left")
        self.custom_hint.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(4, 0))

        # ---------- 服务参数 ----------
        param = Card(wrap.body, self.p, title="服务参数", padx=16, pady=14)
        param.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 10))
        pb = param.body
        pb.columnconfigure(1, weight=1)
        ttk.Label(pb, text="监听端口", style="H3.TLabel").grid(row=0, column=0, sticky="w",
                                                              pady=6)
        self.port_var = tk.IntVar(value=self.app.cfg.portal.port)
        ttk.Spinbox(pb, from_=1, to=65535, width=8, textvariable=self.port_var,
                    command=self.on_param_change).grid(row=0, column=1, sticky="w",
                                                       padx=(12, 0), pady=6)
        self.url_label = ttk.Label(pb, text="", style="Accent.TLabel")
        self.url_label.grid(row=1, column=1, sticky="w", padx=(12, 0))

        dns_row, self.dns_tg = labeled_switch(
            pb, self.p, "DNS 重定向（强制弹出欢迎页）", self.app.cfg.portal.dns_redirect,
            "接管 UDP 53 端口，把客户端的所有域名解析指向本机，触发系统自动弹窗；"
            "若系统 DNS 服务占用该端口会失败",
            on_change=lambda v: self.on_param_change())
        dns_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        req_row, self.req_tg = labeled_switch(
            pb, self.p, "必须点击同意按钮", self.app.cfg.portal.require_accept,
            "关闭后仅展示欢迎页，不做同意记录",
            on_change=lambda v: self.on_param_change())
        req_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        # ---------- 文案 ----------
        text_card = Card(wrap.body, self.p, title="页面文案", padx=16, pady=14)
        text_card.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 10))
        tb = text_card.body
        tb.columnconfigure(1, weight=1)
        self.title_var = tk.StringVar(value=self.app.cfg.portal.title)
        self.subtitle_var = tk.StringVar(value=self.app.cfg.portal.subtitle)
        self.button_var = tk.StringVar(value=self.app.cfg.portal.button)
        self.footer_var = tk.StringVar(value=self.app.cfg.portal.footer)
        for i, (label, var) in enumerate(
                (("主标题", self.title_var), ("副标题", self.subtitle_var),
                 ("按钮文字", self.button_var), ("页脚", self.footer_var))):
            ttk.Label(tb, text=label, style="H3.TLabel").grid(row=i, column=0, sticky="w",
                                                              pady=5)
            ttk.Entry(tb, textvariable=var).grid(row=i, column=1, sticky="ew",
                                                 padx=(12, 0), pady=5)
        ttk.Label(tb, text="须知内容", style="H3.TLabel").grid(row=4, column=0, sticky="nw",
                                                              pady=5)
        self.notice_text = tk.Text(tb, height=5, wrap="word", bg=self.p.input_bg,
                                   fg=self.p.text, bd=0, highlightthickness=1,
                                   highlightbackground=self.p.border,
                                   font=("Microsoft YaHei UI", 9),
                                   insertbackground=self.p.text)
        self.notice_text.grid(row=4, column=1, sticky="ew", padx=(12, 0), pady=5)
        self.notice_text.insert("1.0", self.app.cfg.portal.notice)
        ttk.Label(tb, text="每行一条；部分模板会自动渲染为列表项",
                  style="Dim.TLabel").grid(row=5, column=1, sticky="w", padx=(12, 0))

        # ---------- 操作 ----------
        act = ttk.Frame(wrap.body, style="TFrame")
        act.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.btn_apply = ttk.Button(act, text="保存并应用", style="Primary.TButton",
                                    command=self.apply)
        self.btn_apply.pack(side="left")
        self.btn_preview = ttk.Button(act, text="浏览器预览", command=self.preview)
        self.btn_preview.pack(side="left", padx=8)
        self.btn_start = ttk.Button(act, text="启动服务", command=self.start_service)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(act, text="停止服务", style="Danger.TButton",
                                   command=self.stop_service)
        self.btn_stop.pack(side="left", padx=8)
        self.result_label = ttk.Label(act, text="", style="Muted.TLabel", wraplength=420,
                                      justify="left")
        self.result_label.pack(side="left", padx=(12, 0))

        # ---------- 访问记录 ----------
        logs = Card(wrap.body, self.p, title="欢迎页访问记录", padx=16, pady=12)
        logs.grid(row=6, column=0, sticky="ew", padx=20, pady=(0, 20))
        self.log_text = tk.Text(logs.body, height=8, wrap="none", bg=self.p.input_bg,
                                fg=self.p.text, bd=0, highlightthickness=1,
                                highlightbackground=self.p.border,
                                font=("Cascadia Mono", 9), insertbackground=self.p.text)
        self.log_text.pack(fill="x")
        self.log_text.configure(state="disabled")

        wrap.bind_children_wheel()
        self._sync_enabled()

    # ------------------------------------------------------------------ #
    def _collect(self) -> bool:
        cfg = self.app.cfg.portal
        try:
            port = max(1, min(65535, int(self.port_var.get())))
        except (ValueError, tk.TclError):
            self.app.notify("端口必须是 1-65535 的数字", "error")
            return False
        cfg.port = port
        cfg.template = self.template_var.get()
        cfg.custom_html = self.custom_var.get().strip()
        cfg.dns_redirect = self.dns_tg.value
        cfg.require_accept = self.req_tg.value
        cfg.title = self.title_var.get().strip() or "欢迎接入 {{ssid}}"
        cfg.subtitle = self.subtitle_var.get().strip()
        cfg.button = self.button_var.get().strip() or "同意并开始上网"
        cfg.footer = self.footer_var.get().strip()
        cfg.notice = self.notice_text.get("1.0", "end").strip()
        if cfg.template == "custom" and not Path(cfg.custom_html or "").is_file():
            self.app.notify("请先选择有效的自定义 HTML 文件", "error")
            return False
        self.app.cfg.normalize()
        self.app.save_config()
        return True

    def apply(self) -> None:
        if not self._collect():
            return
        cfg = self.app.cfg.portal
        if not cfg.enabled:
            self.app.notify("配置已保存", "success")
            self.result_label.configure(text="配置已保存（未启用服务）")
            return
        self.btn_apply.configure(state="disabled", text="应用中…")

        def work():
            try:
                ok, msg = self.app.portal.restart(cfg)
            except Exception as exc:
                ok, msg = False, str(exc)
            self.app.after_main(0, self._after, ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def _after(self, ok: bool, msg: str) -> None:
        self.btn_apply.configure(state="normal", text="保存并应用")
        self.result_label.configure(text=msg,
                                    foreground=self.p.success if ok else self.p.danger)
        self.app.notify(msg, "success" if ok else "error")
        self.refresh()

    def on_toggle_enable(self, value: bool) -> None:
        self.app.cfg.portal.enabled = bool(value)
        self.app.save_config()
        self._sync_enabled()
        if value:
            self.apply()
        else:
            self.app.portal.stop()
            self.refresh()

    def on_param_change(self) -> None:
        self.refresh()

    def _sync_enabled(self) -> None:
        """未启用时，把参数区整体置灰，避免误以为已生效。"""
        state = "normal" if self.app.cfg.portal.enabled else "disabled"
        for w in (self.custom_entry,):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        self.enable_tg.set(self.app.cfg.portal.enabled, notify=False)

    def pick_template(self, key: str) -> None:
        self.template_var.set(key)
        self._highlight_templates()
        self._sync_enabled()

    def _highlight_templates(self) -> None:
        cur = self.template_var.get()
        for key, card in self.template_buttons.items():
            on = key == cur
            bg = self.p.elevated if on else self.p.surface2
            card.configure(bg=bg, highlightbackground=self.p.accent if on else self.p.border,
                           highlightthickness=2 if on else 1)
            for child in card.winfo_children():
                child.configure(bg=bg)

    def pick_custom(self) -> None:
        path = filedialog.askopenfilename(
            title="选择欢迎页 HTML 文件",
            initialdir=str(CUSTOM_PORTAL_DIR) if CUSTOM_PORTAL_DIR.exists() else "",
            filetypes=[("网页文件", "*.html *.htm"), ("所有文件", "*.*")])
        if not path:
            return
        dst = copy_custom_html(Path(path))
        self.custom_var.set(str(dst))
        self.template_var.set("custom")
        self._highlight_templates()

    def preview(self) -> None:
        if not self._collect():
            return
        html_text = portal_core.preview_html(self.app.cfg.portal,
                                             self.app.portal_context())
        try:
            tmp = Path(tempfile.gettempdir()) / "hotspot_portal_preview.html"
            tmp.write_text(html_text, encoding="utf-8")
            self.app.open_url(tmp.as_uri())
            self.app.notify("已在浏览器打开预览", "success")
        except Exception as exc:
            self.app.notify(f"预览失败：{exc}", "error")

    def start_service(self) -> None:
        if not self._collect():
            return

        def work():
            try:
                ok, msg = self.app.portal.start(self.app.cfg.portal)
            except Exception as exc:
                ok, msg = False, str(exc)
            self.app.after_main(0, self._after, ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def stop_service(self) -> None:
        ok, msg = self.app.portal.stop()
        self.result_label.configure(text=msg)
        self.app.notify(msg, "success" if ok else "error")
        self.refresh()

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        cfg = self.app.cfg.portal
        running = self.app.portal.running
        gw = self.app.gateway_ip or "192.168.137.1"
        self.url_label.configure(text=f"客户端访问地址：http://{gw}:{cfg.port}/")
        state = "运行中" if running else "未运行"
        self.status_label.configure(
            text=f"服务状态：{state}    模板："
                 f"{dict((k, n) for k, n, _d in TEMPLATES).get(cfg.template, cfg.template)}"
                 f"    端口：{cfg.port}")
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")

        visits = self.app.db.portal_visits(40)
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        if not visits:
            self.log_text.insert("1.0", "暂无访问记录。设备连入 WiFi 并打开网页后会出现在这里。")
        else:
            lines = []
            for v in visits:
                ts = datetime.fromtimestamp(v["ts"]).strftime("%m-%d %H:%M:%S")
                mac = v["mac"] or "—"
                lines.append(f"{ts}  {v['ip']:<16} {mac:<19} "
                             f"{'已同意' if v['accepted'] else '仅浏览'}  {v['ua'][:44]}")
            self.log_text.insert("1.0", "\n".join(lines))
        self.log_text.configure(state="disabled")
