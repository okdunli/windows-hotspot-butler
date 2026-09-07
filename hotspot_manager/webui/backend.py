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
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..core import netinfo, pshell
from ..core.captive import CaptivePortal
from ..core.config import AppConfig, PortalConfig
from ..core.deviceman import DeviceManager
from ..core.hotspot import HotspotController, RawClient
from ..core.paths import DATA_DIR, ensure_dirs
from ..core.portal import PortalContext
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
        self.captive = CaptivePortal(self.ctx, on_accept=self._on_portal_accept)

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

    def _on_portal_accept(self, ip: str, mac: str, ua: str) -> None:
        self._toast(f"{ip} 已通过欢迎页并开始上网", "success")

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
        self._gather_fast()

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
                "portal_enabled": self.cfg.portal.enabled,
                "portal_dns": self.cfg.portal.dns_redirect,
                "portal_template": self.cfg.portal.template,
                "portal_title": self.cfg.portal.title,
                "portal_notice": self.cfg.portal.notice,
                "portal_button": self.cfg.portal.button,
            },
            "portal": self.captive.status(),
            "gateway": self.gateway,
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
            p = self.cfg.portal
            for key in ("enabled", "dns_redirect"):
                if key in patch:
                    setattr(p, key, bool(patch[key]))
            for key in ("template", "title", "notice", "button"):
                if key in patch:
                    setattr(p, key, str(patch[key]))
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
        script = str(Path(__file__).resolve().parent.parent.parent / "main_web.py")
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

    def portal_clear(self) -> Dict[str, Any]:
        self.captive.clear_allowed()
        self._pool.submit(self._gather_fast)
        return {"ok": True, "msg": "已清空放行名单"}

    def portal_open(self) -> Dict[str, Any]:
        url = self.captive.url if self.captive.running else f"http://{self.gateway}/"
        webbrowser.open(url)
        return {"ok": True, "msg": url}

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

    def open_url(self, url: str) -> Dict[str, Any]:
        try:
            webbrowser.open(url)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}

    def minimize(self) -> Dict[str, Any]:
        if self._window:
            try:
                self._window.minimize()
            except Exception:
                pass
        return {"ok": True}

    def close(self) -> Dict[str, Any]:
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
        return {"ok": True}

    def pick_portal_file(self) -> Dict[str, Any]:
        """用系统文件对话框挑选自定义门户 HTML。"""
        try:
            if not self._window:
                return {"ok": False, "msg": "窗口未就绪"}
            import webview
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("HTML 文件 (*.html;*.htm)",),
            )
            if result and len(result):
                return {"ok": True, "path": str(result[0])}
            return {"ok": False, "msg": "未选择文件"}
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}
