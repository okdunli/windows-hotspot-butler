"""热点设置：SSID / 密码 / 加密 / 频段 / 最大连接数 / 后端。

所有选项按运行时探测到的网卡能力动态启用或置灰，不写死任何一种机型。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from ...core.config import BAND_CHOICES, BACKEND_CHOICES, SECURITY_CHOICES
from ..widgets import Card, ScrollFrame, labeled_switch
from .base import Page


class HotspotPage(Page):
    key = "hotspot"
    title = "热点设置"
    nav_label = "热点设置"

    def build(self) -> None:
        wrap = ScrollFrame(self, self.p)
        wrap.pack(fill="both", expand=True)
        wrap.body.columnconfigure(0, weight=1)
        self.scroll = wrap

        # ---------- 开热点能力警告横幅（开热点前就提示换网卡） ----------
        self.warn_frame = tk.Frame(wrap.body, bg=self.p.danger, bd=0,
                                   highlightthickness=0)
        self.warn_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 0))
        self.warn_frame.columnconfigure(0, weight=1)
        self.warn_inner = tk.Frame(self.warn_frame, bg=self.p.surface,
                                   padx=14, pady=10)
        self.warn_inner.grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        self.warn_inner.columnconfigure(1, weight=1)
        self.warn_icon = tk.Label(self.warn_inner, text="!", bg=self.p.surface,
                                  fg=self.p.danger,
                                  font=("Microsoft YaHei UI", 16, "bold"))
        self.warn_icon.grid(row=0, column=0, padx=(6, 12), sticky="nw")
        self.warn_label = ttk.Label(self.warn_inner, text="",
                                    style="Danger.TLabel", wraplength=620,
                                    justify="left")
        self.warn_label.grid(row=0, column=1, sticky="w")
        self.warn_frame.grid_remove()  # 默认隐藏，确认不能托管热点时才显示

        # ---------- 基础配置 ----------
        basic = Card(wrap.body, self.p, title="基础配置", padx=16, pady=14)
        basic.grid(row=1, column=0, sticky="ew", padx=20, pady=(18, 10))
        b = basic.body
        b.columnconfigure(1, weight=1)

        ttk.Label(b, text="网络名称 (SSID)", style="H3.TLabel").grid(
            row=0, column=0, sticky="w", pady=6)
        self.ssid_var = tk.StringVar(value=self.app.cfg.hotspot.ssid)
        self.ssid_entry = ttk.Entry(b, textvariable=self.ssid_var, width=32)
        self.ssid_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=6)
        self.ssid_hint = ttk.Label(b, text="", style="Dim.TLabel")
        self.ssid_hint.grid(row=1, column=1, sticky="w", padx=(12, 0))

        ttk.Label(b, text="密码", style="H3.TLabel").grid(row=2, column=0, sticky="w", pady=6)
        pw_row = ttk.Frame(b, style="Surface.TFrame")
        pw_row.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=6)
        pw_row.columnconfigure(0, weight=1)
        self.pw_var = tk.StringVar(value=self.app.cfg.hotspot.passphrase)
        self.pw_entry = ttk.Entry(pw_row, textvariable=self.pw_var, show="●")
        self.pw_entry.grid(row=0, column=0, sticky="ew")
        self.pw_btn = ttk.Button(pw_row, text="显示", width=6, command=self.toggle_pw)
        self.pw_btn.grid(row=0, column=1, padx=(8, 0))
        self.pw_hint = ttk.Label(b, text="", style="Dim.TLabel")
        self.pw_hint.grid(row=3, column=1, sticky="w", padx=(12, 0))

        ttk.Label(b, text="加密方式", style="H3.TLabel").grid(row=4, column=0, sticky="w", pady=6)
        self.sec_var = tk.StringVar(value=self.app.cfg.hotspot.security)
        self.sec_combo = ttk.Combobox(
            b, textvariable=self.sec_var, state="readonly", width=28,
            values=[f"{k}  —  {label}" for k, label in SECURITY_CHOICES])
        self.sec_combo.grid(row=4, column=1, sticky="w", padx=(12, 0), pady=6)
        self._set_combo(self.sec_combo, self.sec_var, SECURITY_CHOICES)
        self.sec_hint = ttk.Label(b, text="", style="Dim.TLabel", wraplength=520,
                                  justify="left")
        self.sec_hint.grid(row=5, column=1, sticky="w", padx=(12, 0))

        # ---------- 频段与容量 ----------
        band_card = Card(wrap.body, self.p, title="频段与容量", padx=16, pady=14)
        band_card.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        bb = band_card.body
        bb.columnconfigure(1, weight=1)

        ttk.Label(bb, text="频段", style="H3.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        band_row = ttk.Frame(bb, style="Surface.TFrame")
        band_row.grid(row=0, column=1, sticky="w", padx=(12, 0), pady=6)
        self.band_var = tk.StringVar(value=self.app.cfg.hotspot.band)
        self.band_buttons = {}
        for i, (val, text) in enumerate(BAND_CHOICES):
            rb = ttk.Radiobutton(band_row, text=text, value=val,
                                 variable=self.band_var, command=self._on_band_change)
            rb.grid(row=0, column=i, padx=(0, 14))
            self.band_buttons[val] = rb
        self.band_hint = ttk.Label(bb, text="", style="Dim.TLabel", wraplength=520,
                                   justify="left")
        self.band_hint.grid(row=1, column=1, sticky="w", padx=(12, 0))

        ttk.Label(bb, text="最大连接设备数", style="H3.TLabel").grid(
            row=2, column=0, sticky="w", pady=6)
        cap_row = ttk.Frame(bb, style="Surface.TFrame")
        cap_row.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=6)
        self.max_var = tk.IntVar(value=self.app.cfg.hotspot.max_clients)
        self.max_spin = ttk.Spinbox(cap_row, from_=1, to=100, width=6,
                                    textvariable=self.max_var, wrap=False)
        self.max_spin.grid(row=0, column=0)
        self.max_label = ttk.Label(cap_row, text="台", style="Muted.TLabel")
        self.max_label.grid(row=0, column=1, padx=(6, 0))
        self.max_hint = ttk.Label(bb, text="", style="Dim.TLabel", wraplength=520,
                                  justify="left")
        self.max_hint.grid(row=3, column=1, sticky="w", padx=(12, 0))

        # ---------- 后端与行为 ----------
        adv = Card(wrap.body, self.p, title="后端与行为", padx=16, pady=14)
        adv.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 10))
        ab = adv.body
        ab.columnconfigure(1, weight=1)

        ttk.Label(ab, text="控制后端", style="H3.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        self.be_var = tk.StringVar(value=self.app.cfg.hotspot.backend)
        self.be_combo = ttk.Combobox(ab, textvariable=self.be_var, state="readonly",
                                     width=30,
                                     values=[f"{k}  —  {label}" for k, label in BACKEND_CHOICES])
        self.be_combo.grid(row=0, column=1, sticky="w", padx=(12, 0), pady=6)
        self._set_combo(self.be_combo, self.be_var, BACKEND_CHOICES)
        self.be_hint = ttk.Label(ab, text="", style="Dim.TLabel", wraplength=520,
                                 justify="left")
        self.be_hint.grid(row=1, column=1, sticky="w", padx=(12, 0))

        auto_row, self.auto_tg = labeled_switch(
            ab, self.p, "启动程序时自动开启热点", self.app.cfg.hotspot.auto_start,
            on_change=lambda v: self._set_hotspot_cfg("auto_start", v))
        auto_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        enf_row, self.enf_tg = labeled_switch(
            ab, self.p, "超出上限时提醒并标记新设备", self.app.cfg.hotspot.enforce_max_clients,
            on_change=lambda v: self._set_hotspot_cfg("enforce_max_clients", v))
        enf_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        wps_row, self.wps_tg = labeled_switch(
            ab, self.p, "WPS 一键配对", self.app.cfg.hotspot.wps_enabled,
            on_change=lambda v: self._set_hotspot_cfg("wps_enabled", v))
        wps_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.wps_hint = ttk.Label(ab, text="", style="Dim.TLabel", wraplength=520,
                                  justify="left")
        self.wps_hint.grid(row=5, column=0, columnspan=2, sticky="w", padx=(12, 0))

        # ---------- 操作 ----------
        act = ttk.Frame(wrap.body, style="TFrame")
        act.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.btn_apply = ttk.Button(act, text="应用配置到系统", style="Primary.TButton",
                                    command=self.apply_config)
        self.btn_apply.pack(side="left")
        ttk.Button(act, text="读取系统当前值", command=self.read_system).pack(
            side="left", padx=8)
        self.btn_start = ttk.Button(act, text="开启热点", command=self.start_hotspot)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(act, text="停止热点", style="Danger.TButton",
                                   command=self.stop_hotspot)
        self.btn_stop.pack(side="left", padx=8)
        self.result_label = ttk.Label(act, text="", style="Muted.TLabel", wraplength=460,
                                      justify="left")
        self.result_label.pack(side="left", padx=(12, 0))

        # ---------- 系统信息 ----------
        info = Card(wrap.body, self.p, title="系统当前热点信息", padx=16, pady=12)
        info.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 20))
        self.info_text = tk.Text(info.body, height=7, wrap="word", bg=self.p.surface,
                                 fg=self.p.text, bd=0, highlightthickness=0,
                                 font=("Cascadia Mono", 9), insertbackground=self.p.text)
        self.info_text.pack(fill="x")
        self.info_text.configure(state="disabled")

        self.ssid_var.trace_add("write", lambda *_: self._validate())
        self.pw_var.trace_add("write", lambda *_: self._validate())
        self.sec_combo.bind("<<ComboboxSelected>>", lambda _e: self._validate())
        self.be_combo.bind("<<ComboboxSelected>>", lambda _e: self._validate())
        wrap.bind_children_wheel()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _set_combo(combo, var, choices) -> None:
        cur = var.get()
        for val, label in choices:
            if val == cur:
                combo.set(f"{val}  —  {label}")
                return
        combo.set(str(choices[0][0]))

    @staticmethod
    def _read_combo(var) -> str:
        return str(var.get()).split("—")[0].strip()

    def _set_hotspot_cfg(self, key: str, value) -> None:
        setattr(self.app.cfg.hotspot, key, value)
        self.app.save_config()

    def toggle_pw(self) -> None:
        if self.pw_entry.cget("show") == "":
            self.pw_entry.configure(show="●")
            self.pw_btn.configure(text="显示")
        else:
            self.pw_entry.configure(show="")
            self.pw_btn.configure(text="隐藏")

    def _on_band_change(self) -> None:
        self._validate()

    def _validate(self) -> None:
        ssid = self.ssid_var.get().strip()
        pw = self.pw_var.get()
        sec = self._read_combo(self.sec_var)
        hints = []
        if not ssid:
            hints.append("网络名称不能为空")
        elif len(ssid) > 32:
            hints.append("网络名称超过 32 字符，可能被系统截断")
        if sec == "open":
            self.pw_entry.configure(state="disabled")
            hints.append("开放网络无需密码，任何人都能连接")
        else:
            self.pw_entry.configure(state="normal")
            if len(pw) < 8:
                hints.append("密码至少需要 8 位")
            elif len(pw) > 63:
                hints.append("密码超过 63 位上限")
        self.ssid_hint.configure(text="")
        self.pw_hint.configure(text="；".join(hints))
        self.pw_hint.configure(
            foreground=self.p.warning if hints else self.p.dim)

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        st = self.app.status
        lines = [
            f"状态       ：{st.state_text}",
            f"网络名称   ：{st.ssid or '(未读取)'}",
            f"密码       ：{('*' * len(st.passphrase)) if st.passphrase else '(未读取)'}",
            f"频段       ：{ {'2.4': '2.4 GHz', '5': '5 GHz', 'auto': '自动'}.get(st.band, st.band or '(未读取)') }",
            f"客户端     ：{st.client_count}" + (f" / 上限 {st.max_clients}" if st.max_clients else ""),
            f"控制后端   ：{self.app.controller.backend_label}",
            f"热点网关   ：{self.app.gateway_ip}",
        ]
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", "\n".join(lines))
        self.info_text.configure(state="disabled")
        self.btn_start.configure(state="disabled" if st.active else "normal")
        self.btn_stop.configure(state="normal" if st.active else "disabled")

    def on_caps(self, caps) -> None:
        self._apply_caps(caps)

    def _apply_caps(self, caps=None) -> None:
        caps = caps if caps is not None else self.app.capabilities
        if not caps:
            return
        # --- 频段 ---
        band_writable = bool(caps.get("band"))
        b5 = bool(caps.get("band_5"))
        for val, btn in self.band_buttons.items():
            disabled = False
            if val == "5":
                disabled = (not band_writable) and caps.get("band_5_known") is False
                disabled = disabled or (caps.get("band_5_known") and not b5)
            if val == "2.4":
                disabled = not bool(caps.get("band_24"))
            btn.configure(state="disabled" if disabled else "normal")
        if not band_writable:
            self.band_hint.configure(
                text="当前后端不支持指定频段，将跟随系统默认。",
                foreground=self.p.warning)
        elif caps.get("band_5_known") and not b5:
            self.band_hint.configure(
                text="本机无线网卡不支持 5 GHz（" + str(caps.get("radios")) + "），"
                     "已禁用该选项。", foreground=self.p.warning)
        else:
            self.band_hint.configure(
                text=f"网卡无线电类型：{caps.get('radios')}"
                     f"，5 GHz {caps.get('band_5_text')}。",
                foreground=self.p.dim)
            if not b5 and not caps.get("band_5_known"):
                self.band_hint.configure(
                    text="未能确定 5 GHz 支持情况，可先开启热点后再查看系统实际频段。",
                    foreground=self.p.dim)

        # --- 加密 ---
        if caps.get("security_choice"):
            self.sec_combo.configure(state="readonly")
            self.sec_hint.configure(
                text=f"驱动能力：WPA2 {'√' if caps.get('wpa2') else '×'}，"
                     f"WPA3 {'√' if caps.get('wpa3') else '×'}；"
                     "netsh 后端固定为 WPA2-个人，其它选项仅本地保存。",
                foreground=self.p.dim)
        else:
            self.sec_combo.configure(state="disabled")
            self.sec_hint.configure(
                text="Windows 移动热点接口固定使用 WPA2-个人，暂不允许程序修改加密方式"
                     f"（网卡驱动本身{'支持' if caps.get('wpa3') else '不支持'} WPA3）。",
                foreground=self.p.warning)

        # --- 后端 ---
        rec = caps.get("recommended_backend") or ""
        if (not caps.get("hosted_supported")
                and caps.get("soft_ap_supported") is not True
                and caps.get("wifi_direct_supported") is not True
                and not caps.get("winrt_available")):
            self.be_hint.configure(
                text="本机网卡不支持软 AP / Wi-Fi Direct / 承载网络，目前没有可用后端——"
                     "需更换支持热点的无线网卡，或安装带虚拟 AP 驱动的共享软件。",
                foreground=self.p.danger)
        elif rec == "netsh":
            self.be_hint.configure(
                text="本机不支持软 AP（移动热点 WinRT 用不了），但支持承载网络(hostednetwork)，"
                     "这正是猎豹/360 WiFi 用的方案。请把上方「控制后端」设为"
                     "「承载网络(netsh)」或「自动选择」，即可正常开热点。",
                foreground=self.p.warning)
        else:
            winrt_ok = (caps.get("soft_ap_supported") is True
                        or caps.get("wifi_direct_supported") is True
                        or bool(caps.get("winrt_available")))
            winrt_text = ("可用（Wi-Fi Direct / 软 AP）"
                          if (caps.get("soft_ap_supported") is True
                              or caps.get("wifi_direct_supported") is True)
                          else ("可用（Windows 移动热点 API）"
                                if caps.get("winrt_available") else "本机网卡不支持软 AP / Wi-Fi Direct"))
            self.be_hint.configure(
                text=("承载网络(hostednetwork)："
                      + ("可用" if caps.get("hosted_supported") else "本机网卡驱动不支持")
                      + "；移动热点(WinRT)：" + winrt_text
                      + "。推荐「自动选择」。"),
                foreground=self.p.warning if not (caps.get("hosted_supported") or winrt_ok) else self.p.dim)

        # --- 最大连接数 ---
        sysmax = caps.get("max_clients_system")
        if sysmax:
            self.max_spin.configure(to=max(100, sysmax))
            self.max_hint.configure(
                text=f"系统当前允许最多 {sysmax} 台设备。Windows 未开放写入接口，"
                     f"此处设置仅作为提醒阈值（超出时标记并提示）。",
                foreground=self.p.warning)
        else:
            self.max_hint.configure(
                text="系统未上报上限（热点未开启时常见）。此处设置仅作为提醒阈值。",
                foreground=self.p.dim)

        # --- WPS ---
        if caps.get("backend") == "netsh" and caps.get("hosted_supported"):
            self.wps_hint.configure(
                text="承载网络(netsh)模式下，可在支持 WPS 的终端上按配对按钮免密接入；"
                     "移动热点(WinRT)模式由 Windows 自行管理，无需单独开启。",
                foreground=self.p.dim)
        else:
            self.wps_hint.configure(
                text="移动热点(WinRT)由 Windows 自行管理 WPS，本开关仅作记录；"
                     "如需在终端上一键配对，请在终端本身启用 WPS。",
                foreground=self.p.warning)

        # --- 开热点能力警告（在用户点击「开启热点」之前就提示） ---
        can_host = caps.get("can_host")
        if can_host is False:
            reason = (caps.get("host_block_reason")
                      or "本机无线网卡不支持承载 WiFi 热点。")
            detail = caps.get("host_block_detail") or ""
            text = "⚠ " + reason
            if detail:
                text += "\n\n" + detail
            self.warn_label.configure(text=text)
            self.warn_frame.grid()          # 显示横幅
            self.warn_frame.update_idletasks()
        else:
            self.warn_frame.grid_remove()   # 隐藏横幅

    # ------------------------------------------------------------------ #
    def _collect(self) -> bool:
        ssid = self.ssid_var.get().strip()
        pw = self.pw_var.get()
        sec = self._read_combo(self.sec_var)
        if not ssid:
            self.app.notify("网络名称不能为空", "error")
            return False
        if sec != "open" and len(pw) < 8:
            self.app.notify("密码至少需要 8 位", "error")
            return False
        h = self.app.cfg.hotspot
        h.ssid = ssid
        h.passphrase = pw
        h.security = sec
        h.band = self.band_var.get()
        try:
            h.max_clients = max(1, min(100, int(self.max_var.get())))
        except (ValueError, tk.TclError):
            h.max_clients = 8
        h.wps_enabled = bool(self.wps_tg.value)
        h.backend = self._read_combo(self.be_var)
        self.app.cfg.normalize()
        self.app.save_config()
        return True

    def apply_config(self) -> None:
        if not self._collect():
            return
        self.btn_apply.configure(state="disabled", text="正在应用…")
        self.result_label.configure(text="")

        def work():
            try:
                ok, msg = self.app.controller.apply_config(self.app.cfg.hotspot)
            except Exception as exc:
                ok, msg = False, str(exc)
            self.app.after_main(0, self._after_apply, ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def _after_apply(self, ok: bool, msg: str) -> None:
        self.btn_apply.configure(state="normal", text="应用配置到系统")
        self.result_label.configure(text=msg, foreground=self.p.success if ok else self.p.danger)
        self.app.notify(msg, "success" if ok else "error")
        self.app.request_refresh(True)

    def read_system(self) -> None:
        st = self.app.status
        if st.ssid:
            self.ssid_var.set(st.ssid)
            self.app.cfg.hotspot.ssid = st.ssid
        if st.passphrase:
            self.pw_var.set(st.passphrase)
            self.app.cfg.hotspot.passphrase = st.passphrase
        if st.band:
            self.band_var.set(st.band)
            self.app.cfg.hotspot.band = st.band
        if st.max_clients:
            self.max_var.set(st.max_clients)
            self.app.cfg.hotspot.max_clients = st.max_clients
        self.app.save_config()
        self.app.notify("已写入系统当前值到界面", "success")
        self._validate()

    def start_hotspot(self) -> None:
        if not self._collect():
            return
        self._run_op(self.app.controller.start)

    def stop_hotspot(self) -> None:
        self._run_op(self.app.controller.stop)

    def _run_op(self, fn) -> None:
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="disabled")

        def runner():
            try:
                ok, msg = fn()
            except Exception as exc:
                ok, msg = False, str(exc)
            self.app.after_main(0, self._after_op, ok, msg)

        threading.Thread(target=runner, daemon=True).start()

    def _after_op(self, ok: bool, msg: str) -> None:
        self.result_label.configure(text=msg, foreground=self.p.success if ok else self.p.danger)
        self.app.notify(msg, "success" if ok else "error")
        self.app.request_refresh(True)
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="normal")
