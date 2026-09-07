"""无线网卡能力探测。

不同机器差异很大（USB Realtek 8188GU 只支持 2.4G 且不支持承载网络，
Intel AX200/AX210 支持 2.4G+5G+6G 且支持 WPA3），所有能力一律运行时探测，
界面上的选项按探测结果动态启用/置灰，不写死。

数据来源：
  * netsh wlan show drivers      —— 无线电类型、是否支持承载网络、支持的认证方式
  * WinRT NetworkOperatorTetheringManager —— 移动热点实际支持的频段（热点开启后最准）
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import pshell

log = logging.getLogger(__name__)

# 无线电类型 -> 可能的频段
_RADIO_24 = {"802.11b", "802.11g", "802.11n", "802.11ax", "802.11be", "802.11bn"}
_RADIO_5 = {"802.11a", "802.11n", "802.11ac", "802.11ax", "802.11be", "802.11bn"}
_RADIO_6 = {"802.11ax", "802.11be", "802.11bn"}

_KV_ALIASES = {
    "driver": ("驱动程序", "driver"),
    "vendor": ("供应商", "vendor"),
    "radios": ("支持的无线电类型", "radio types supported"),
    "hosted": ("支持的承载网络", "hosted network supported"),
}
_YES = ("yes", "是", "true", "支持")
_NO = ("no", "否", "false", "不支持")


@dataclass
class WlanAdapter:
    name: str = ""
    driver: str = ""
    vendor: str = ""
    radios: List[str] = field(default_factory=list)
    hosted_supported: bool = False
    soft_ap_supported: Optional[bool] = None   # None = 未能静态确认
    wifi_direct_supported: Optional[bool] = None  # None = 未能静态确认
    auths: List[str] = field(default_factory=list)
    band_24: bool = True
    band_5: bool = False
    band_6: bool = False
    band_5_possible: bool = False

    @property
    def pretty_radios(self) -> str:
        return " / ".join(self.radios) if self.radios else "未知"


@dataclass
class WlanCapabilities:
    adapters: List[WlanAdapter] = field(default_factory=list)
    hosted_supported: bool = False
    soft_ap_supported: Optional[bool] = None    # None = 未能静态确认
    wifi_direct_supported: Optional[bool] = None  # None = 未能静态确认
    band_24_supported: bool = True
    band_5_supported: bool = False
    band_5_known: bool = False       # 是否已探测到确定结论
    wpa3_supported: bool = False
    wpa2_supported: bool = True
    notes: List[str] = field(default_factory=list)
    raw: str = ""

    @property
    def primary(self) -> Optional[WlanAdapter]:
        return self.adapters[0] if self.adapters else None

    @property
    def band_5_text(self) -> str:
        if self.band_5_supported:
            return "支持"
        if self.band_5_known:
            return "不支持"
        return "未知（需网卡驱动支持）"

    def supports_band(self, band: str) -> bool:
        if band == "5":
            return self.band_5_supported or not self.band_5_known
        if band == "6":
            return bool(self.primary and self.primary.band_6)
        return True

    def host_hotspot_capability(self) -> Dict[str, object]:
        """判断本机能否发射 WiFi 热点。

        返回 dict: can_host(bool), reason(str), soft_ap(Optional[bool]),
                   wifi_direct(Optional[bool]), hosted(Optional[bool]),
                   backend(str), detail(str)

        Windows 发射热点有三条互不相同的路，能力要分开判断：
          * 软 AP(Soft AP) 模式 —— 对应「移动热点(WinRT)」后端（旧式能力标志）；
          * Wi-Fi Direct —— 对应「移动热点(WinRT)」后端（Win10/11 真正的底层，
            很多「软 AP 不支持」的网卡其实 Wi-Fi Direct 支持，照样能开）；
          * 承载网络(hostednetwork) —— 对应「承载网络(netsh)」后端。
        只要其中一条可用，就能开热点——只是要用对后端。只有当三条都确认
        不支持时，才是真正的硬件限制、需要换网卡或装虚拟 AP 驱动。
        """
        if not self.adapters:
            return {
                "can_host": False,
                "reason": "未检测到无线网卡，或 WLAN 服务未运行。",
                "soft_ap": None,
                "wifi_direct": self.wifi_direct_supported or None,
                "hosted": self.hosted_supported or None,
                "backend": "",
                "detail": "请确认本机有无线网卡且已启用。",
            }
        sa = self.soft_ap_supported
        wfd = self.wifi_direct_supported
        hosted = self.hosted_supported
        drv = self.primary.driver or self.primary.name
        name = self.primary.name

        # 三条路都明确不支持 → 真实硬件限制
        if sa is False and wfd is False and not hosted:
            return {
                "can_host": False,
                "reason": (
                    f"本机 WiFi 网卡「{name}」（{drv}）既不支持「软 AP(Soft AP)」、"
                    f"也不支持「Wi-Fi Direct」、也不支持「承载网络(hostednetwork)」，"
                    f"Windows 无法用它发射 WiFi 信号，任何软件都开不了热点。"
                ),
                "soft_ap": False,
                "wifi_direct": False,
                "hosted": False,
                "backend": "",
                "detail": (
                    "这是网卡硬件/驱动能力限制，并非软件问题。\n"
                    "解决办法：更换一块支持 Wi-Fi Direct 或承载网络(hostednetwork)的无线网卡"
                    "——多数笔记本内置无线网卡支持；当前这块属于只能连 WiFi、不能发射的客户端芯片。\n"
                    "若确实需要用本机共享网络，可尝试带虚拟网卡驱动的方案（如猎豹/360 免费 WiFi），"
                    "它们会在系统里安装一张虚拟 AP 网卡来绕过此限制。"
                ),
            }

        # 软 AP 或 Wi-Fi Direct 支持 → 移动热点(WinRT) 后端可用
        if sa is True or wfd is True:
            via = "Wi-Fi Direct" if (wfd is True and sa is not True) else "软 AP"
            return {
                "can_host": True,
                "reason": "",
                "soft_ap": sa,
                "wifi_direct": wfd,
                "hosted": hosted or None,
                "backend": "winrt",
                "detail": (
                    f"网卡支持{via}，可使用「移动热点(WinRT)」后端开启热点"
                    f"（Windows 10/11 移动热点正是基于{'Wi-Fi Direct' if wfd is True else '软 AP'}）。"
                    f"若 WinRT 不可用，也可回退到「承载网络(netsh)」后端。"
                ),
            }

        # 软 AP / Wi-Fi Direct 不支持，但承载网络支持 → 走 netsh 后端
        if hosted:
            return {
                "can_host": True,
                "reason": "",
                "soft_ap": sa,
                "wifi_direct": wfd,
                "hosted": True,
                "backend": "netsh",
                "detail": (
                    f"本机 WiFi 网卡「{name}」（{drv}）不支持软 AP / Wi-Fi Direct，"
                    f"因此「移动热点(WinRT)」后端用不了；\n"
                    f"但驱动支持「承载网络(hostednetwork)」，可改用「承载网络(netsh)」后端开启热点"
                    f"（功能与猎豹/360 WiFi 走的方案相同，需再用 ICS 共享上网连接）。\n"
                    f"请在「后端与行为」里把控制后端设为「承载网络(netsh)」或「自动选择」。"
                ),
            }

        # 都未能确认 → 保守放行，由实际开启结果决定
        return {
            "can_host": True,
            "reason": "",
            "soft_ap": sa,
            "wifi_direct": wfd,
            "hosted": hosted or None,
            "backend": "",
            "detail": "未能静态确认热点能力，将以实际开启结果为准。",
        }


_KV_RE = re.compile(r"^\s*(?P<key>[^:：]{2,40})\s*[:：]\s*(?P<val>.+?)\s*$")
_IFACE_RE = re.compile(r"^\s*(?:接口名称|Interface name)\s*[:：]\s*(?P<val>.+?)\s*$", re.I)


def _match_alias(key: str, field_name: str) -> bool:
    aliases = _KV_ALIASES.get(field_name, ())
    low = key.lower()
    return any(a in key or a.lower() in low for a in aliases)


def _is_yes(value: str) -> bool:
    low = (value or "").strip().lower()
    return any(low.startswith(y) for y in _YES)


def _is_no(value: str) -> bool:
    low = (value or "").strip().lower()
    return any(low.startswith(n) for n in _NO)


def probe_wireless_capabilities() -> Dict[str, Optional[bool]]:
    """解析 `netsh wlan show wirelesscapabilities` 取「软 AP(Soft AP)」支持。

    返回 接口名 -> soft_ap_supported(True/False/None)。Windows 移动热点
    依赖软 AP 模式，这是判断网卡能否作为热点发射信号的关键静态指标。
    """
    text = pshell.run_console(["netsh", "wlan", "show", "wirelesscapabilities"], timeout=20)
    result: Dict[str, Optional[bool]] = {}
    cur: Optional[str] = None
    for line in text.splitlines():
        m_iface = _IFACE_RE.match(line)
        if m_iface:
            cur = m_iface.group("val").strip()
            result.setdefault(cur, None)
            continue
        low = line.lower()
        # 形如 "    软 AP  : 不支持" / "    Soft AP  : Not Supported"
        if "软 ap" in low or "soft ap" in low or "softap" in low:
            mv = re.search(r"[:：]\s*(.+?)\s*$", line)
            if mv:
                v = mv.group(1).strip()
                if _is_yes(v):
                    result[cur] = True
                elif _is_no(v):
                    result[cur] = False
                # 未知/Unknown 保持 None
    return result


def probe_wifi_direct() -> Dict[str, Optional[bool]]:
    """解析 `netsh wlan show wirelesscapabilities` 取「Wi-Fi Direct」支持。

    返回 接口名 -> wifi_direct_supported(True/False/None)。这是关键但常被忽略的
    能力：Windows 10/11 的移动热点(WinRT) 实际依赖 **Wi-Fi Direct** 而非老旧的
    「软 AP(Soft AP)」模式。很多 USB 免驱网卡（如 Realtek 8188GU）显示
    「软 AP: 不支持」，却「Wi-Fi Direct: 支持」——这类网卡**照样能开热点**
    （猎豹/360 WiFi 正是走这条路），之前的「软 AP 不支持=不能开」判断过于悲观。
    """
    text = pshell.run_console(["netsh", "wlan", "show", "wirelesscapabilities"], timeout=20)
    result: Dict[str, Optional[bool]] = {}
    cur: Optional[str] = None
    for line in text.splitlines():
        m_iface = _IFACE_RE.match(line)
        if m_iface:
            cur = m_iface.group("val").strip()
            result.setdefault(cur, None)
            continue
        low = line.lower()
        # 形如 "    Wi-Fi Direct  : 支持" / "    WiFiDirect  : Supported"
        if "wi-fi direct" in low or "wifi direct" in low or "wifidirect" in low:
            mv = re.search(r"[:：]\s*(.+?)\s*$", line)
            if mv:
                v = mv.group(1).strip()
                if _is_yes(v):
                    result[cur] = True
                elif _is_no(v):
                    result[cur] = False
                # Unknown 保持 None
    return result


def probe_drivers() -> WlanCapabilities:
    """解析 `netsh wlan show drivers`（中英文双语，支持多网卡）。"""
    text = pshell.run_console(["netsh", "wlan", "show", "drivers"], timeout=20)
    caps = WlanCapabilities(raw=text)
    if not text.strip() or re.search(r"(没有|no wireless|不是)", text, re.I):
        caps.notes.append("未检测到无线网卡，或 WLAN 服务未运行。")
        return caps

    current: Optional[WlanAdapter] = None
    in_auth_block = False

    for line in text.splitlines():
        m_iface = _IFACE_RE.match(line)
        if m_iface:
            if current:
                caps.adapters.append(current)
            current = WlanAdapter(name=m_iface.group("val").strip())
            in_auth_block = False
            continue
        if current is None:
            continue
        low = line.lower()
        if ("authentication and cipher" in low) or ("身份验证和密码" in line):
            in_auth_block = True
            continue
        # 认证/加密列表块内的行不含冒号，形如 "WPA2 - 个人       CCMP"
        if in_auth_block and ":" not in line and "：" not in line:
            auth = re.split(r"\s{2,}", line.strip())
            if auth and auth[0] and re.search(r"[A-Za-z一-鿿]{2,}", auth[0]):
                current.auths.append(auth[0].strip())
            continue
        m = _KV_RE.match(line)
        if not m:
            continue
        in_auth_block = False
        key, val = m.group("key").strip(), m.group("val").strip()
        if _match_alias(key, "driver"):
            current.driver = val
        elif _match_alias(key, "vendor"):
            current.vendor = val
        elif _match_alias(key, "radios"):
            current.radios = re.findall(r"802\.11[a-zA-Z]{1,3}", val)
        elif _match_alias(key, "hosted"):
            current.hosted_supported = _is_yes(val)

    if current:
        caps.adapters.append(current)
    if not caps.adapters:
        caps.notes.append("无法解析无线网卡信息（netsh 输出格式异常）。")
        return caps

    # 合并「软 AP」支持（按接口名匹配）
    soft = probe_wireless_capabilities()
    soft_values: List[Optional[bool]] = []
    for ad in caps.adapters:
        if ad.name in soft:
            ad.soft_ap_supported = soft[ad.name]
            soft_values.append(soft[ad.name])

    # 合并「Wi-Fi Direct」支持（按接口名匹配）——移动热点真正的底层能力
    wfd = probe_wifi_direct()
    wfd_values: List[Optional[bool]] = []
    for ad in caps.adapters:
        if ad.name in wfd:
            ad.wifi_direct_supported = wfd[ad.name]
            wfd_values.append(wfd[ad.name])

    for ad in caps.adapters:
        radios = {r.lower() for r in ad.radios}
        ad.band_24 = bool(radios & _RADIO_24) or not radios
        ad.band_5 = bool(radios & _RADIO_5) and bool(radios & {"802.11a", "802.11ac", "802.11ax", "802.11be", "802.11bn"})
        ad.band_6 = bool(radios & _RADIO_6) and bool(radios & {"802.11be", "802.11ax"})
        # 802.11n 常见于 2.4G 单频网卡，也可能是双频，标记为“可能”
        ad.band_5_possible = (not ad.band_5) and ("802.11n" in radios or "802.11ac" in radios)
        auths = " ".join(ad.auths).lower()
        ad_wpa3 = "wpa3" in auths
        ad_wpa2 = "wpa2" in auths
        caps.wpa3_supported = caps.wpa3_supported or ad_wpa3
        caps.wpa2_supported = caps.wpa2_supported or ad_wpa2

    caps.hosted_supported = any(a.hosted_supported for a in caps.adapters)
    caps.soft_ap_supported = next((v for v in soft_values if v is not None), None)
    caps.wifi_direct_supported = next((v for v in wfd_values if v is not None), None)
    caps.band_24_supported = any(a.band_24 for a in caps.adapters)
    caps.band_5_supported = any(a.band_5 for a in caps.adapters)
    # 只有明确列出无线电类型才认为结论可信
    caps.band_5_known = any(a.radios for a in caps.adapters)

    if caps.soft_ap_supported is False:
        if caps.wifi_direct_supported is True:
            caps.notes.append(
                "网卡「软 AP(Soft AP)」模式不支持，但支持 **Wi-Fi Direct**——"
                "Windows 10/11 移动热点(WinRT) 正是基于 Wi-Fi Direct，因此仍可正常开热点；"
                "若走「承载网络(netsh)」后端也可，前提是驱动支持承载网络。"
            )
        else:
            caps.notes.append(
                "无线网卡不支持「软 AP(Soft AP)」模式，Windows 移动热点无法用它发射 WiFi 信号，"
                "开热点会失败（错误 WiFiDeviceOff）。需更换支持 Soft AP/承载网络的网卡。"
            )
    if caps.wifi_direct_supported is True and caps.soft_ap_supported is not True:
        caps.notes.append(
            "检测到 Wi-Fi Direct 支持：即使「软 AP」显示不支持，本机仍可用「移动热点(WinRT)」"
            "或「承载网络(netsh)」发射热点（猎豹/360 等共享软件即走此能力）。"
        )
    if not caps.hosted_supported:
        caps.notes.append(
            "当前无线网卡驱动不支持「承载网络(hostednetwork)」，"
            "netsh 后端不可用，请使用「移动热点(WinRT)」后端。"
        )
    if caps.band_5_known and not caps.band_5_supported:
        caps.notes.append(
            "无线网卡无线电类型不含 5 GHz，5 GHz 选项即使选择也会被系统忽略。"
        )
    return caps


def merge_tethering(caps: WlanCapabilities, tether_status: Dict) -> WlanCapabilities:
    """合并 WinRT 移动热点上报的频段支持（热点开启时最准）。"""
    if not tether_status:
        return caps
    active = str(tether_status.get("state", "")).lower() == "on"
    b24 = tether_status.get("band24Supported")
    b5 = tether_status.get("band5Supported")
    if active:
        # 热点运行中的数据是权威结论
        if b24 is not None:
            caps.band_24_supported = bool(b24)
        if b5 is not None:
            caps.band_5_supported = bool(b5)
            caps.band_5_known = True
    else:
        # 未开启时 IsBandSupported 常返回 false，只作弱证据
        if b5 is True:
            caps.band_5_supported = True
            caps.band_5_known = True
    return caps
