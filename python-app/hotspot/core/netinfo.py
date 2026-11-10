"""网络信息采集：网卡列表、热点网卡定位、ARP 表、主机名反查、网卡流量计数。"""
from __future__ import annotations

import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import pshell
from .storage import normalize_mac

log = logging.getLogger(__name__)

# ICS(Internet 连接共享) 默认给热点网卡分配的地址
ICS_DEFAULT_IP = "192.168.137.1"
_MAC_RE = re.compile(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")
_IPV4_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


@dataclass
class Adapter:
    name: str = ""
    description: str = ""
    status: str = ""
    mac: str = ""
    index: int = 0
    ipv4: str = ""
    is_hotspot: bool = False
    scapy_name: str = ""


def list_adapters() -> List[Adapter]:
    """通过 PowerShell 获取网卡 + IPv4 地址。"""
    data = pshell.ps_json(
        "$a=Get-NetAdapter -ErrorAction SilentlyContinue | "
        "Select-Object Name,InterfaceDescription,Status,MacAddress,InterfaceIndex;"
        "$ip=Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Select-Object InterfaceIndex,IPAddress;"
        "ConvertTo-Json -Depth 4 -Compress @{adapters=@($a);ips=@($ip)}",
        timeout=20,
    )
    out: List[Adapter] = []
    if not isinstance(data, dict):
        return out
    ip_map: Dict[int, str] = {}
    for item in data.get("ips") or []:
        if isinstance(item, dict):
            try:
                idx = int(item.get("InterfaceIndex") or 0)
            except (TypeError, ValueError):
                continue
            addr = str(item.get("IPAddress") or "")
            if idx and addr and not addr.startswith("169.254"):
                ip_map.setdefault(idx, addr)
    for item in data.get("adapters") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("InterfaceIndex") or 0)
        except (TypeError, ValueError):
            idx = 0
        ad = Adapter(
            name=str(item.get("Name") or ""),
            description=str(item.get("InterfaceDescription") or ""),
            status=str(item.get("Status") or ""),
            mac=normalize_mac(str(item.get("MacAddress") or "")),
            index=idx,
            ipv4=ip_map.get(idx, ""),
        )
        low = (ad.description + " " + ad.name).lower()
        ad.is_hotspot = (
            "wi-fi direct" in low
            or "virtual adapter" in low
            or "本地连接*" in ad.name
            or bool(re.match(r"local area connection\*\s*\d+", ad.name.lower()))
            or ad.ipv4 == ICS_DEFAULT_IP
        )
        out.append(ad)
    return out


def find_hotspot_adapter(adapters: Optional[List[Adapter]] = None) -> Optional[Adapter]:
    """定位热点(AP)网卡：优先 192.168.137.1，其次 Wi-Fi Direct 虚拟网卡。"""
    ads = adapters if adapters is not None else list_adapters()
    for ad in ads:
        if ad.ipv4 == ICS_DEFAULT_IP:
            return ad
    up_hotspots = [a for a in ads if a.is_hotspot and a.status.lower() == "up"]
    if up_hotspots:
        return sorted(up_hotspots, key=lambda a: (not bool(a.ipv4), a.index))[0]
    hotspots = [a for a in ads if a.is_hotspot]
    return hotspots[0] if hotspots else None


def hotspot_gateway_ip() -> str:
    ad = find_hotspot_adapter()
    if ad and ad.ipv4:
        return ad.ipv4
    return ICS_DEFAULT_IP


def adapter_stats(name: str) -> Optional[Tuple[int, int]]:
    """返回网卡 (接收字节, 发送字节)。"""
    if not name:
        return None
    safe = name.replace("'", "''")
    data = pshell.ps_json(
        f"$s=Get-NetAdapterStatistics -Name '{safe}' -ErrorAction Stop;"
        "ConvertTo-Json -Compress @{rx=[int64]$s.ReceivedBytes;tx=[int64]$s.SentBytes}",
        timeout=15,
    )
    if isinstance(data, dict) and "rx" in data:
        try:
            return int(data["rx"]), int(data["tx"])
        except (TypeError, ValueError):
            return None
    return None


def arp_entries(subnet_prefix: str = "") -> List[Tuple[str, str]]:
    """解析 `arp -a`，返回 [(ip, mac)]，可按网段前缀过滤（如 '192.168.137.'）。"""
    text = pshell.run_console(["arp", "-a"], timeout=12)
    out: List[Tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("interface", "接口")):
            continue
        m_ip = _IPV4_RE.search(line)
        m_mac = _MAC_RE.search(line)
        if not (m_ip and m_mac):
            continue
        ip = m_ip.group(1)
        mac = normalize_mac(m_mac.group(0))
        if mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"):
            continue
        if ip.endswith(".255") or ip.startswith(("224.", "239.", "169.254")):
            continue
        if subnet_prefix and not ip.startswith(subnet_prefix):
            continue
        out.append((ip, mac))
    return out


def subnet_prefix_of(ip: str) -> str:
    parts = (ip or "").split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + "."
    return ""


def resolve_hostname(ip: str, timeout: float = 0.6) -> str:
    """DNS 反查主机名（快速失败）。"""
    if not ip:
        return ""
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        name = socket.gethostbyaddr(ip)[0]
        return name.split(".")[0]
    except (OSError, socket.herror, socket.gaierror):
        return ""
    finally:
        socket.setdefaulttimeout(old)


def nbt_name(ip: str) -> str:
    """NetBIOS 名称查询（Windows 设备常能查到）。"""
    if not ip:
        return ""
    text = pshell.run_console(["nbtstat", "-A", ip], timeout=6)
    for line in text.splitlines():
        m = re.match(r"\s*(\S+)\s*<00>\s*UNIQUE", line)
        if m:
            return m.group(1).strip()
    return ""


def best_hostname(ip: str, use_nbt: bool = False) -> str:
    name = resolve_hostname(ip)
    if not name and use_nbt:
        name = nbt_name(ip)
    return name


@dataclass
class IcsState:
    shared_adapter: str = ""
    hotspot_adapter: str = ""
    gateway_ip: str = ICS_DEFAULT_IP
    notes: List[str] = field(default_factory=list)


def ics_state() -> IcsState:
    """粗略检查共享状态，给用户提示用。"""
    ads = list_adapters()
    ap = find_hotspot_adapter(ads)
    st = IcsState()
    if ap:
        st.hotspot_adapter = ap.name
        st.gateway_ip = ap.ipv4 or ICS_DEFAULT_IP
    else:
        st.notes.append("未发现热点网卡，热点可能尚未开启。")
    ups = [a for a in ads if a.status.lower() == "up" and not a.is_hotspot and a.ipv4]
    if ups:
        st.shared_adapter = ups[0].name
    else:
        st.notes.append("未发现可共享的上网连接。")
    return st
