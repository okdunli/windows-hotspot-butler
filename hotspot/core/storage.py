"""本地存储：设备档案(JSON) + 流量库(SQLite)。程序重启后可继续读取。"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .paths import DEVICES_FILE, TRAFFIC_DB, ensure_dirs

log = logging.getLogger(__name__)


def normalize_mac(mac: str) -> str:
    """统一成 aa:bb:cc:dd:ee:ff。"""
    if not mac:
        return ""
    hexs = "".join(c for c in str(mac).lower() if c in "0123456789abcdef")
    if len(hexs) < 12:
        return str(mac).lower().strip()
    hexs = hexs[:12]
    return ":".join(hexs[i:i + 2] for i in range(0, 12, 2))


def human_bytes(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.2f} {unit}" if n < 100 else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def human_rate(bps: float) -> str:
    return human_bytes(bps) + "/s"


# --------------------------------------------------------------------------- #
#                                设备档案                                     #
# --------------------------------------------------------------------------- #
DEVICE_DEFAULTS: Dict[str, Any] = {
    "mac": "",
    "name": "",              # 用户自定义名称
    "icon": "auto",          # auto | 预设图标 key | custom
    "custom_icon": "",       # 自定义图片路径
    "type": "unknown",       # 自动识别或用户指定的设备类型
    "type_locked": False,    # 用户手动指定过类型
    "vendor": "",
    "hostname": "",
    "note": "",
    "last_ip": "",
    "first_seen": 0.0,
    "last_seen": 0.0,
    "limited": False,        # 标记为受限设备
    "portal_accepted": 0.0,
    "seen_count": 0,
}


class DeviceStore:
    """设备名称 / 图标 / 备注等元数据，落地为 devices.json。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self.load()

    # ---------- IO ----------
    def load(self) -> None:
        with self._lock:
            try:
                if DEVICES_FILE.exists():
                    raw = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        for mac, rec in raw.items():
                            if not isinstance(rec, dict):
                                continue
                            item = dict(DEVICE_DEFAULTS)
                            item.update(rec)
                            item["mac"] = normalize_mac(mac)
                            self._data[item["mac"]] = item
                    log.info("已读取 %d 条设备档案", len(self._data))
            except Exception as exc:
                log.warning("设备档案读取失败：%s", exc)

    def save(self, force: bool = False) -> bool:
        with self._lock:
            if not (self._dirty or force):
                return True
            ensure_dirs()
            try:
                tmp = DEVICES_FILE.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                tmp.replace(DEVICES_FILE)
                self._dirty = False
                return True
            except OSError as exc:
                log.error("设备档案保存失败：%s", exc)
                return False

    # ---------- 读写 ----------
    def get(self, mac: str) -> Dict[str, Any]:
        mac = normalize_mac(mac)
        with self._lock:
            rec = self._data.get(mac)
            if rec is None:
                rec = dict(DEVICE_DEFAULTS)
                rec["mac"] = mac
                rec["first_seen"] = time.time()
                self._data[mac] = rec
                self._dirty = True
            return dict(rec)

    def exists(self, mac: str) -> bool:
        with self._lock:
            return normalize_mac(mac) in self._data

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._data.values()]

    def update(self, mac: str, **kw: Any) -> Dict[str, Any]:
        mac = normalize_mac(mac)
        with self._lock:
            rec = self._data.get(mac)
            if rec is None:
                rec = dict(DEVICE_DEFAULTS)
                rec["mac"] = mac
                rec["first_seen"] = time.time()
                self._data[mac] = rec
            for k, v in kw.items():
                if k in DEVICE_DEFAULTS and rec.get(k) != v:
                    rec[k] = v
                    self._dirty = True
            return dict(rec)

    def touch(
        self,
        mac: str,
        ip: str = "",
        hostname: str = "",
        vendor: str = "",
        dev_type: str = "",
    ) -> Dict[str, Any]:
        """设备被发现时刷新在线信息（不覆盖用户手工设置）。"""
        mac = normalize_mac(mac)
        now = time.time()
        with self._lock:
            rec = self._data.get(mac)
            new = rec is None
            if new:
                rec = dict(DEVICE_DEFAULTS)
                rec["mac"] = mac
                rec["first_seen"] = now
                self._data[mac] = rec
            rec["last_seen"] = now
            rec["seen_count"] = int(rec.get("seen_count") or 0) + (1 if new else 0)
            if ip:
                rec["last_ip"] = ip
            if hostname and not rec.get("hostname"):
                rec["hostname"] = hostname
            if vendor and not rec.get("vendor"):
                rec["vendor"] = vendor
            if dev_type and not rec.get("type_locked"):
                rec["type"] = dev_type
            self._dirty = True
            return dict(rec)

    def delete(self, mac: str) -> None:
        with self._lock:
            if self._data.pop(normalize_mac(mac), None) is not None:
                self._dirty = True
                self.save(force=True)

    def display_name(self, rec: Dict[str, Any]) -> str:
        if rec.get("name"):
            return str(rec["name"])
        if rec.get("hostname"):
            return str(rec["hostname"])
        if rec.get("vendor"):
            mac = rec.get("mac", "")
            return f"{rec['vendor']} {mac[-5:].replace(':', '')}"
        return rec.get("mac", "未知设备")


# --------------------------------------------------------------------------- #
#                                流量库                                       #
# --------------------------------------------------------------------------- #
class TrafficDB:
    """累计用量 / 每日用量 / 会话 / 欢迎页访问记录。"""

    def __init__(self) -> None:
        ensure_dirs()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(TRAFFIC_DB), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS totals(
                    mac TEXT PRIMARY KEY,
                    rx INTEGER NOT NULL DEFAULT 0,
                    tx INTEGER NOT NULL DEFAULT 0,
                    updated REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS daily(
                    mac TEXT NOT NULL,
                    day TEXT NOT NULL,
                    rx INTEGER NOT NULL DEFAULT 0,
                    tx INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(mac, day)
                );
                CREATE TABLE IF NOT EXISTS sessions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mac TEXT NOT NULL,
                    started REAL NOT NULL,
                    ended REAL,
                    rx INTEGER NOT NULL DEFAULT 0,
                    tx INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS portal_visits(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    ip TEXT, mac TEXT, ua TEXT,
                    accepted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS dns_queries(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    ip TEXT, mac TEXT, domain TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_daily_day ON daily(day);
                """
            )
            self._conn.commit()

    # ---------- 写 ----------
    def add(self, mac: str, rx: int, tx: int, ts: Optional[float] = None) -> None:
        """累加一次流量增量（rx=下行/设备接收，tx=上行/设备发送）。"""
        rx, tx = int(max(0, rx)), int(max(0, tx))
        if not mac or (rx == 0 and tx == 0):
            return
        mac = normalize_mac(mac)
        ts = ts or time.time()
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO totals(mac, rx, tx, updated) VALUES(?,?,?,?) "
                "ON CONFLICT(mac) DO UPDATE SET rx=rx+excluded.rx, tx=tx+excluded.tx, "
                "updated=excluded.updated",
                (mac, rx, tx, ts),
            )
            cur.execute(
                "INSERT INTO daily(mac, day, rx, tx) VALUES(?,?,?,?) "
                "ON CONFLICT(mac, day) DO UPDATE SET rx=rx+excluded.rx, tx=tx+excluded.tx",
                (mac, day, rx, tx),
            )
            self._conn.commit()

    def add_batch(self, items: Dict[str, Tuple[int, int]]) -> None:
        for mac, (rx, tx) in items.items():
            self.add(mac, rx, tx)

    def open_session(self, mac: str) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO sessions(mac, started, rx, tx) VALUES(?,?,0,0)",
                (normalize_mac(mac), time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def close_session(self, sid: int, rx: int, tx: int) -> None:
        if not sid:
            return
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE sessions SET ended=?, rx=?, tx=? WHERE id=?",
                (time.time(), int(rx), int(tx), int(sid)),
            )
            self._conn.commit()

    def record_portal_visit(self, ip: str, mac: str, ua: str, accepted: bool) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO portal_visits(ts, ip, mac, ua, accepted) VALUES(?,?,?,?,?)",
                (time.time(), ip, normalize_mac(mac), (ua or "")[:220], 1 if accepted else 0),
            )
            self._conn.commit()

    # ---------- 读 ----------
    def total_for(self, mac: str) -> Tuple[int, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT rx, tx FROM totals WHERE mac=?", (normalize_mac(mac),)
            ).fetchone()
        return (int(row["rx"]), int(row["tx"])) if row else (0, 0)

    def totals_map(self) -> Dict[str, Tuple[int, int]]:
        with self._lock:
            rows = self._conn.execute("SELECT mac, rx, tx FROM totals").fetchall()
        return {r["mac"]: (int(r["rx"]), int(r["tx"])) for r in rows}

    def grand_total(self) -> Tuple[int, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(rx),0) rx, COALESCE(SUM(tx),0) tx FROM totals"
            ).fetchone()
        return (int(row["rx"]), int(row["tx"])) if row else (0, 0)

    def daily(self, mac: str, days: int = 14) -> List[Tuple[str, int, int]]:
        """返回最近 N 天（含今天，缺失补 0）的 (日期, rx, tx)。"""
        mac = normalize_mac(mac)
        start = datetime.now().date() - timedelta(days=days - 1)
        with self._lock:
            rows = self._conn.execute(
                "SELECT day, rx, tx FROM daily WHERE mac=? AND day>=? ORDER BY day",
                (mac, start.strftime("%Y-%m-%d")),
            ).fetchall()
        got = {r["day"]: (int(r["rx"]), int(r["tx"])) for r in rows}
        out: List[Tuple[str, int, int]] = []
        for i in range(days):
            d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            rx, tx = got.get(d, (0, 0))
            out.append((d, rx, tx))
        return out

    def daily_all(self, days: int = 14) -> List[Tuple[str, int, int]]:
        """全部设备按日汇总的 (日期, rx, tx)，缺失补 0。"""
        start = datetime.now().date() - timedelta(days=days - 1)
        with self._lock:
            rows = self._conn.execute(
                "SELECT day, SUM(rx) rx, SUM(tx) tx FROM daily WHERE day>=? GROUP BY day ORDER BY day",
                (start.strftime("%Y-%m-%d"),),
            ).fetchall()
        got = {r["day"]: (int(r["rx"]), int(r["tx"])) for r in rows}
        out: List[Tuple[str, int, int]] = []
        for i in range(days):
            d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            rx, tx = got.get(d, (0, 0))
            out.append((d, rx, tx))
        return out

    def top_devices(self, limit: int = 8) -> List[Dict[str, Any]]:
        """用量 TOP 设备（按累计 rx+tx 降序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT mac, rx, tx FROM totals ORDER BY (rx+tx) DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [{"mac": r["mac"], "rx": int(r["rx"]), "tx": int(r["tx"])} for r in rows]

    def sessions(self, mac: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT started, ended, rx, tx FROM sessions WHERE mac=? "
                "ORDER BY started DESC LIMIT ?",
                (normalize_mac(mac), int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def portal_visits(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, ip, mac, ua, accepted FROM portal_visits ORDER BY ts DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_dns_query(self, ip: str, mac: str, domain: str) -> None:
        if not domain:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO dns_queries(ts, ip, mac, domain) VALUES(?,?,?,?)",
                (time.time(), ip or "", normalize_mac(mac), domain[:120]),
            )
            # 简单滚动清理：只保留最近 5000 条
            self._conn.execute(
                "DELETE FROM dns_queries WHERE id NOT IN "
                "(SELECT id FROM dns_queries ORDER BY id DESC LIMIT 5000)"
            )
            self._conn.commit()

    def dns_queries(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, ip, mac, domain FROM dns_queries "
                "ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    def top_domains(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT domain, COUNT(*) c FROM dns_queries GROUP BY domain "
                "ORDER BY c DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    def reset_device(self, mac: str) -> None:
        mac = normalize_mac(mac)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM totals WHERE mac=?", (mac,))
            cur.execute("DELETE FROM daily WHERE mac=?", (mac,))
            cur.execute("DELETE FROM sessions WHERE mac=?", (mac,))
            cur.execute("DELETE FROM dns_queries WHERE mac=?", (mac,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass
