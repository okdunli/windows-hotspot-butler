"""主窗口：侧边栏 + 分页内容 + 状态栏 + 后台轮询。"""
from __future__ import annotations

import logging
import queue
import threading
import time
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk

from ..core import netinfo, pshell
from ..core.config import AppConfig
from ..core.deviceman import DeviceManager
from ..core.hotspot import HotspotController, HotspotStatus, RawClient
from ..core.portal import PortalContext, PortalManager
from ..core.storage import DeviceStore, TrafficDB, human_bytes, human_rate
from ..core.traffic import DEMO_CLIENTS, TrafficMonitor
from ..core.paths import APP_TITLE, APP_VERSION
from .pages.base import Page
from .pages.devices_page import DevicesPage
from .pages.hotspot_page import HotspotPage
from .pages.overview import OverviewPage
from .pages.portal_page import PortalPage
from .pages.settings_page import SettingsPage
from .theme import Palette, apply_ttk_style
from .widgets import Sidebar, Toast

log = logging.getLogger(__name__)

NAV_ITEMS = (
    ("overview", "概览"),
    ("hotspot", "热点设置"),
    ("devices", "已连接设备"),
    ("portal", "欢迎页"),
    ("settings", "设置"),
)


class AppWindow:
    def __init__(self, root: tk.Tk, cfg: AppConfig, controller: HotspotController,
                 store: DeviceStore, db: TrafficDB, traffic: TrafficMonitor,
                 devman: DeviceManager, portal: PortalManager) -> None:
        self.root = root
        self.cfg = cfg
        self.controller = controller
        self.store = store
        self.db = db
        self.traffic = traffic
        self.devman = devman
        self.portal = portal

        self.palette = Palette(dark=(cfg.theme != "light"))
        self.style = apply_ttk_style(root, self.palette)
        self.toast = Toast(root, self.palette)

        self.status: HotspotStatus = HotspotStatus(state="unknown", backend="none")
        self.clients: List = []
        self.gateway_ip = netinfo.ICS_DEFAULT_IP  # 占位，首次轮询后台填充（避免启动时主线程卡顿）
        self.capabilities: Dict = {}
        self._cap_refreshing = False

        self._queue: "queue.Queue" = queue.Queue()
        self._poller: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._busy = False
        self._closing = False
        self._root_jobs: set = set()
        self._drain_job = ""
        self._last_devices_hash = ""

        self.pages: Dict[str, Page] = {}
        self.current_key = "overview"
        self._arp_cache: Dict[str, str] = {}

        root.title(f"{APP_TITLE}  v{APP_VERSION}")
        root.configure(bg=self.palette.bg)
        self._set_icon()
        self.build_ui()
        self._restore_geometry()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<F5>", lambda _e: self.request_refresh(force=True))

        self.start_poller()
        self.after_root(400, self._drain)

    # ------------------------------------------------------------------ #
    #                                构建                                 #
    # ------------------------------------------------------------------ #
    def _set_icon(self) -> None:
        try:
            ico = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
            if ico.exists():
                self.root.iconbitmap(str(ico))
        except Exception:
            pass

    def build_ui(self) -> None:
        for w in list(self.root.winfo_children()):
            w.destroy()

        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        def make_page(key: str):
            cls = {
                "overview": OverviewPage, "hotspot": HotspotPage,
                "devices": DevicesPage, "portal": PortalPage, "settings": SettingsPage,
            }[key]
            return cls

        self.sidebar = Sidebar(
            self.root, self.palette,
            [(k, label, (lambda kk=k: self.show_page(kk))) for k, label in NAV_ITEMS],
            expanded=bool(self.cfg.sidebar_expanded),
        )
        self.sidebar.grid(row=0, column=0, sticky="nswe")

        right = ttk.Frame(self.root, style="TFrame")
        right.grid(row=0, column=1, sticky="nswe")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        self.topbar = ttk.Frame(right, style="TFrame", height=54)
        self.topbar.grid(row=0, column=0, sticky="ew")
        self.topbar.grid_propagate(False)
        tk.Button(
            self.topbar, text="☰", command=self.toggle_sidebar,
            bg=self.palette.surface, fg=self.palette.text, bd=0,
            font=("Segoe UI Symbol", 13), activebackground=self.palette.hover,
            activeforeground=self.palette.text, padx=14, pady=6, cursor="hand2",
        ).pack(side="left", padx=(8, 4), pady=8)
        self.page_title = ttk.Label(self.topbar, text="概览", style="Title.TLabel")
        self.page_title.pack(side="left", padx=(6, 0))
        self.top_status = ttk.Label(self.topbar, text="", foreground=self.palette.muted,
                                    background=self.palette.bg,
                                    font=("Microsoft YaHei UI", 9))
        self.top_status.pack(side="right", padx=16)

        self.content = ttk.Frame(right, style="TFrame")
        self.content.grid(row=1, column=0, sticky="nswe")
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        self.statusbar = ttk.Frame(right, style="Surface.TFrame", height=30)
        self.statusbar.grid(row=2, column=0, sticky="ew")
        self.statusbar.grid_propagate(False)
        self.status_labels: Dict[str, ttk.Label] = {}
        for name in ("state", "clients", "rate", "backend", "portal", "admin"):
            lab = ttk.Label(self.statusbar, text="", style="Muted.TLabel")
            lab.pack(side="left", padx=(14, 0))
            self.status_labels[name] = lab

        for key, _label in NAV_ITEMS:
            page = make_page(key)(self.content, self)
            page.grid(row=0, column=0, sticky="nswe")
            page.grid_remove()
            self.pages[key] = page

        self.show_page(self.current_key, rebuild=True)

    def _restore_geometry(self) -> None:
        geo = self.cfg.window_geometry
        if geo and geo.startswith("1") and "x" in geo:
            try:
                self.root.geometry(geo)
                return
            except tk.TclError:
                pass
        self.root.geometry("1120x720")
        self.root.minsize(900, 600)

    # ------------------------------------------------------------------ #
    #                                导航                                 #
    # ------------------------------------------------------------------ #
    def show_page(self, key: str, rebuild: bool = False) -> None:
        if key not in self.pages:
            return
        prev = self.pages.get(self.current_key)
        if prev is not None and prev is not self.pages.get(key):
            prev.on_hide()
            prev.grid_remove()
        page = self.pages[key]
        self.current_key = key
        page.grid()
        page.lift()
        label = dict(NAV_ITEMS).get(key, key)
        self.page_title.configure(text=label)
        self.sidebar.select(key, fire=False)
        page.on_show()

    def toggle_sidebar(self) -> None:
        self.sidebar.toggle()
        self.cfg.sidebar_expanded = self.sidebar.expanded
        self.save_config()

    def current_page(self) -> Optional[Page]:
        return self.pages.get(self.current_key)

    # ------------------------------------------------------------------ #
    #                              轮询刷新                               #
    # ------------------------------------------------------------------ #
    def start_poller(self) -> None:
        if self._poller and self._poller.is_alive():
            return
        self._stop.clear()
        self._poller = threading.Thread(target=self._poll_loop, name="ui-poller", daemon=True)
        self._poller.start()
        threading.Thread(target=self._detect_caps, name="caps-probe", daemon=True).start()

    def _detect_caps(self) -> None:
        try:
            caps = self.controller.capabilities(refresh=True)
            self._queue.put(("caps", caps))
        except Exception:
            log.exception("能力探测失败")

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._gather_and_queue()
            self._stop.wait(max(1.0, float(self.cfg.poll_interval or 4)))

    def _gather_and_queue(self) -> None:
        """在后台线程完成所有网络/子进程采集：热点状态 + 客户端 + 网关 +
        ARP 表 + 设备合并。主线程只负责渲染，至此不再有任何阻塞调用。"""
        try:
            status, raw = self.controller.snapshot()
            if self.cfg.demo_mode:
                have = {normalize(c.mac) for c in raw}
                for mac, ip, _host in DEMO_CLIENTS:
                    if mac not in have:
                        raw.append(RawClient(mac=mac, ip=ip, source="demo"))
            gateway = netinfo.hotspot_gateway_ip()
            prefix = netinfo.subnet_prefix_of(gateway)
            arp_cache = {ip: mac for ip, mac in netinfo.arp_entries(prefix)}
            # 设备合并（含 ARP 补充、SQLite 读取、store 写入）全部在后台线程
            clients = self.devman.sync(
                raw, active=status.active, gateway_ip=gateway,
                arp_cache=arp_cache, resolve_names=False,
            )
            self._queue.put(("status", (status, clients, gateway, arp_cache)))
        except Exception:
            log.exception("采集异常")

    def _drain(self) -> None:
        if self._closing or not self.root.winfo_exists():
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "status":
                    self._on_status(*payload)
                elif kind == "caps":
                    self.capabilities = payload or {}
                    page = self.current_page()
                    if page and hasattr(page, "on_caps"):
                        page.on_caps(self.capabilities)
                    self._update_statusbar()
                elif kind == "toast":
                    self.toast.show(payload[0], payload[1])
                elif kind == "call":
                    delay, fn, fargs = payload
                    self.after_root(delay, lambda: self._safe_call(fn, fargs))
        except queue.Empty:
            pass
        self._update_statusbar()
        page = self.current_page()
        if page is not None:
            try:
                page.refresh()
            except Exception:
                log.exception("页面刷新失败 %s", self.current_key)
        self.after_root(700, self._drain)

    @staticmethod
    def _safe_call(fn, args) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("主线程回调执行失败")

    def _on_status(self, status: HotspotStatus, clients: List,
                   gateway: str, arp_cache: Dict[str, str]) -> None:
        # 纯赋值：所有网络/子进程采集已在后台线程完成，此处绝不阻塞。
        self.status = status
        self.clients = clients
        self.gateway_ip = gateway
        self._arp_cache = arp_cache

    def request_refresh(self, force: bool = False) -> None:
        if force:
            threading.Thread(target=self._gather_and_queue, daemon=True).start()

    def _poll_once(self) -> None:
        self._gather_and_queue()

    def notify(self, message: str, kind: str = "info") -> None:
        self._queue.put(("toast", (message, kind)))

    def after_main(self, ms: int, fn, *args) -> None:
        """从工作线程安全地回到主线程执行（tkinter 不允许跨线程直接调用）。"""
        self._queue.put(("call", (int(ms), fn, args)))

    def after_root(self, ms: int, fn, *args) -> str:
        """主线程定时器：跟踪句柄，窗口关闭时统一取消，避免 Tcl 报错。"""

        def cb() -> None:
            self._root_jobs.discard(jid)
            if self._closing or not self.root.winfo_exists():
                return
            try:
                fn(*args)
            except Exception:
                log.exception("主线程定时器回调失败")

        jid = self.root.after(ms, cb)
        self._root_jobs.add(jid)
        return jid

    # ------------------------------------------------------------------ #
    #                              状态栏                                 #
    # ------------------------------------------------------------------ #
    def _update_statusbar(self) -> None:
        st = self.status
        online = sum(1 for d in self.clients if getattr(d, "online", False))
        down, up = self.traffic.total_rate()
        dot = {"on": "●", "off": "○", "transition": "◐"}.get(st.state, "?")
        color = {
            "on": self.palette.success, "off": self.palette.dim,
            "transition": self.palette.warning,
        }.get(st.state, self.palette.warning)
        self.status_labels["state"].configure(text=f"{dot} {st.state_text}", foreground=color)
        self.status_labels["clients"].configure(text=f"在线 {online}")
        self.status_labels["rate"].configure(
            text=f"↓ {human_rate(down)}  ↑ {human_rate(up)}")
        self.status_labels["backend"].configure(
            text=f"后端：{self.controller.backend_label}")
        pstate = "欢迎页 运行中" if self.portal.running else "欢迎页 未启动"
        self.status_labels["portal"].configure(
            text=pstate,
            foreground=self.palette.success if self.portal.running else self.palette.dim)
        adm = self.status_labels["admin"]
        if pshell.is_admin():
            adm.configure(text="管理员 ✓", foreground=self.palette.success)
        else:
            adm.configure(text="非管理员（部分功能受限）", foreground=self.palette.warning)

        title_state = f"{st.state_text}"
        if st.ssid:
            title_state += f" · {st.ssid}"
        if self.capabilities:
            title_state += f" · {self.capabilities.get('radios', '')}"
        self.top_status.configure(text=title_state)

    # ------------------------------------------------------------------ #
    #                              配置                                   #
    # ------------------------------------------------------------------ #
    def save_config(self) -> None:
        self.cfg.window_geometry = self.root.geometry()
        self.cfg.save()

    def apply_theme(self, dark: bool) -> None:
        self.cfg.theme = "dark" if dark else "light"
        self.save_config()
        p = self.palette
        p.__init__(dark=dark)  # 原地切换配色
        self.style = apply_ttk_style(self.root, p)
        self.toast = Toast(self.root, p)
        self.pages.clear()
        self.build_ui()
        self.notify(f"已切换到{'深色' if dark else '浅色'}主题", "success")

    # ------------------------------------------------------------------ #
    #                              其它                                   #
    # ------------------------------------------------------------------ #
    def portal_context(self) -> PortalContext:
        return PortalContext(
            get_ssid=lambda: self.status.ssid or self.cfg.hotspot.ssid,
            get_gateway=lambda: self.gateway_ip or "192.168.137.1",
            get_clients=lambda: sum(1 for d in self.clients if getattr(d, "online", False)),
            mac_of_ip=lambda ip: self._arp_cache.get(ip, ""),
        )

    def open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception as exc:
            self.notify(f"打开浏览器失败：{exc}", "error")

    def on_close(self) -> None:
        self._closing = True
        # 先取消所有待触发的定时器，避免窗口销毁后 Tcl 回调报错
        for jid in list(self._root_jobs):
            try:
                self.root.after_cancel(jid)
            except Exception:
                pass
        self._root_jobs.clear()
        try:
            self.toast.cancel()
        except Exception:
            pass
        self.cfg.window_geometry = self.root.geometry()
        self.cfg.save()
        self._stop.set()
        try:
            self.store.save(force=True)
        except Exception:
            pass
        try:
            self.portal.stop()
        except Exception:
            pass
        try:
            self.traffic.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def normalize(mac: str) -> str:
    from ..core.storage import normalize_mac

    return normalize_mac(mac)
