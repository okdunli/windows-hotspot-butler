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
        """判断本机能否用 Windows 移动热点发射 WiFi。

        返回 dict: can_host(bool), reason(str), soft_ap(Optional[bool]), detail(str)
        Windows 移动热点依赖「软 AP(Soft AP)」模式；只有支持该模式的网卡
        才能作为热点发射信号。多数笔记本内置无线网卡支持，部分 USB 免驱
        网卡（如 Realtek 8188GU）只支持「连 WiFi」、不支持「发射 WiFi」。
        """
        if not self.adapters:
            return {
                "can_host": False,
                "reason": "未检测到无线网卡，或 WLAN 服务未运行。",
                "soft_ap": None,
                "detail": "请确认本机有无线网卡且已启用。",
            }
        sa = self.soft_ap_supported
        drv = self.primary.driver or self.primary.name
        if sa is False:
            return {
                "can_host": False,
                "reason": (
                    f"本机 WiFi 网卡「{self.primary.name}」（{drv}）"
                    f"不支持「软 AP / 热点」模式，Windows 无法用它发射 WiFi 信号，"
                    f"因此开不了热点。"
                ),
                "soft_ap": False,
                "detail": (
                    "这是网卡硬件/驱动能力限制，并非软件问题。\n"
                    "解决办法：更换一块支持 Soft AP（或承载网络 hostednetwork）的无线网卡"
                    "——多数笔记本内置无线网卡支持；当前这块属于只能连 WiFi、不能发射的客户端芯片。"
                ),
            }
        if sa is True:
            return {
                "can_host": True,
                "reason": "",
                "soft_ap": True,
                "detail": "网卡支持软 AP，可以尝试开启热点。",
            }
        # 未能静态确认（命令不可用或显示「未知」），交由运行时决定
        return {
            "can_host": True,
            "reason": "",
            "soft_ap": None,
            "detail": "未能静态确认软 AP 支持，将以实际开启结果为准。",
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
    caps.band_24_supported = any(a.band_24 for a in caps.adapters)
    caps.band_5_supported = any(a.band_5 for a in caps.adapters)
    # 只有明确列出无线电类型才认为结论可信
    caps.band_5_known = any(a.radios for a in caps.adapters)

    if caps.soft_ap_supported is False:
        caps.notes.append(
            "无线网卡不支持「软 AP(Soft AP)」模式，Windows 移动热点无法用它发射 WiFi 信号，"
            "开热点会失败（错误 WiFiDeviceOff）。需更换支持 Soft AP/承载网络的网卡。"
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
