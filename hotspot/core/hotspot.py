"""热点控制：Windows 10/11 移动热点(WinRT) + 承载网络(netsh) 双后端。

开热点有三条互不相同的路，能力由 wificaps 静态探测：
  * 软 AP(Soft AP) 模式        → WinRT 后端（旧式能力标志）
  * Wi-Fi Direct               → WinRT 后端（Win10/11 真正底层，很多「软 AP 不支持」
                                  的 USB 网卡其实支持它，照样能开）
  * 承载网络(hostednetwork)    → netsh 后端（猎豹/360 同款方案）
netsh 后端只建 AP，需再用 ics 模块启用 ICS 共享上网连接，设备才能实际上网。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import netinfo, pshell, wificaps, ics
from .config import HotspotConfig
from .paths import TETHERING_PS1
from .storage import normalize_mac

log = logging.getLogger(__name__)

_MAC_RE = re.compile(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")


@dataclass
class HotspotStatus:
    ok: bool = True
    active: bool = False
    state: str = "unknown"          # on | off | transition | unknown
    ssid: str = ""
    passphrase: str = ""
    band: str = ""
    client_count: int = 0
    max_clients: Optional[int] = None
    backend: str = "none"
    message: str = ""
    error_type: str = ""
    band_24_supported: bool = True
    band_5_supported: bool = False
    clients_supported: bool = False
    updated: float = field(default_factory=time.time)

    @property
    def state_text(self) -> str:
        return {
            "on": "已开启",
            "off": "已关闭",
            "transition": "切换中…",
        }.get(self.state, "未知")


@dataclass
class RawClient:
    mac: str = ""
    ip: str = ""
    source: str = ""


class BackendError(Exception):
    pass


# --------------------------------------------------------------------------- #
#                          后端 1：移动热点 (WinRT)                            #
# --------------------------------------------------------------------------- #
class WinRTBackend:
    name = "winrt"
    label = "移动热点 (WinRT)"

    def __init__(self) -> None:
        self._available: Optional[bool] = None
        self._last_error = ""

    # ---- 内部 ----
    def _call(self, action: str, timeout: float = 45.0, **params) -> Dict:
        args = {"Action": action}
        args.update(params)
        data = pshell.ps_script(TETHERING_PS1, args, timeout=timeout)
        if not data.get("ok", False):
            self._last_error = str(data.get("error") or "未知错误")
        return data

    @staticmethod
    def _to_status(data: Dict) -> HotspotStatus:
        state_raw = str(data.get("state") or "").lower()
        state = {"on": "on", "off": "off", "intransition": "transition"}.get(
            state_raw, "unknown"
        )
        max_c = data.get("maxClientCount")
        try:
            max_c = int(max_c) if max_c else None
        except (TypeError, ValueError):
            max_c = None
        st = HotspotStatus(
            ok=bool(data.get("ok", False)),
            active=state == "on",
            state=state,
            ssid=str(data.get("ssid") or ""),
            passphrase=str(data.get("passphrase") or ""),
            band=str(data.get("band") or ""),
            client_count=int(data.get("clientCount") or 0),
            max_clients=max_c,
            backend="winrt",
            message=str(data.get("error") or ""),
            error_type=str(data.get("errorType") or ""),
            band_24_supported=bool(data.get("band24Supported", True)),
            band_5_supported=bool(data.get("band5Supported", False)),
            clients_supported=bool(data.get("clientsSupported", False)),
        )
        # 热点未开启时 IsBandSupported 常返回 false，视为“未知”而非不支持
        if not st.active and not st.band_24_supported and not st.band_5_supported:
            st.band_24_supported = True
        return st

    # ---- 对外 ----
    def available(self) -> bool:
        if self._available is None:
            data = self._call("probe", timeout=30)
            self._available = bool(data.get("ok")) or data.get("errorType") == "NO_INTERNET_PROFILE"
            if not self._available:
                log.info("WinRT 移动热点不可用：%s", self._last_error)
        return bool(self._available)

    @property
    def last_error(self) -> str:
        return self._last_error

    def snapshot(self) -> Tuple[HotspotStatus, List[RawClient]]:
        data = self._call("all", timeout=40)
        st = self._to_status(data)
        clients: List[RawClient] = []
        for item in data.get("clients") or []:
            if not isinstance(item, dict):
                continue
            mac = normalize_mac(str(item.get("mac") or ""))
            ips = [str(i) for i in (item.get("ips") or []) if i]
            ipv4 = next((i for i in ips if re.match(r"^\d+\.\d+\.\d+\.\d+$", i)), "")
            if mac:
                clients.append(RawClient(mac=mac, ip=ipv4, source="winrt"))
        return st, clients

    def apply(self, cfg: HotspotConfig) -> Tuple[bool, str]:
        params = {
            "SsidB64": pshell.b64(cfg.ssid),
            "PassB64": pshell.b64("" if cfg.security == "open" else cfg.passphrase),
            "Band": cfg.band,
        }
        data = self._call("configure", timeout=60, **params)
        if data.get("ok"):
            warn = data.get("bandWarning")
            return True, f"配置已写入系统移动热点。{('频段提示：' + str(warn)) if warn else ''}"
        return False, self._explain(data)

    def start(self) -> Tuple[bool, str]:
        data = self._call("start", timeout=90)
        if data.get("ok"):
            return True, "热点已开启"
        return False, self._explain(data)

    def stop(self) -> Tuple[bool, str]:
        data = self._call("stop", timeout=90)
        if data.get("ok"):
            return True, "热点已关闭"
        return False, self._explain(data)

    @staticmethod
    def _explain(data: Dict) -> str:
        etype = str(data.get("errorType") or "")
        err = str(data.get("error") or "未知错误")
        table = {
            "NO_INTERNET_PROFILE": "未检测到可共享的网络连接，请先连上有线/无线网络再开启热点。",
            "API_UNAVAILABLE": "当前系统不支持移动热点 API（需 Windows 10 1607 以上）。",
            "TIMEOUT": "系统响应超时，请稍后重试。",
        }
        detail = table.get(etype)
        state_map = {
            "WiFiDeviceOff": "无线网卡已关闭（飞行模式/无线开关），请先打开 WLAN。",
            "MobileBroadbandDeviceOff": "移动宽带设备已关闭。",
            "NetworkLimitedConnectivity": "当前网络无法共享（无 Internet 连接）。",
            "TetheringOperationInProgress": "上一次操作仍在进行中，请稍等。",
            "OperationInProgress": "上一次操作仍在进行中，请稍等。",
            "EntitlementCheckFailure": "运营商/系统策略不允许共享网络。",
            "Unknown": "系统返回未知错误，建议在「设置 → 网络 → 移动热点」里手动试一次。",
        }
        if err in state_map:
            detail = state_map[err]
        return f"{detail or err}"


# --------------------------------------------------------------------------- #
#                        后端 2：承载网络 (netsh)                              #
# --------------------------------------------------------------------------- #
class NetshBackend:
    name = "netsh"
    label = "承载网络 (netsh)"

    _YES = ("yes", "是", "允许", "已启动", "started", "available", "可用")

    def __init__(self) -> None:
        self._available: Optional[bool] = None
        self._last_error = ""

    @staticmethod
    def _kv(text: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                out[k.strip().lower()] = v.strip()
        return out

    def available(self) -> bool:
        if self._available is None:
            text = pshell.run_console(["netsh", "wlan", "show", "drivers"], timeout=15)
            kv = self._kv(text)
            val = ""
            for key in kv:
                if "hosted network" in key or "承载网络" in key:
                    val = kv[key].lower()
                    break
            self._available = any(y in val for y in self._YES)
            if not self._available:
                self._last_error = "无线网卡驱动不支持承载网络(hostednetwork)"
                log.info("netsh 承载网络不可用：%s", val or "未知")
        return bool(self._available)

    @property
    def last_error(self) -> str:
        return self._last_error

    def snapshot(self) -> Tuple[HotspotStatus, List[RawClient]]:
        text = pshell.run_console(["netsh", "wlan", "show", "hostednetwork"], timeout=15)
        kv = self._kv(text)
        status_val = ""
        ssid = ""
        max_c = None
        for key, val in kv.items():
            if key in ("status", "状态"):
                status_val = val.lower()
            elif "ssid" in key and "name" not in key and "bssid" not in key:
                ssid = val.strip('"')
            elif "max number of clients" in key or "客户端最大数目" in key or "最大客户端" in key:
                try:
                    max_c = int(re.sub(r"\D", "", val) or 0) or None
                except ValueError:
                    max_c = None
        active = any(y in status_val for y in ("started", "已启动"))
        clients: List[RawClient] = []
        in_clients = False
        for line in text.splitlines():
            low = line.lower()
            if "client" in low or "客户端" in line:
                in_clients = True
            m = _MAC_RE.search(line)
            if m and in_clients:
                mac = normalize_mac(m.group(0))
                if mac not in [c.mac for c in clients]:
                    clients.append(RawClient(mac=mac, source="netsh"))
        st = HotspotStatus(
            ok=bool(text.strip()),
            active=active,
            state="on" if active else "off",
            ssid=ssid,
            band="2.4",
            client_count=len(clients),
            max_clients=max_c,
            backend="netsh",
            clients_supported=True,
            band_24_supported=True,
            band_5_supported=False,
            message="" if text.strip() else "netsh 无输出",
        )
        return st, clients

    def apply(self, cfg: HotspotConfig) -> Tuple[bool, str]:
        args = ["netsh", "wlan", "set", "hostednetwork", "mode=allow", f"ssid={cfg.ssid}"]
        if cfg.security != "open":
            args.append(f"key={cfg.passphrase}")
            args.append("keyUsage=persistent")
        text = pshell.run_console(args, timeout=20)
        ok = bool(text) and not re.search(r"(fail|失败|错误|无效)", text, re.I)
        return ok, (text.strip().splitlines() or ["已写入承载网络配置"])[-1][:160]

    def start(self) -> Tuple[bool, str]:
        text = pshell.run_console(["netsh", "wlan", "start", "hostednetwork"], timeout=30)
        ok = bool(re.search(r"(started|已启动)", text, re.I))
        msg = (text.strip().splitlines() or ["无输出"])[-1][:160]
        if not ok and "无法启动" in text:
            msg += "（承载网络需要网卡驱动支持，且需管理员权限）"
        return ok, msg

    def stop(self) -> Tuple[bool, str]:
        text = pshell.run_console(["netsh", "wlan", "stop", "hostednetwork"], timeout=30)
        ok = bool(re.search(r"(stopped|已停止)", text, re.I))
        return ok, (text.strip().splitlines() or ["无输出"])[-1][:160]


# --------------------------------------------------------------------------- #
#                              统一控制器                                     #
# --------------------------------------------------------------------------- #
class HotspotController:
    """对上层只暴露一套接口，内部自动挑选后端。"""

    def __init__(self, cfg: HotspotConfig) -> None:
        self.cfg = cfg
        self.winrt = WinRTBackend()
        self.netsh = NetshBackend()
        self._backend: Optional[object] = None
        self._lock = threading.RLock()
        self.last_status = HotspotStatus(state="unknown", backend="none")
        self.diagnostics: List[str] = []
        self.wlan_caps: Optional[wificaps.WlanCapabilities] = None
        # 事实标记：一旦真的开成功过，后续一切能力提示都以此为准，不再误报“不支持”
        self.ever_active: bool = False

    # ---- 网卡能力探测 ----
    def detect_capabilities(self, refresh: bool = False) -> wificaps.WlanCapabilities:
        """探测本机无线网卡能力（驱动 + 移动热点 API），结果供 UI 动态启用选项。"""
        if self.wlan_caps is not None and not refresh:
            return self.wlan_caps
        caps = wificaps.probe_drivers()
        try:
            data = self.winrt._call("probe", timeout=30)
            if data.get("ok") or data.get("ssid") is not None:
                caps = wificaps.merge_tethering(caps, data)
            # WinRT 移动热点 API 可用 = 本机可开热点（权威信号，盖过 netsh 误报）
            caps.winrt_available = bool(data.get("ok")) or (
                data.get("errorType") == "NO_INTERNET_PROFILE")
        except Exception:
            pass
        # 把“实测结果”带给能力对象：静态探测只是预测，实测才是结论
        caps.active_now = bool(self.last_status.active)
        caps.ever_active = bool(self.ever_active or caps.active_now)
        self.wlan_caps = caps
        return caps

    # ---- 后端选择 ----
    def resolve_backend(self, force: str = "") -> Optional[object]:
        want = force or self.cfg.backend or "auto"
        with self._lock:
            if want == "winrt":
                self._backend = self.winrt if self.winrt.available() else None
            elif want == "netsh":
                self._backend = self.netsh if self.netsh.available() else None
            else:
                # auto：优先用静态能力结论选对后端，避免 winrt.available()
                # 只查 API 是否存在（不查硬件）而误选导致 8188GU 类网卡 start 才失败。
                caps = self.detect_capabilities()
                sa = caps.soft_ap_supported
                wfd = caps.wifi_direct_supported
                hosted = caps.hosted_supported
                if sa is True or wfd is True:
                    # 软 AP 或 Wi-Fi Direct 支持 → 移动热点(WinRT) 最完整
                    self._backend = self.winrt if self.winrt.available() else (
                        self.netsh if self.netsh.available() else None)
                elif sa is False and wfd is False and hosted:
                    # 软 AP/Wi-Fi Direct 都不支持但承载网络支持 → 直接走 netsh
                    self._backend = self.netsh if self.netsh.available() else None
                elif sa is False and wfd is False and not hosted:
                    # 三条路都确认不行 → 无可用后端
                    self._backend = None
                else:
                    # 静态结论未知 → 回退到 available() 探测
                    if self.winrt.available():
                        self._backend = self.winrt
                    elif self.netsh.available():
                        self._backend = self.netsh
                    else:
                        self._backend = None
            return self._backend

    @property
    def backend(self) -> Optional[object]:
        return self._backend or self.resolve_backend()

    @property
    def backend_label(self) -> str:
        be = self._backend
        return getattr(be, "label", "不可用") if be else "不可用"

    def capabilities(self, refresh: bool = False) -> Dict[str, object]:
        """告诉界面哪些功能真正可用，避免给用户虚假承诺（全部来自实际探测）。"""
        be = self.backend
        st = self.last_status
        bname = getattr(be, "name", "none") if be else "none"
        winrt = bname == "winrt"
        caps = self.detect_capabilities(refresh=refresh)
        hc = caps.host_hotspot_capability()
        return {
            "backend": bname,
            "backend_label": self.backend_label,
            "recommended_backend": hc.get("backend", ""),
            "ssid": be is not None,
            "passphrase": be is not None,
            "band": winrt,
            "band_24": caps.band_24_supported,
            "band_5": winrt and caps.supports_band("5"),
            "band_5_known": caps.band_5_known,
            "band_5_text": caps.band_5_text,
            "hosted_supported": caps.hosted_supported,
            "soft_ap_supported": caps.soft_ap_supported,
            "wifi_direct_supported": caps.wifi_direct_supported,
            "winrt_available": caps.winrt_available,
            "can_host_hotspot": hc["can_host"],
            "host_block_reason": hc.get("reason", ""),
            "host_block_detail": hc.get("detail", ""),
            "wpa3": caps.wpa3_supported,
            "wpa2": caps.wpa2_supported,
            "max_clients_writable": False,          # Windows 未开放写入 API
            "max_clients_readable": st.max_clients is not None,
            "max_clients_system": st.max_clients,
            "client_list": True,                    # 退化用 ARP 也能拿到
            "security_choice": bname == "netsh",
            "adapters": [a.name for a in caps.adapters],
            "radios": caps.primary.pretty_radios if caps.primary else "未知",
            "driver": caps.primary.driver if caps.primary else "",
            "notes": list(caps.notes),
            "admin": pshell.is_admin(),
        }

    # ---- 状态与客户端 ----
    def snapshot(self) -> Tuple[HotspotStatus, List[RawClient]]:
        be = self.backend
        if be is None:
            st = HotspotStatus(
                ok=False, state="unknown", backend="none",
                message=self.winrt.last_error or self.netsh.last_error
                or "系统未提供可用的热点后端",
            )
            self.last_status = st
            return st, []
        try:
            st, clients = be.snapshot()          # type: ignore[union-attr]
        except Exception as exc:                  # 后端异常不应崩溃 UI
            log.exception("获取热点状态失败")
            st = HotspotStatus(ok=False, backend=getattr(be, "name", "?"),
                               message=f"获取状态异常：{exc}")
            clients = []
        st.backend = getattr(be, "name", "?")
        self.last_status = st
        if st.active:
            self.ever_active = True
            if self.wlan_caps is not None:
                self.wlan_caps.active_now = True
                self.wlan_caps.ever_active = True

        # 用 ARP 表补齐 IP（WinRT 有时只给 MAC）
        try:
            gw = netinfo.hotspot_gateway_ip()
            prefix = netinfo.subnet_prefix_of(gw)
            arp = netinfo.arp_entries(prefix)
            by_mac = {mac: ip for ip, mac in arp}
            known = {c.mac for c in clients}
            for c in clients:
                if not c.ip and c.mac in by_mac:
                    c.ip = by_mac[c.mac]
            # netsh 后端拿不到 IP；ARP 里的额外设备也补进来（排除网关自身）
            if st.active:
                for ip, mac in arp:
                    if mac not in known and ip != gw:
                        clients.append(RawClient(mac=mac, ip=ip, source="arp"))
                        known.add(mac)
        except Exception as exc:
            log.debug("ARP 补全失败：%s", exc)
        st.client_count = max(st.client_count, len(clients))
        return st, clients

    # ---- 操作 ----
    def apply_config(self, cfg: Optional[HotspotConfig] = None) -> Tuple[bool, str]:
        cfg = cfg or self.cfg
        be = self.backend
        if be is None:
            return False, "没有可用的热点后端"
        ok, msg = be.apply(cfg)                   # type: ignore[union-attr]
        extra = []
        if getattr(be, "name", "") == "winrt":
            if cfg.security != "wpa2":
                extra.append("系统移动热点固定使用 WPA2-个人加密，其它加密选项仅本地记录")
            if cfg.band == "5" and not self.last_status.band_5_supported:
                extra.append("当前无线网卡可能不支持 5 GHz，系统会回退到 2.4 GHz")
        if extra:
            msg = msg + "（" + "；".join(extra) + "）"
        return ok, msg

    def start(self) -> Tuple[bool, str]:
        be = self.backend
        if be is None:
            return False, "没有可用的热点后端"
        ok, msg = be.start()                      # type: ignore[union-attr]
        if not ok:
            msg = self._enrich_start_error(msg)
            return ok, msg
        self.ever_active = True
        if self.wlan_caps is not None:
            self.wlan_caps.ever_active = True
            self.wlan_caps.active_now = True
        # netsh 后端只建 AP、不共享上网，必须显式开 ICS 设备才能上网
        if getattr(be, "name", "") == "netsh":
            try:
                ok_ics, m_ics = ics.enable()
                if ok_ics:
                    msg += "；已开启互联网连接共享(ICS)，设备可正常上网。"
                else:
                    msg += (f"；但互联网连接共享未开启（{m_ics}），"
                            f"设备可能连上却无法上网，请确认以管理员身份运行。")
                    log.warning("ICS 启用失败：%s", m_ics)
            except Exception as exc:
                log.exception("ICS 启用异常")
                msg += "；互联网连接共享启用时发生异常，设备可能无法上网。"
        return ok, msg

    def _enrich_start_error(self, msg: str) -> str:
        """开启失败时，结合网卡能力给出明确、可操作的指引，避免含糊的
        WiFiDeviceOff 报错让用户摸不着头脑。"""
        caps = self.detect_capabilities(refresh=False)
        hc = caps.host_hotspot_capability()
        if not hc["can_host"] and hc.get("detail"):
            # 已知网卡不支持 AP 模式：直接说明硬件限制 + 换网卡建议
            if hc["reason"]:
                return hc["reason"] + "\n" + hc["detail"]
        return msg

    def stop(self) -> Tuple[bool, str]:
        be = self.backend
        if be is None:
            return False, "没有可用的热点后端"
        name = getattr(be, "name", "")
        priv = None
        if name == "netsh":
            # 关闭前先拿到热点网卡名，关闭后该虚拟网卡可能被卸载
            try:
                priv = netinfo.find_hotspot_adapter()
            except Exception:
                priv = None
        ok, msg = be.stop()                       # type: ignore[union-attr]
        if ok and name == "netsh":
            try:
                ics.disable(private_name=priv.name if priv else None)
            except Exception:
                log.exception("ICS 关闭异常")
        return ok, msg

    def toggle(self) -> Tuple[bool, str]:
        return self.stop() if self.last_status.active else self.start()

    def diagnose(self) -> List[str]:
        """收集环境诊断信息，供「关于/诊断」页面显示。"""
        lines: List[str] = []
        caps = self.detect_capabilities(refresh=True)
        if caps.primary:
            lines.append(f"无线网卡：{caps.primary.name} — {caps.primary.driver}")
            lines.append(f"无线电类型：{caps.primary.pretty_radios}")
            lines.append(
                f"频段支持：2.4GHz {'√' if caps.band_24_supported else '×'}，"
                f"5GHz {caps.band_5_text}，6GHz {'√' if caps.primary.band_6 else '×'}"
            )
            lines.append(
                f"加密能力：WPA2 {'√' if caps.wpa2_supported else '×'}，"
                f"WPA3 {'√' if caps.wpa3_supported else '×'}"
            )
            wfd = caps.wifi_direct_supported
            lines.append(
                f"Wi-Fi Direct：{'支持' if wfd is True else ('不支持' if wfd is False else '未知')}"
            )
        lines.extend(caps.notes)
        lines.append(f"管理员权限：{'是' if pshell.is_admin() else '否（部分操作会失败）'}")
        lines.append(f"移动热点 API(WinRT)：{'可用' if self.winrt.available() else '不可用 - ' + self.winrt.last_error}")
        lines.append(f"承载网络(netsh)：{'可用' if self.netsh.available() else '不可用 - ' + self.netsh.last_error}")
        st = self.last_status
        lines.append(f"当前后端：{self.backend_label}，状态：{st.state_text}")
        if st.max_clients:
            lines.append(f"系统上报的最大客户端数：{st.max_clients}")
        try:
            ics = netinfo.ics_state()
            lines.append(f"热点网卡：{ics.hotspot_adapter or '未发现'}，网关 IP：{ics.gateway_ip}")
            lines.append(f"被共享的上网连接：{ics.shared_adapter or '未发现'}")
            lines.extend(ics.notes)
        except Exception as exc:
            lines.append(f"网卡信息获取失败：{exc}")
        self.diagnostics = lines
        return lines
