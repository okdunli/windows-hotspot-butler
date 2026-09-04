"""流量统计引擎。

三种数据源（自动降级）：
  1. scapy   —— 在热点网卡上抓包，按 MAC 精确统计每台设备的上行/下行（需安装 Npcap + scapy）
  2. adapter —— 读取热点网卡累计收发字节，只能给出「整体」速率与总量
  3. demo    —— 演示数据，用于没有真实热点时预览界面

统计结果实时写入 SQLite，重启后仍可查看累计用量。
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

from . import netinfo
from .storage import TrafficDB, normalize_mac

log = logging.getLogger(__name__)

# 演示模式使用的固定 MAC，方便一键清理
DEMO_CLIENTS: List[Tuple[str, str, str]] = [
    ("a8:5c:2c:11:22:33", "192.168.137.24", "iPhone-Demo"),
    ("64:b4:73:aa:bb:cc", "192.168.137.31", "Redmi-Demo"),
    ("24:a1:60:de:ad:01", "192.168.137.57", "esp32-sensor"),
    ("8c:55:4a:99:88:77", "192.168.137.12", "DESKTOP-DEMO"),
]
DEMO_MACS = [m for m, _, _ in DEMO_CLIENTS]

FLUSH_INTERVAL = 5.0      # 每 5 秒把增量落库
SAMPLE_INTERVAL = 1.0     # 每秒计算一次速率
HISTORY_LEN = 180         # 保留 3 分钟整体速率曲线


class TrafficMonitor:
    """线程安全的流量采集器。"""

    def __init__(
        self,
        db: TrafficDB,
        traffic_backend: str = "auto",
        demo_mode: bool = False,
        iface_provider: Optional[Callable[[], Optional[netinfo.Adapter]]] = None,
    ) -> None:
        self.db = db
        self.requested_backend = traffic_backend
        self.demo_mode = demo_mode
        self._iface_provider = iface_provider or netinfo.find_hotspot_adapter

        self.backend_name = "off"
        self.backend_note = "未启动"
        self.per_device = self.requested_backend != "adapter"

        self._lock = threading.RLock()
        self._acc: Dict[str, List[int]] = {}       # mac -> [rx, tx] 未落库增量
        self._session: Dict[str, List[int]] = {}   # mac -> [rx, tx] 本次运行累计
        self._rates: Dict[str, List[float]] = {}   # mac -> [down_bps, up_bps]
        self._total_rate: List[float] = [0.0, 0.0]
        self._history: Deque[Tuple[float, float, float]] = deque(maxlen=HISTORY_LEN)
        self._known: set[str] = set()

        self._prev_acc: Dict[str, List[int]] = {}
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._sniffer = None
        self._adapter_last: Optional[Tuple[int, int, float]] = None

    # ------------------------------------------------------------------ #
    #                              生命周期                              #
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self._select_backend()
        t = threading.Thread(target=self._loop, name="traffic-loop", daemon=True)
        t.start()
        self._threads.append(t)
        log.info("流量统计已启动：%s（%s）", self.backend_name, self.backend_note)

    def stop(self) -> None:
        self._stop.set()
        self._flush()
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads.clear()

    def restart(self, traffic_backend: Optional[str] = None,
                demo_mode: Optional[bool] = None) -> None:
        self.stop()
        if traffic_backend is not None:
            self.requested_backend = traffic_backend
        if demo_mode is not None:
            self.demo_mode = demo_mode
        with self._lock:
            self._rates.clear()
            self._total_rate = [0.0, 0.0]
        self.start()

    # ------------------------------------------------------------------ #
    #                              后端选择                              #
    # ------------------------------------------------------------------ #
    def _select_backend(self) -> None:
        want = self.requested_backend
        if self.demo_mode:
            self.backend_name, self.backend_note = "demo", "演示数据（非真实流量）"
            self.per_device = True
            return
        if want == "off":
            self.backend_name, self.backend_note = "off", "已关闭统计"
            self.per_device = False
            return
        if want in ("auto", "scapy"):
            ok, note = self._try_start_sniffer()
            if ok:
                self.backend_name, self.backend_note = "scapy", note
                self.per_device = True
                return
            if want == "scapy":
                self.backend_name = "adapter"
                self.backend_note = f"抓包不可用（{note}），已退回网卡总量统计"
                self.per_device = False
                return
            log.info("scapy 抓包不可用：%s", note)
        self.backend_name = "adapter"
        self.backend_note = "网卡总量统计（无法区分单台设备）"
        self.per_device = False

    def _try_start_sniffer(self) -> Tuple[bool, str]:
        try:
            from scapy.all import conf, sniff  # type: ignore
            from scapy.layers.l2 import Ether  # type: ignore
        except Exception as exc:  # ImportError / 运行时缺 Npcap
            return False, f"未安装 scapy（{type(exc).__name__}）"

        adapter = None
        try:
            adapter = self._iface_provider()
        except Exception:
            adapter = None
        if adapter is None:
            return False, "未找到热点网卡（热点未开启？）"

        target = None
        try:
            for iface in list(conf.ifaces.data.values()):
                imac = normalize_mac(str(getattr(iface, "mac", "") or ""))
                if imac and imac == adapter.mac:
                    target = iface
                    break
                iname = str(getattr(iface, "name", "") or "")
                idesc = str(getattr(iface, "description", "") or "")
                if adapter.name and adapter.name in (iname, idesc):
                    target = iface
                    break
        except Exception as exc:
            return False, f"枚举抓包网卡失败（{exc}）"
        if target is None:
            return False, f"Npcap 未识别到热点网卡 {adapter.name}"

        def _on_packet(pkt) -> None:
            try:
                if Ether not in pkt:
                    return
                eth = pkt[Ether]
                size = len(pkt)
                src = normalize_mac(eth.src)
                dst = normalize_mac(eth.dst)
                with self._lock:
                    if src:
                        self._acc.setdefault(src, [0, 0])[1] += size      # 设备上行
                    if dst and not dst.startswith(("ff:ff", "01:00:5e", "33:33")):
                        self._acc.setdefault(dst, [0, 0])[0] += size      # 设备下行
            except Exception:
                pass

        def _run() -> None:
            try:
                sniff(iface=target, prn=_on_packet, store=False,
                      stop_filter=lambda _p: self._stop.is_set())
            except Exception as exc:
                log.warning("抓包线程结束：%s", exc)
                self.backend_note = f"抓包中断：{exc}"

        th = threading.Thread(target=_run, name="traffic-sniffer", daemon=True)
        th.start()
        self._threads.append(th)
        self._sniffer = target
        return True, f"抓包统计（{getattr(target, 'name', adapter.name)}）"

    # ------------------------------------------------------------------ #
    #                              采样主循环                            #
    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        last_flush = time.time()
        while not self._stop.is_set():
            time.sleep(SAMPLE_INTERVAL)
            try:
                if self.backend_name == "demo":
                    self._sample_demo()
                elif self.backend_name == "adapter":
                    self._sample_adapter()
                self._recalc_rates()
                if time.time() - last_flush >= FLUSH_INTERVAL:
                    self._flush()
                    last_flush = time.time()
            except Exception:
                log.exception("流量采样异常")

    def _sample_demo(self) -> None:
        with self._lock:
            for mac in DEMO_MACS:
                if random.random() < 0.25:
                    continue
                down = int(random.gauss(180_000, 120_000))
                up = int(random.gauss(28_000, 20_000))
                acc = self._acc.setdefault(mac, [0, 0])
                acc[0] += max(0, down)
                acc[1] += max(0, up)

    def _sample_adapter(self) -> None:
        adapter = None
        try:
            adapter = self._iface_provider()
        except Exception:
            adapter = None
        if adapter is None or not adapter.name:
            return
        stats = netinfo.adapter_stats(adapter.name)
        if stats is None:
            return
        rx, tx, now = stats[0], stats[1], time.time()
        prev = self._adapter_last
        self._adapter_last = (rx, tx, now)
        if prev is None:
            return
        d_rx, d_tx, dt = rx - prev[0], tx - prev[1], max(0.2, now - prev[2])
        if d_rx < 0 or d_tx < 0:            # 网卡重置
            return
        with self._lock:
            # 网卡视角：ReceivedBytes 是设备上行；SentBytes 是设备下行
            acc = self._acc.setdefault("__total__", [0, 0])
            acc[0] += d_tx
            acc[1] += d_rx
            self._total_rate = [d_tx / dt, d_rx / dt]

    def _recalc_rates(self) -> None:
        """用「本轮未落库增量 - 上轮未落库增量」求瞬时速率，再做指数平滑。"""
        now = time.time()
        with self._lock:
            snapshot = {m: list(v) for m, v in self._acc.items()}
            total_down = total_up = 0.0
            for mac, cur in snapshot.items():
                old = self._prev_acc.get(mac, [0, 0])
                d_down = max(0, cur[0] - old[0])
                d_up = max(0, cur[1] - old[1])
                down_bps = d_down / SAMPLE_INTERVAL
                up_bps = d_up / SAMPLE_INTERVAL
                # 指数平滑，曲线更好看
                prev = self._rates.get(mac, [0.0, 0.0])
                self._rates[mac] = [
                    prev[0] * 0.35 + down_bps * 0.65,
                    prev[1] * 0.35 + up_bps * 0.65,
                ]
                if mac != "__total__":
                    total_down += self._rates[mac][0]
                    total_up += self._rates[mac][1]
            self._prev_acc = {m: list(v) for m, v in snapshot.items()}
            if self.backend_name == "adapter":
                total_down, total_up = self._total_rate[0], self._total_rate[1]
            else:
                self._total_rate = [total_down, total_up]
            self._history.append((now, total_down, total_up))
            # 清理长时间无流量的速率项
            for mac in list(self._rates):
                if self._rates[mac][0] < 1 and self._rates[mac][1] < 1:
                    self._rates[mac] = [0.0, 0.0]

    def _flush(self) -> None:
        """把增量写入数据库并累加到本次会话。"""
        with self._lock:
            pending = {m: v for m, v in self._acc.items() if v[0] or v[1]}
            self._acc = {}
            self._prev_acc = {}
            for mac, (rx, tx) in pending.items():
                s = self._session.setdefault(mac, [0, 0])
                s[0] += rx
                s[1] += tx
        for mac, (rx, tx) in pending.items():
            if mac == "__total__":
                continue
            try:
                self.db.add(mac, rx, tx)
            except Exception:
                log.exception("流量落库失败 %s", mac)
        if "__total__" in pending:
            rx, tx = pending["__total__"]
            try:
                self.db.add("__total__", rx, tx)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #                              查询接口                              #
    # ------------------------------------------------------------------ #
    def set_known_macs(self, macs: List[str]) -> None:
        with self._lock:
            self._known = {normalize_mac(m) for m in macs if m}

    def rate_of(self, mac: str) -> Tuple[float, float]:
        """返回 (下行 bytes/s, 上行 bytes/s)。"""
        with self._lock:
            r = self._rates.get(normalize_mac(mac))
            return (r[0], r[1]) if r else (0.0, 0.0)

    def session_of(self, mac: str) -> Tuple[int, int]:
        with self._lock:
            s = self._session.get(normalize_mac(mac))
            return (int(s[0]), int(s[1])) if s else (0, 0)

    def total_rate(self) -> Tuple[float, float]:
        with self._lock:
            return self._total_rate[0], self._total_rate[1]

    def history(self) -> List[Tuple[float, float, float]]:
        with self._lock:
            return list(self._history)

    def session_totals(self) -> Tuple[int, int]:
        with self._lock:
            rx = sum(v[0] for m, v in self._session.items() if m != "__total__")
            tx = sum(v[1] for m, v in self._session.items() if m != "__total__")
            if self.backend_name == "adapter":
                tot = self._session.get("__total__", [0, 0])
                rx, tx = int(tot[0]), int(tot[1])
        return int(rx), int(tx)

    @property
    def status_text(self) -> str:
        label = {
            "scapy": "精确抓包",
            "adapter": "网卡总量",
            "demo": "演示数据",
            "off": "已关闭",
        }.get(self.backend_name, self.backend_name)
        return f"{label} · {self.backend_note}"
