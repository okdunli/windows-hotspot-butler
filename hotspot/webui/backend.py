"""新界面的后端：负责采集状态、执行操作，并把结果整理成前端能直接渲染的字典。

设计原则（界面不卡的关键）：
  * 所有子进程 / 网络采集都在后台线程或线程池里跑；
  * 暴露给 JS 的方法只做两件事：①丢任务进线程池 ②读缓存；
  * 前端每 1.2 秒拉一次 get_state()，读的是内存缓存，耗时 < 1ms。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..core import netinfo, pshell
from ..core.captive import CaptivePortal
from ..core.config import AppConfig, PortalConfig
from ..core.deviceman import DeviceManager
from ..core.hotspot import HotspotController, RawClient
from ..core.paths import DATA_DIR, SHARE_DIR, ensure_dirs
from ..core.portal import PortalContext
from ..core.portforward import PortForwarder
from ..core.share import FileShare
from ..core.storage import DeviceStore, TrafficDB, human_bytes, human_rate, normalize_mac
from ..core.traffic import TrafficMonitor

log = logging.getLogger(__name__)

TYPE_EMOJI = {
    "phone": "📱", "tablet": "📲", "laptop": "💻", "desktop": "🖥️",
    "tv": "📺", "console": "🎮", "watch": "⌚", "speaker": "🔊",
    "camera": "📷", "printer": "🖨️", "router": "📡", "nas": "🗄️",
    "iot": "💡", "car": "🚗", "unknown": "📶",
}
TYPE_LABEL = {
    "phone": "手机", "tablet": "平板", "laptop": "笔记本", "desktop": "台式机",
    "tv": "电视 / 盒子", "console": "游戏机", "watch": "手表 / 手环",
    "speaker": "音箱", "camera": "摄像头", "printer": "打印机",
    "router": "路由 / AP", "nas": "NAS / 服务器", "iot": "智能家居",
    "car": "车机", "unknown": "未知设备",
}
ICON_CHOICES = [(k, f"{e} {TYPE_LABEL[k]}") for k, e in TYPE_EMOJI.items()]

BLOCK_FILE: Path = DATA_DIR / "blocked.json"
# 网关查询结果很慢（PowerShell 约 4 秒），落地缓存，下次启动瞬间可用
GW_FILE: Path = DATA_DIR / "gateway.txt"
# 快采集：ARP + 流量（几十毫秒），负责设备列表实时更新
_POLL_FAST = 1.5
# 慢采集：WinRT 状态 + 网关（PowerShell，8~15 秒），只定期跑，绝不阻塞快采集
_POLL_SLOW = 8.0
_GW_TTL = 300.0


def _ago(ts: float) -> str:
    if not ts:
        return ""
    d = max(0, int(time.time() - ts))
    if d < 60:
        return f"{d} 秒前"
    if d < 3600:
        return f"{d // 60} 分钟前"
    if d < 86400:
        return f"{d // 3600} 小时前"
    return f"{d // 86400} 天前"


def _uptime(ts: float) -> str:
    if not ts:
        return ""
    d = max(0, int(time.time() - ts))
    h, m, s = d // 3600, (d % 3600) // 60, d % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class HotspotBackend:
    """暴露给前端的 API（pywebview js_api）。"""

    def __init__(self) -> None:
        self.cfg = AppConfig.load()
        self.store = DeviceStore()
        self.db = TrafficDB()
        self.traffic = TrafficMonitor(
            self.db,
            traffic_backend=self.cfg.traffic_backend,
            demo_mode=self.cfg.demo_mode,
            iface_provider=netinfo.find_hotspot_adapter,
        )
        self.devman = DeviceManager(self.store, self.db, self.traffic)
        self.controller = HotspotController(self.cfg.hotspot)
        self.gateway = self._load_gateway() or "192.168.137.1"
        self._arp: Dict[str, str] = {}
        # 慢采集的缓存：系统状态 / 原始客户端 / 网关缓存时间戳
        self._status = None
        self._raw: List[RawClient] = []
        self._slow_running = False
        self._last_slow = 0.0
        self._gw_ts = 0.0

        self.ctx = PortalContext(
            get_ssid=lambda: self.controller.last_status.ssid or self.cfg.hotspot.ssid,
            get_gateway=lambda: self.gateway,
            get_clients=lambda: sum(1 for d in self.devman.all() if d.online),
            mac_of_ip=lambda ip: self._arp.get(ip, ""),
        )
        self.captive = CaptivePortal(self.ctx, on_accept=self._on_portal_accept,
                                     on_dns_query=self._on_dns_query)
        self.share = FileShare(SHARE_DIR, port=8081,
                               title_provider=lambda: self.cfg.hotspot.ssid,
                               gateway_provider=lambda: self.gateway)
        self.portfwd = PortForwarder()

        self._lock = threading.RLock()
        self._busy: Dict[str, float] = {}
        self._toasts: List[Dict[str, str]] = []
        self._snap: Dict[str, Any] = {}
        self._caps: Dict[str, Any] = {}
        self._diag: List[str] = []
        self._ready = False
        self.started_at = 0.0
        self._window = None
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="whm")
        self._blocked: Dict[str, str] = self._load_blocked()
        self._prev_online: set = set()
        self._tray = None      # TrayIcon 实例，由 app.py 注入
        self._stop_timer: Optional[threading.Timer] = None
        self._stop_deadline = 0.0
        self._temp_timer: Optional[threading.Timer] = None
        self._temp_deadline = 0.0
        self._exit_confirmed = False
        self._open_mini = None
        self._close_mini = None
        # 标记门户是否由"热点随启随停"逻辑托管；用户手动启停后改为 False，避免被自动逻辑覆盖
        self._portal_auto = False

        self.traffic.start()
        threading.Thread(target=self._loop, name="whm-poll", daemon=True).start()
        threading.Thread(target=self._detect_caps, name="whm-caps", daemon=True).start()

        # 按配置自动开启热点（与传统界面行为一致）
        if self.cfg.hotspot.auto_start and not self.controller.last_status.active:
            threading.Thread(target=lambda: self.controller.start(),
                             name="whm-autostart", daemon=True).start()

    # ------------------------------------------------------------------ #
    #                              窗口钩子                                #
    # ------------------------------------------------------------------ #
    def attach_window(self, window) -> None:
        self._window = window

    def shutdown(self) -> None:
        self._stop.set()
        try:
            self.captive.stop()
        except Exception:
            pass
        try:
            self.share.stop()
        except Exception:
            pass
        try:
            self.traffic.stop()
        except Exception:
            pass
        try:
            self.store.save(force=True)
        except Exception:
            pass
        try:
            self.db.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #                              toast                                  #
    # ------------------------------------------------------------------ #
    def _toast(self, text: str, kind: str = "info") -> None:
        with self._lock:
            self._toasts.append({"text": text, "kind": kind, "ts": time.time()})

    def _tray_notify(self, text: str) -> None:
        """托盘气泡（窗口隐藏时也能看到）。"""
        if self._tray is not None:
            try:
                self._tray.notify(text)
            except Exception:
                log.debug("托盘通知失败", exc_info=True)

    def _on_portal_accept(self, ip: str, mac: str, ua: str) -> None:
        maxn = int(self.cfg.hotspot.max_clients or 0)
        if maxn:
            online = sum(1 for d in self.devman.all() if d.online)
            if online > maxn:
                try:
                    self.captive.revoke_ip(ip, mac)
                except Exception:
                    pass
                self._toast(f"已拒绝 {ip}：设备数超过上限 {maxn} 台", "error")
                return
        self._toast(f"{ip} 已通过欢迎页并开始上网", "success")

    def _on_dns_query(self, ip: str, domain: str) -> None:
        """DNS 劫持路径上的域名记录（URL 访问日志，竞品的 URL Logging 功能）。"""
        if not domain or domain.endswith((".arpa", ".lan", ".local", ".localdomain")):
            return
        try:
            self.db.record_dns_query(ip, self._arp.get(ip, ""), domain)
        except Exception:
            log.debug("DNS 记录失败", exc_info=True)

    @staticmethod
    def _load_gateway() -> str:
        try:
            txt = GW_FILE.read_text(encoding="utf-8").strip()
            if txt.count(".") == 3:
                return txt
        except OSError:
            pass
        return ""

    @staticmethod
    def _save_gateway(ip: str) -> None:
        try:
            ensure_dirs()
            GW_FILE.write_text(ip, encoding="utf-8")
        except OSError:
            log.debug("网关缓存写入失败")

    # ------------------------------------------------------------------ #
    #                            采集循环                                  #
    # ------------------------------------------------------------------ #
    def _detect_caps(self) -> None:
        try:
            self._caps = self.controller.capabilities(refresh=True)
        except Exception:
            log.exception("能力探测失败")
        finally:
            with self._lock:
                self._ready = True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._gather_fast()
            except Exception:
                log.exception("快采集异常")
            if not self._slow_running and time.time() - self._last_slow >= _POLL_SLOW:
                threading.Thread(target=self._gather_slow, daemon=True).start()
            self._stop.wait(_POLL_FAST)

    def _gather_slow(self, force: bool = False) -> None:
        """慢采集：系统热点状态 + 客户端列表 + 网关（PowerShell，耗时数秒）。

        单独线程执行，快采集（ARP）不受它阻塞，界面始终秒级刷新。
        """
        if self._slow_running:
            return
        self._slow_running = True
        try:
            status, raw = self.controller.snapshot()
            self._status = status
            self._raw = list(raw or [])
            if status.active and not self.started_at:
                self.started_at = time.time()
            if not status.active:
                self.started_at = 0.0
            # 网关查询同样要 4 秒左右，做缓存：首次 / 强制 / 过期才查
            if force or not self._gw_ts or (time.time() - self._gw_ts > _GW_TTL):
                gw = netinfo.hotspot_gateway_ip()
                if gw:
                    if gw != self.gateway:
                        self.gateway = gw
                        self._save_gateway(gw)
                    self._gw_ts = time.time()
        except Exception:
            log.exception("慢采集异常")
        finally:
            self._last_slow = time.time()
            self._slow_running = False
        # 热点状态已刷新，顺势同步强制门户（开启时拉起、关闭时收起）
        try:
            self._sync_portal(status.active)
        except Exception:
            log.exception("门户同步异常")
        self._update_tray_tooltip(status)
        self._gather_fast()

    def _update_tray_tooltip(self, status) -> None:
        if self._tray is None:
            return
        try:
            if status.active:
                online = sum(1 for d in self.devman.all() if d.online)
                self._tray.set_tooltip(f"{status.ssid or '热点'} · 运行中 · 在线 {online} 台")
            else:
                self._tray.set_tooltip("WiFi 热点管理器 · 热点未开启")
        except Exception:
            log.debug("托盘提示刷新失败", exc_info=True)

    def _gather_fast(self) -> None:
        """快采集：ARP + 流量统计，几十毫秒，负责设备列表与速率实时更新。"""
        prefix = netinfo.subnet_prefix_of(self.gateway)
        arp = {ip: mac for ip, mac in netinfo.arp_entries(prefix)}
        self._arp = arp
        status = self._status or self.controller.last_status
        # 首次还没拿到系统状态时乐观处理，避免开局空列表
        active = bool(status.active) if self._status is not None else True
        clients = self.devman.sync(
            self._raw, active=active, gateway_ip=self.gateway,
            arp_cache=arp, resolve_names=False,
        )
        if active and not self.started_at:
            self.started_at = time.time()
        down, up = self.traffic.total_rate()

        devices = []
        for d in clients:
            devices.append({
                "mac": d.mac,
                "ip": d.ip,
                "name": d.name,
                "hostname": d.hostname,
                "vendor": d.vendor,
                "type": d.dev_type,
                "emoji": TYPE_EMOJI.get(d.dev_type, "📶"),
                "type_label": TYPE_LABEL.get(d.dev_type, "未知设备"),
                "online": d.online,
                "blocked": d.mac in self._blocked,
                "allowed": self.captive.allow.has(ip=d.ip, mac=d.mac),
                "rate_down": d.rate_down,
                "rate_up": d.rate_up,
                "rate_down_text": human_rate(d.rate_down),
                "rate_up_text": human_rate(d.rate_up),
                "session_rx": d.session_rx,
                "session_tx": d.session_tx,
                "total_rx": d.total_rx,
                "total_tx": d.total_tx,
                "total_text": human_bytes(d.total_rx + d.total_tx),
                "last_seen_text": _ago(d.last_seen) if not d.online else "在线",
                "portal_accepted": d.portal_accepted,
            })

        online = sum(1 for d in clients if d.online)
        # 新设备接入提醒：对比上一轮的在线 MAC 集合
        now_online = {d.mac for d in clients if d.online}
        with self._lock:
            prev_online = self._prev_online
            self._prev_online = now_online
        for mac in now_online - prev_online:
            d = next((c for c in clients if c.mac == mac), None)
            if d is not None and not d.portal_accepted:
                text = f"新设备接入：{d.name}（{d.ip or d.mac}）"
                self._toast(text, "info")
                self._tray_notify(text)
        with self._lock:
            self._snap = {
                "ts": time.time(),
                "devices": devices,
            "stats": {
                "online": online,
                "total": len(clients),
                "down": down,
                "up": up,
                "down_text": human_rate(down),
                "up_text": human_rate(up),
            },
        }

    def _sync_portal(self, active: bool) -> None:
        """随热点状态同步强制门户：开启且配置了门户就拉起（DNS 劫持），关闭就收起。

        这样连上热点后会真正弹出门户页（DNS 劫持把未同意设备的解析指向网关），
        而不是"连上就能直接上网、欢迎页形同虚设"。用户手动启停门户时由 portal_start/
        portal_stop 把 _portal_auto 置 False，这里就不再自动接管。
        """
        want = bool(self.cfg.portal.enabled) and bool(active)
        if want and not self.captive.running:
            try:
                self.captive.ctx = self.ctx
                ok, msg = self.captive.start(self.cfg.portal, force_dns=True)
                if ok:
                    self._portal_auto = True
                    self._toast("强制门户已随热点启动（DNS 劫持生效）", "info")
                else:
                    self._toast("门户启动失败：" + msg, "error")
            except Exception as exc:
                log.exception("门户自动启动失败")
                self._toast(f"门户启动失败：{exc}", "error")
        elif not want and self.captive.running and self._portal_auto:
            try:
                self.captive.stop()
                self._portal_auto = False
                self._toast("热点已关闭，强制门户已停止", "info")
            except Exception:
                log.debug("门户自动停止异常", exc_info=True)

    # ------------------------------------------------------------------ #
    #                           对外：读状态                               #
    # ------------------------------------------------------------------ #
    def get_state(self) -> Dict[str, Any]:
        st = self.controller.last_status
        with self._lock:
            snap = dict(self._snap)
            toasts = list(self._toasts)
            self._toasts.clear()
            busy = dict(self._busy)
            caps = dict(self._caps)

        caps = caps or {}
        can_host = bool(caps.get("can_host_hotspot", True))
        block_reason = ""
        if not can_host:
            block_reason = str(caps.get("host_block_reason") or "")

        return {
            "ready": bool(self._ready and snap),
            "admin": pshell.is_admin(),
            "busy": bool(busy),
            "toasts": toasts,
            "hotspot": {
                "active": bool(st.active),
                "state": st.state,
                "state_text": st.state_text,
                "ssid": st.ssid or self.cfg.hotspot.ssid,
                "passphrase": st.passphrase or self.cfg.hotspot.passphrase,
                "band": st.band,
                "backend": st.backend,
                "backend_label": self.controller.backend_label,
                "client_count": st.client_count,
                "max_clients": st.max_clients,
                "message": st.message,
                "started_at": self.started_at,
                "uptime": _uptime(self.started_at),
            },
            "caps": {
                "can_host": can_host,
                "block_reason": block_reason,
                "driver": caps.get("driver", ""),
                "radios": caps.get("radios", ""),
                "wifi_direct": caps.get("wifi_direct_supported"),
                "soft_ap": caps.get("soft_ap_supported"),
                "hosted": caps.get("hosted_supported"),
                "band_5": caps.get("band_5_supported"),
                "notes": caps.get("notes", []),
            },
            "devices": snap.get("devices", []),
            "stats": snap.get("stats", {"online": 0, "total": 0, "down": 0, "up": 0,
                                        "down_text": "0 B/s", "up_text": "0 B/s"}),
            "config": {
                "ssid": self.cfg.hotspot.ssid,
                "passphrase": self.cfg.hotspot.passphrase,
                "security": self.cfg.hotspot.security,
                "band": self.cfg.hotspot.band,
                "backend": self.cfg.hotspot.backend,
                "auto_start": self.cfg.hotspot.auto_start,
                "max_clients": self.cfg.hotspot.max_clients,
                "start_with_windows": self.cfg.start_with_windows,
                "close_to_tray": self.cfg.close_to_tray,
                "portal_enabled": self.cfg.portal.enabled,
                "portal_dns": self.cfg.portal.dns_redirect,
                "portal_template": self.cfg.portal.template,
                "portal_title": self.cfg.portal.title,
                "portal_notice": self.cfg.portal.notice,
                "portal_button": self.cfg.portal.button,
                "portal_password": self.cfg.portal.access_password,
                "portal_schedule_start": self.cfg.portal.schedule_start,
                "portal_schedule_end": self.cfg.portal.schedule_end,
                "confirm_exit_hotspot": self.cfg.confirm_exit_hotspot,
            },
            "portal": self.captive.status(),
            "temp_password": self.temp_password_status(),
            "share": self.share.status(),
            "port_fwd": self.portfwd.list_rules(),
            "gateway": self.gateway,
            "auto_stop": self.stop_status(),
            "icons": [{"key": k, "label": v} for k, v in ICON_CHOICES],
        }

    # ------------------------------------------------------------------ #
    #                           对外：操作                                 #
    # ------------------------------------------------------------------ #
    def _run(self, key: str, fn: Callable[[], Any]) -> Dict[str, Any]:
        """把耗时操作丢进线程池，立即返回，前端通过 busy / toast 感知进度。"""
        with self._lock:
            if key in self._busy:
                return {"ok": False, "started": False, "msg": "操作进行中，请稍候"}

        def wrapper() -> None:
            try:
                ok, msg = fn()
                if key in ("start", "stop") and ok:
                    # 开关成功立即反映到界面，不等下一次慢采集（PowerShell 要 4~8 秒）
                    self._optimistic_status(key == "start")
                self._toast(str(msg), "success" if ok else "error")
            except Exception as exc:
                log.exception("操作失败 %s", key)
                self._toast(f"操作失败：{exc}", "error")
            finally:
                with self._lock:
                    self._busy.pop(key, None)
                self._pool.submit(self._gather_slow, True)

        with self._lock:
            self._busy[key] = time.time()
        self._pool.submit(wrapper)
        return {"ok": True, "started": True}

    def _optimistic_status(self, active: bool) -> None:
        st = self.controller.last_status
        self.controller.last_status = replace(st, active=active,
                                              state="on" if active else "off")
        if active:
            self.controller.ever_active = True
            if self.controller.wlan_caps is not None:
                self.controller.wlan_caps.ever_active = True
                self.controller.wlan_caps.active_now = True
        self._status = self.controller.last_status
        if active and not self.started_at:
            self.started_at = time.time()
        if not active:
            self.started_at = 0.0
            with self._lock:
                if self._stop_timer is not None:
                    self._stop_timer.cancel()
                    self._stop_timer = None
                self._stop_deadline = 0.0
        try:
            self._sync_portal(active)
        except Exception:
            log.debug("乐观门户同步异常", exc_info=True)
        # 乐观状态直接刷托盘 tooltip（慢采集要 4~8 秒后才来，期间 tooltip 仍是旧状态）
        try:
            self._update_tray_tooltip(self.controller.last_status)
        except Exception:
            log.debug("乐观托盘刷新异常", exc_info=True)

    def start(self) -> Dict[str, Any]:
        return self._run("start", self.controller.start)

    def stop(self) -> Dict[str, Any]:
        return self._run("stop", self.controller.stop)

    def toggle(self) -> Dict[str, Any]:
        return self.stop() if self.controller.last_status.active else self.start()

    def refresh(self) -> Dict[str, Any]:
        """手动刷新：慢采集（系统状态）丢后台，快采集立刻跑一次，界面马上动。"""
        self._pool.submit(self._gather_slow, True)
        self._pool.submit(self._gather_fast)
        return {"ok": True, "started": True}

    def save_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        try:
            h = self.cfg.hotspot
            for key in ("ssid", "passphrase", "security", "band", "backend"):
                if key in patch:
                    setattr(h, key, str(patch[key]))
            if "auto_start" in patch:
                h.auto_start = bool(patch["auto_start"])
            if "max_clients" in patch:
                h.max_clients = int(patch["max_clients"] or 8)
            if "start_with_windows" in patch:
                self.cfg.start_with_windows = bool(patch["start_with_windows"])
                self._apply_start_with_windows(bool(patch["start_with_windows"]))
            if "close_to_tray" in patch:
                self.cfg.close_to_tray = bool(patch["close_to_tray"])
            if "confirm_exit_hotspot" in patch:
                self.cfg.confirm_exit_hotspot = bool(patch["confirm_exit_hotspot"])
            p = self.cfg.portal
            # 前端发的是 portal_* 前缀键，映射到 PortalConfig 字段
            portal_map = {
                "portal_enabled": ("enabled", bool),
                "portal_dns": ("dns_redirect", bool),
                "portal_template": ("template", str),
                "portal_title": ("title", str),
                "portal_notice": ("notice", str),
                "portal_button": ("button", str),
                "portal_password": ("access_password", str),
            }
            for key, (attr, typ) in portal_map.items():
                if key in patch:
                    setattr(p, attr, typ(patch[key]))
            if "portal_schedule_start" in patch:
                p.schedule_start = max(0, min(23, int(patch["portal_schedule_start"] or 0)))
            if "portal_schedule_end" in patch:
                p.schedule_end = max(1, min(24, int(patch["portal_schedule_end"] or 24)))
            self.cfg.normalize()
            self.cfg.save()
            self.controller.cfg = self.cfg.hotspot
            self._toast("设置已保存", "success")
            # 配置变更需要下发到系统
            self._pool.submit(self._apply_config)
            return {"ok": True, "msg": "已保存"}
        except Exception as exc:
            log.exception("保存配置失败")
            return {"ok": False, "msg": str(exc)}

    def _apply_config(self) -> None:
        try:
            self.controller.apply_config(self.cfg.hotspot)
        except Exception:
            log.debug("配置下发失败", exc_info=True)

    def apply_now(self) -> Dict[str, Any]:
        return self._run("apply", lambda: self.controller.apply_config(self.cfg.hotspot))

    def _apply_start_with_windows(self, enabled: bool) -> None:
        """开机自启：写/删 HKCU\\...\\Run 注册表项。"""
        import subprocess
        import sys
        exe = sys.executable
        script = str(Path(__file__).resolve().parent.parent.parent / "main.py")
        value = f'"{exe}" "{script}"'
        args = ["reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                "/v", "WifiHotspotManager", "/t", "REG_SZ", "/d", value, "/f"] \
            if enabled else \
            ["reg", "delete", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
             "/v", "WifiHotspotManager", "/f"]
        try:
            subprocess.run(args, timeout=15, creationflags=0x08000000)
        except Exception:
            log.debug("设置开机自启失败", exc_info=True)

    # ------------------------------ 设备 ------------------------------ #
    def set_device_name(self, mac: str, name: str) -> Dict[str, Any]:
        try:
            self.devman.rename(mac, name)
            self._pool.submit(self._gather_fast)
            return {"ok": True, "msg": "已重命名"}
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}

    def set_device_type(self, mac: str, dtype: str) -> Dict[str, Any]:
        try:
            self.devman.set_type(mac, dtype, lock=True)
            self._pool.submit(self._gather_fast)
            return {"ok": True, "msg": "已更新设备类型"}
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}

    def forget_device(self, mac: str) -> Dict[str, Any]:
        try:
            self.devman.forget(mac)
            self._pool.submit(self._gather_fast)
            return {"ok": True, "msg": "已移除设备"}
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}

    # --------------------------- 禁止上网 ----------------------------- #
    @staticmethod
    def _rule_name(mac: str, direction: str) -> str:
        return f"WHM-BLOCK-{normalize_mac(mac).replace(':', '')}-{direction}"

    def _load_blocked(self) -> Dict[str, str]:
        try:
            if BLOCK_FILE.exists():
                return json.loads(BLOCK_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.debug("黑名单读取失败")
        return {}

    def _save_blocked(self) -> None:
        try:
            ensure_dirs()
            BLOCK_FILE.write_text(
                json.dumps(self._blocked, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.warning("黑名单保存失败")

    def block_device(self, mac: str) -> Dict[str, Any]:
        mac = normalize_mac(mac)
        dev = self.devman.get(mac)
        ip = dev.ip if dev else ""
        if not ip:
            return {"ok": False, "msg": "该设备当前不在线，拿不到 IP，无法禁用"}
        base = ["netsh", "advfirewall", "firewall", "add", "rule",
                "action=block", "enable=yes", f"remoteip={ip}"]
        ok_all = True
        msg = ""
        for direction in ("out", "in"):
            args = base + [f"name={self._rule_name(mac, direction.upper())}",
                           f"dir={direction}"]
            code, out, err = pshell.run(args, timeout=20)
            if code != 0:
                ok_all = False
                msg = (out or err).strip()[:200]
                break
        if ok_all:
            self._blocked[mac] = ip
            self._save_blocked()
            self._pool.submit(self._gather_fast)
            return {"ok": True, "msg": f"已禁止 {ip} 上网"}
        return {"ok": False, "msg": msg or "添加防火墙规则失败（需要管理员权限）"}

    def unblock_device(self, mac: str) -> Dict[str, Any]:
        mac = normalize_mac(mac)
        for direction in ("OUT", "IN"):
            pshell.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                        f"name={self._rule_name(mac, direction)}"], timeout=20)
        self._blocked.pop(mac, None)
        self._save_blocked()
        self._pool.submit(self._gather_fast)
        return {"ok": True, "msg": "已恢复上网"}

    # ---------------------------- 强制门户 ---------------------------- #
    def portal_start(self) -> Dict[str, Any]:
        def work() -> Any:
            self.captive.ctx = self.ctx
            self._portal_auto = False  # 用户手动控制，交还给手动逻辑
            return self.captive.start(self.cfg.portal, force_dns=True)
        return self._run("portal", work)

    def portal_stop(self) -> Dict[str, Any]:
        def work() -> Any:
            self._portal_auto = False  # 用户手动控制，交还给手动逻辑
            return self.captive.stop()
        return self._run("portal", work)

    def portal_allow(self, mac: str) -> Dict[str, Any]:
        dev = self.devman.get(normalize_mac(mac))
        if not dev or not dev.ip:
            return {"ok": False, "msg": "设备不在线，无法放行"}
        self.captive.allow_ip(dev.ip, dev.mac)
        self._pool.submit(self._gather_fast)
        return {"ok": True, "msg": f"已放行 {dev.ip}"}

    def portal_revoke(self, mac: str) -> Dict[str, Any]:
        dev = self.devman.get(normalize_mac(mac))
        if not dev:
            return {"ok": False, "msg": "未找到设备"}
        self.captive.revoke_ip(dev.ip, dev.mac)
        self._pool.submit(self._gather_fast)
        return {"ok": True, "msg": f"已取消放行 {dev.ip or dev.mac}"}

    # ------------------------------ 其它 ------------------------------ #
    def diagnose(self) -> Dict[str, Any]:
        def work() -> Any:
            try:
                self._diag = self.controller.diagnose()
            except Exception as exc:
                self._diag = [f"诊断失败：{exc}"]
            return True, "\n".join(self._diag)
        self._pool.submit(work)
        return {"ok": True, "started": True}

    def get_diagnose(self) -> Dict[str, Any]:
        return {"lines": list(self._diag)}

    # --------------------------- WiFi 二维码 --------------------------- #
    def wifi_qrcode(self) -> Dict[str, Any]:
        """生成 WiFi 扫码连接二维码（data:image/png;base64）。

        标准 WIFI: 格式（Android/iOS 11+ 相机均支持），特殊字符按规范转义。
        """
        try:
            import base64
            import io

            import qrcode

            def esc(s: str) -> str:
                for a, b in (("\\", "\\\\"), (";", "\\;"), (",", "\\,"),
                             (":", "\\:"), ('"', '\\"')):
                    s = s.replace(a, b)
                return s

            hs = self.controller.last_status
            ssid = hs.ssid or self.cfg.hotspot.ssid
            passphrase = "" if self.cfg.hotspot.security == "open" else (
                hs.passphrase or self.cfg.hotspot.passphrase)
            security = "nopass" if self.cfg.hotspot.security == "open" else "WPA"
            payload = f'WIFI:T:{security};S:{esc(ssid)};P:{esc(passphrase)};;'

            img = qrcode.make(payload, box_size=8, border=2)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            return {"ok": True, "data": data_uri, "ssid": ssid}
        except ImportError:
            return {"ok": False, "msg": "缺少 qrcode 库：pip install qrcode"}
        except Exception as exc:
            log.exception("二维码生成失败")
            return {"ok": False, "msg": str(exc)}

    # --------------------------- 定时关闭 ----------------------------- #
    def schedule_stop(self, minutes: int) -> Dict[str, Any]:
        """N 分钟后自动关闭热点；minutes<=0 取消。"""
        with self._lock:
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None
        if minutes <= 0:
            return {"ok": True, "msg": "已取消定时关闭"}
        remaining = {"sec": minutes * 60}

        def tick() -> None:
            # ponytail: 简单倒计时线程，GUI 重启后定时即失效（够用，不加持久化）
            with self._lock:
                cur = self._stop_timer if self._stop_timer else None
            remaining["sec"] -= 1
            if remaining["sec"] <= 0:
                self._toast("定时时间到，正在关闭热点…", "info")
                self.stop()
                with self._lock:
                    self._stop_deadline = 0.0
                return
            with self._lock:
                self._stop_deadline = time.time() + remaining["sec"]
            t = threading.Timer(1.0, tick)
            with self._lock:
                self._stop_timer = t
            t.daemon = True
            t.start()

        with self._lock:
            self._stop_deadline = time.time() + minutes * 60
        t = threading.Timer(1.0, tick)
        with self._lock:
            self._stop_timer = t
        t.daemon = True
        t.start()
        return {"ok": True, "msg": f"将在 {minutes} 分钟后自动关闭热点"}

    def stop_status(self) -> Dict[str, Any]:
        with self._lock:
            dl = self._stop_deadline
        if not dl or dl < time.time():
            return {"remaining": 0}
        return {"remaining": int(dl - time.time())}

    # --------------------------- 临时密码 ----------------------------- #
    def temp_password_start(self, hours: float) -> Dict[str, Any]:
        """生成临时密码并应用到热点，hours 小时后自动改回原密码。

        酒店/咖啡馆场景：给访客一个临时密码，到期自动恢复，无需手动改。
        """
        import secrets
        import string
        alphabet = string.ascii_lowercase + string.digits
        temp_pw = "".join(secrets.choice(alphabet) for _ in range(8))
        saved_pw = self.controller.last_status.passphrase or self.cfg.hotspot.passphrase
        with self._lock:
            if self._temp_timer is not None:
                self._temp_timer.cancel()
                self._temp_timer = None
        # 立即下发临时密码（走正常配置流程）
        self.cfg.hotspot.passphrase = temp_pw
        self.cfg.save()
        self.controller.cfg = self.cfg.hotspot
        self._pool.submit(self._apply_config)
        if self.controller.last_status.active:
            self._toast(f"临时密码已生效：{temp_pw}", "success")
        else:
            self._toast(f"临时密码已设置：{temp_pw}（热点开启后生效）", "info")
        self._tray_notify(f"临时密码：{temp_pw}")

        def restore() -> None:
            self.cfg.hotspot.passphrase = saved_pw
            self.cfg.temp_password = ""
            self.cfg.temp_password_until = 0.0
            self.cfg.save()
            self.controller.cfg = self.cfg.hotspot
            self._pool.submit(self._apply_config)
            self._toast("临时密码已到期，已恢复原密码", "info")
            self._tray_notify("临时密码已到期，热点密码已恢复")
            with self._lock:
                self._temp_timer = None
                self._temp_deadline = 0.0

        t = threading.Timer(hours * 3600, restore)
        t.daemon = True
        with self._lock:
            self._temp_timer = t
            self._temp_deadline = time.time() + hours * 3600
        t.start()
        self.cfg.temp_password = temp_pw
        self.cfg.temp_password_until = self._temp_deadline
        self.cfg.save()
        return {"ok": True, "msg": f"临时密码 {temp_pw}，{hours:g} 小时后自动恢复", "password": temp_pw}

    def temp_password_stop(self) -> Dict[str, Any]:
        with self._lock:
            if self._temp_timer is not None:
                self._temp_timer.cancel()
                self._temp_timer = None
            self._temp_deadline = 0.0
        if self.cfg.temp_password:
            self.cfg.temp_password = ""
            self.cfg.temp_password_until = 0.0
            self.cfg.save()
        return {"ok": True, "msg": "已取消临时密码（密码保持当前值不变）"}

    def temp_password_status(self) -> Dict[str, Any]:
        with self._lock:
            dl = self._temp_deadline
        active = bool(self.cfg.temp_password) and dl > time.time()
        return {"active": active, "password": self.cfg.temp_password if active else "",
                "remaining": int(dl - time.time()) if active else 0}

    # --------------------------- 配置导入导出 -------------------------- #
    def export_config(self) -> Dict[str, Any]:
        """导出全部配置为 JSON（不含密码明文选项可选，这里全量导出便于完整恢复）。"""
        try:
            ensure_dirs()
            export = {
                "app": "WifiHotspotManager",
                "version": 1,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "config": self.cfg.to_dict(),
                "devices": self.store.all(),
            }
            export_file = DATA_DIR / f"export-{time.strftime('%Y%m%d-%H%M%S')}.json"
            export_file.write_text(
                json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
            import os
            os.startfile(str(DATA_DIR))  # noqa: S606
            return {"ok": True, "msg": f"已导出到 {export_file}"}
        except Exception as exc:
            log.exception("配置导出失败")
            return {"ok": False, "msg": str(exc)}

    def import_config(self) -> Dict[str, Any]:
        """弹出文件选择框选择此前导出的 JSON，恢复配置。"""
        try:
            if not self._window:
                return {"ok": False, "msg": "窗口未就绪"}
            import webview
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("JSON 文件 (*.json)",),
            )
            if not result or not len(result):
                return {"ok": False, "msg": "未选择文件"}
            path = Path(result[0])
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("app") != "WifiHotspotManager" or not isinstance(data.get("config"), dict):
                return {"ok": False, "msg": "文件格式不正确（不是本程序导出的配置）"}
            from ..core.config import AppConfig
            self.cfg = AppConfig.from_dict(data["config"])
            self.cfg.save()
            self.controller.cfg = self.cfg.hotspot
            self._pool.submit(self._apply_config)
            self._toast("配置已导入并下发", "success")
            return {"ok": True, "msg": "配置已导入"}
        except Exception as exc:
            log.exception("配置导入失败")
            return {"ok": False, "msg": str(exc)}

    # --------------------------- 迷你悬浮窗 --------------------------- #
    def set_theme(self, theme: str) -> Dict[str, Any]:
        """主窗口切主题时同步到配置；浮窗 Form 底色跟随（色块与卡片融合）。"""
        if theme in ("dark", "light"):
            self.cfg.theme = theme
            self.cfg.save()
            styler = getattr(self, "_mini_style", None)
            if styler:
                try:
                    styler(self._mini_window, theme)
                except Exception:
                    log.debug("浮窗底色跟随主题失败", exc_info=True)
        return {"ok": True}

    def get_theme(self) -> str:
        return self.cfg.theme

    def mini_state(self) -> Dict[str, Any]:
        """迷你浮窗专用轻量状态（只读缓存，不触发任何采集）。"""
        with self._lock:
            snap = dict(self._snap)
        st = self.controller.last_status
        stats = snap.get("stats", {})
        return {"active": bool(st.active), "online": stats.get("online", 0),
                "down": stats.get("down", 0), "up": stats.get("up", 0),
                "theme": self.cfg.theme}

    def mini_toggle(self) -> Dict[str, Any]:
        return self.toggle()

    def mini_drag_start(self) -> Dict[str, Any]:
        """迷你浮窗开始原生拖拽（JS mousedown 调一次，Windows 接管移动）。"""
        app_ref = getattr(self, "_app_ref", None)
        if not app_ref:
            return {"ok": False}
        try:
            app_ref["mini_drag_start"]()
            return {"ok": True}
        except Exception:
            log.debug("浮窗拖拽失败", exc_info=True)
            return {"ok": False}

    def mini_restore_main(self) -> Dict[str, Any]:
        """迷你浮窗右键：显示主窗口、关闭浮窗。"""
        if self._window:
            try:
                self._window.show()
                self._window.restore()
            except Exception:
                pass
        self.close_mini()
        return {"ok": True}

    def open_mini(self) -> Dict[str, Any]:
        """打开迷你悬浮窗（由 app.py 挂载的 _open_mini 回调完成）。"""
        if hasattr(self, "_open_mini") and self._open_mini:
            self._open_mini()
            return {"ok": True}
        return {"ok": False, "msg": "悬浮窗不可用"}

    def close_mini(self) -> Dict[str, Any]:
        if hasattr(self, "_close_mini") and self._close_mini:
            self._close_mini()
        return {"ok": True}

    def confirm_exit(self) -> Dict[str, Any]:
        """用户已在确认弹窗点了"确认退出"，下次 close 请求放行。"""
        self._exit_confirmed = True
        return {"ok": True}

    def cancel_exit(self) -> Dict[str, Any]:
        self._exit_confirmed = False
        return {"ok": True}

    def minimize(self) -> Dict[str, Any]:
        if self._window:
            try:
                self._window.minimize()
            except Exception:
                pass
        return {"ok": True}

    def hide_main(self) -> Dict[str, Any]:
        """打开悬浮窗后藏起主窗口（不销毁：后端/托盘/采集线程全部保留）。
        恢复走 mini_restore_main() → show()。"""
        if self._window:
            try:
                self._window.hide()
            except Exception:
                pass
        return {"ok": True}

    def close(self) -> Dict[str, Any]:
        """标题栏 ✕：close_to_tray 开启时隐藏到托盘，否则真正关闭。"""
        try:
            if self.cfg.close_to_tray and self._window:
                self._window.hide()
                return {"ok": True, "hidden": True}
            if self._window:
                self._window.destroy()
        except Exception:
            pass
        return {"ok": True}

    # --------------------------- 统计报表 ----------------------------- #
    def get_stats_report(self) -> Dict[str, Any]:
        """流量统计 + 用量 TOP + 域名访问记录（竞品 Statistics / URL Logging）。"""
        try:
            daily = self.db.daily_all(14)
            names: Dict[str, str] = {}
            for rec in self.store.all():
                names[rec["mac"]] = self.store.display_name(rec) or rec["mac"]
            top = [{"name": names.get(t["mac"], t["mac"]), "mac": t["mac"],
                    "total_text": human_bytes(t["rx"] + t["tx"])}
                   for t in self.db.top_devices(8)]
            queries = self.db.dns_queries(80)
            for q in queries:
                q["time_text"] = time.strftime("%H:%M:%S", time.localtime(q["ts"]))
                q["name"] = names.get(q.get("mac") or "", q.get("ip") or "")
            domains = self.db.top_domains(10)
            grand = self.db.grand_total()
            return {
                "ok": True,
                "daily": [{"day": d, "rx": rx, "tx": tx,
                           "total_text": human_bytes(rx + tx)} for d, rx, tx in daily],
                "top": top,
                "queries": queries,
                "top_domains": domains,
                "grand": {"rx": human_bytes(grand[0]), "tx": human_bytes(grand[1]),
                          "total": human_bytes(grand[0] + grand[1])},
            }
        except Exception as exc:
            log.exception("统计报表失败")
            return {"ok": False, "msg": str(exc)}

    # --------------------------- 文件共享 ----------------------------- #
    def share_start(self) -> Dict[str, Any]:
        def work():
            return self.share.start()
        return self._run("share", work)

    def share_stop(self) -> Dict[str, Any]:
        def work():
            return self.share.stop()
        return self._run("share", work)

    def share_open_folder(self) -> Dict[str, Any]:
        try:
            SHARE_DIR.mkdir(parents=True, exist_ok=True)
            import os
            os.startfile(str(SHARE_DIR))  # noqa: S606
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}

    # --------------------------- 端口转发 ----------------------------- #
    def pf_add(self, name: str, listen_port: int, connect_ip: str,
               connect_port: int, proto: str = "tcp") -> Dict[str, Any]:
        ok, msg = self.portfwd.add(name, listen_port, connect_ip, connect_port, proto)
        return {"ok": ok, "msg": msg}

    def pf_remove(self, name: str) -> Dict[str, Any]:
        ok, msg = self.portfwd.remove(name)
        return {"ok": ok, "msg": msg}
