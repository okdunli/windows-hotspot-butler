"""互联网连接共享(ICS)控制。

netsh hostednetwork 只是建了一个 AP，**并不会**把上网连接共享给连进来的设备——
这正是很多「能连上却上不了网」的根因，也是猎豹/360 类共享软件能上网的关键：
它们在打开热点后，会用 HNetCfg.HNetShare COM 对象启用 ICS。

本模块用纯 Windows 自带能力复现这一步，无需安装任何第三方虚拟网卡驱动。
ICE 启用后，连接该热点的设备即可通过本机上网连接访问互联网。

注意：ICS 需要管理员权限；启用/关闭均为 best-effort，失败不应阻断热点本身。
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from . import netinfo, pshell
from .paths import ICS_PS1

log = logging.getLogger(__name__)


def internet_adapter_name() -> Optional[str]:
    """返回当前用于上网的网卡名称（Get-NetConnectionProfile IPv4Connectivity=Internet）。"""
    data = pshell.ps_json(
        "$p = Get-NetConnectionProfile -ErrorAction SilentlyContinue | "
        "Where-Object {$_.IPv4Connectivity -eq 'Internet' -or $_.IPv6Connectivity -eq 'Internet'};"
        "if ($p) { ($p | Select-Object -First 1).InterfaceAlias } else { $null }",
        timeout=20,
    )
    if isinstance(data, str) and data.strip():
        return data.strip()
    return None


def enable(private_name: Optional[str] = None,
           public_name: Optional[str] = None) -> Tuple[bool, str]:
    """把上网连接(public)共享给热点网卡(private)。返回 (ok, 消息)。"""
    pub = public_name or internet_adapter_name()
    if not pub:
        return False, "找不到可共享的上网连接（请先连上有线/无线网）"
    if not private_name:
        try:
            ap = netinfo.find_hotspot_adapter()
            private_name = ap.name if ap else None
        except Exception:
            private_name = None
    if not private_name:
        return False, "未找到热点网卡（请先开启热点再启用共享）"
    data = pshell.ps_script(
        ICS_PS1,
        {"Action": "Enable", "Public": pub, "Private": private_name},
        timeout=45,
    )
    if isinstance(data, dict) and data.get("ok"):
        return True, data.get("message") or f"已共享 {pub} -> {private_name}"
    err = (data.get("error") if isinstance(data, dict) else None) or "ICS 启用失败"
    return False, str(err)


def disable(private_name: Optional[str] = None) -> Tuple[bool, str]:
    """关闭热点网卡的共享。返回 (ok, 消息)。"""
    if not private_name:
        try:
            ap = netinfo.find_hotspot_adapter()
            private_name = ap.name if ap else None
        except Exception:
            private_name = None
    if not private_name:
        return False, "未找到热点网卡"
    data = pshell.ps_script(
        ICS_PS1,
        {"Action": "Disable", "Private": private_name},
        timeout=45,
    )
    if isinstance(data, dict) and data.get("ok"):
        return True, data.get("message") or "已关闭共享"
    err = (data.get("error") if isinstance(data, dict) else None) or "ICS 关闭失败"
    return False, str(err)
