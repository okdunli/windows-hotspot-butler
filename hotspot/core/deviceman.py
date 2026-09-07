"""设备管理器：把「热点上报的客户端 / ARP / 设备档案 / 流量」合并成统一视图。"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import netinfo, oui
from .hotspot import RawClient
from .storage import DeviceStore, TrafficDB, normalize_mac

log = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    mac: str = ""
    ip: str = ""
    online: bool = False
    name: str = ""            # 展示名（自定义名 > 主机名 > 厂商+尾号 > MAC）
    custom_name: str = ""
    hostname: str = ""
    vendor: str = ""
    dev_type: str = "unknown"
    icon: str = "auto"
    custom_icon: str = ""
    note: str = ""
    limited: bool = False
    first_seen: float = 0.0
    last_seen: float = 0.0
    portal_accepted: float = 0.0
    rate_down: float = 0.0    # B/s
    rate_up: float = 0.0
    session_rx: int = 0
    session_tx: int = 0
    total_rx: int = 0
    total_tx: int = 0
    source: str = ""

    @property
    def effective_icon(self) -> str:
        """auto 时按识别出的类型取预设图标。"""
        if self.icon == "custom":
            return "custom"
        if self.icon and self.icon != "auto":
            return self.icon
        return self.dev_type or "unknown"

    @property
    def type_label(self) -> str:
        return oui.type_label(self.dev_type)


class DeviceManager:
    def __init__(self, store: DeviceStore, db: TrafficDB, traffic) -> None:
        self.store = store
        self.db = db
        self.traffic = traffic
        self._lock = threading.RLock()
        self._cache: Dict[str, DeviceInfo] = {}
        self.online_macs: set = set()

    # ------------------------------------------------------------------ #
    def sync(self, raw_clients: Sequence[RawClient], active: bool = True,
             gateway_ip: str = "", resolve_names: bool = True,
             arp_cache: Optional[Dict[str, str]] = None) -> List[DeviceInfo]:
        """合并热点客户端 + ARP + 档案 + 流量，返回按在线优先排序的设备列表。

        arp_cache: 预先抓好的 {ip: mac}（来自后台线程），传入可避免重复扫描 ARP；
        不传则内部自行调用 netinfo.arp_entries()。
        """
        gw = gateway_ip or netinfo.hotspot_gateway_ip()
        prefix = netinfo.subnet_prefix_of(gw)

        merged: Dict[str, str] = {}
        sources: Dict[str, str] = {}
        for rc in raw_clients:
            mac = normalize_mac(rc.mac)
            if not mac:
                continue
            merged[mac] = rc.ip or merged.get(mac, "")
            sources[mac] = rc.source or "hotspot"

        # ARP 补充同网段设备（有些设备 WinRT 不报）
        if active:
            try:
                entries = arp_cache if arp_cache is not None else \
                    {ip: mac for ip, mac in netinfo.arp_entries(prefix)}
                for ip, mac in entries.items():
                    if ip == gw:
                        continue
                    if mac not in merged or not merged[mac]:
                        merged[mac] = ip
                    sources.setdefault(mac, "arp")
            except Exception as exc:
                log.debug("ARP 补充失败：%s", exc)

        online = set(merged)
        self.online_macs = online

        result: List[DeviceInfo] = []
        with self._lock:
            for mac, ip in merged.items():
                rec = self.store.touch(mac, ip=ip)
                info = self._build(mac, ip, rec, online=True)
                info.source = sources.get(mac, "")
                if not info.hostname and resolve_names and ip:
                    info.hostname = netinfo.best_hostname(ip, use_nbt=False)
                    if not info.hostname:
                        info.hostname = netinfo.nbt_name(ip)
                    if info.hostname:
                        self.store.update(mac, hostname=info.hostname)
                        info.name = self.store.display_name(self.store.get(mac))
                result.append(info)

            # 历史设备（离线）
            for rec in self.store.all():
                mac = rec.get("mac", "")
                if not mac or mac in merged:
                    continue
                info = self._build(mac, rec.get("last_ip", ""), rec, online=False)
                result.append(info)

        result.sort(key=lambda d: (
            not d.online,
            -d.rate_down - d.rate_up,
            (d.name or d.mac).lower(),
        ))
        with self._lock:
            self._cache = {d.mac: d for d in result}
        try:
            self.traffic.set_known_macs([d.mac for d in result])
        except Exception:
            pass
        return result

    def _build(self, mac: str, ip: str, rec: Dict, online: bool) -> DeviceInfo:
        vendor = rec.get("vendor") or ""
        if not vendor:
            vendor = oui.lookup_vendor(mac)
            if vendor:
                self.store.update(mac, vendor=vendor)
        dev_type = rec.get("type") or "unknown"
        if not rec.get("type_locked"):
            guessed = oui.guess_type(mac, rec.get("hostname", ""), vendor)
            if guessed != dev_type:
                dev_type = guessed
                self.store.update(mac, type=dev_type)
        rate_down, rate_up = self.traffic.rate_of(mac)
        srx, stx = self.traffic.session_of(mac)
        trx, ttx = self.db.total_for(mac)
        info = DeviceInfo(
            mac=mac,
            ip=ip or rec.get("last_ip", ""),
            online=online,
            custom_name=rec.get("name", ""),
            hostname=rec.get("hostname", ""),
            vendor=vendor,
            dev_type=dev_type,
            icon=rec.get("icon", "auto"),
            custom_icon=rec.get("custom_icon", ""),
            note=rec.get("note", ""),
            limited=bool(rec.get("limited")),
            first_seen=float(rec.get("first_seen") or 0),
            last_seen=float(rec.get("last_seen") or 0),
            portal_accepted=float(rec.get("portal_accepted") or 0),
            rate_down=rate_down,
            rate_up=rate_up,
            session_rx=srx,
            session_tx=stx,
            total_rx=trx,
            total_tx=ttx,
        )
        info.name = self.store.display_name(rec)
        return info

    # ------------------------------------------------------------------ #
    def all(self) -> List[DeviceInfo]:
        with self._lock:
            return list(self._cache.values())

    def get(self, mac: str) -> Optional[DeviceInfo]:
        with self._lock:
            return self._cache.get(normalize_mac(mac))

    def rename(self, mac: str, name: str) -> None:
        self.store.update(mac, name=(name or "").strip())
        self.store.save()

    def set_icon(self, mac: str, icon: str, custom_path: str = "") -> None:
        """icon: 'auto' | 预设 key | 'custom'"""
        self.store.update(mac, icon=icon, custom_icon=custom_path or "")
        if icon not in ("auto", "custom"):
            self.store.update(mac, type=icon, type_locked=True)
        self.store.save()

    def set_type(self, mac: str, dev_type: str, lock: bool = True) -> None:
        self.store.update(mac, type=dev_type, type_locked=bool(lock))
        self.store.save()

    def set_note(self, mac: str, note: str) -> None:
        self.store.update(mac, note=note)
        self.store.save()

    def set_limited(self, mac: str, limited: bool) -> None:
        self.store.update(mac, limited=bool(limited))
        self.store.save()

    def forget(self, mac: str) -> None:
        self.store.delete(mac)
        with self._lock:
            self._cache.pop(normalize_mac(mac), None)

    def resolve_hostname(self, mac: str, use_nbt: bool = True) -> str:
        info = self.get(mac)
        if not info or not info.ip:
            return ""
        name = netinfo.best_hostname(info.ip, use_nbt=use_nbt)
        if name:
            self.store.update(mac, hostname=name)
        return name

    def stats_summary(self) -> Tuple[int, int, int, int]:
        """返回 (在线数, 设备总数, 本次会话总下行, 本次会话总上行)。"""
        with self._lock:
            online = sum(1 for d in self._cache.values() if d.online)
            total = len(self._cache)
            srx = sum(d.session_rx for d in self._cache.values())
            stx = sum(d.session_tx for d in self._cache.values())
        return online, total, srx, stx
