"""配置对象与持久化（JSON，保存在 %APPDATA%）。"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict

from .paths import CONFIG_FILE, ensure_dirs

log = logging.getLogger(__name__)

SECURITY_CHOICES = [
    ("wpa2", "WPA2-个人 (推荐)"),
    ("wpa2wpa3", "WPA2/WPA3 混合"),
    ("wpa3", "WPA3-个人 (SAE)"),
    ("open", "开放 (无密码)"),
]
BAND_CHOICES = [("auto", "自动"), ("2.4", "2.4 GHz"), ("5", "5 GHz")]
BACKEND_CHOICES = [
    ("auto", "自动选择"),
    ("winrt", "移动热点 (WinRT)"),
    ("netsh", "承载网络 (netsh)"),
]
TRAFFIC_CHOICES = [
    ("auto", "自动"),
    ("scapy", "抓包精确统计 (需 Npcap)"),
    ("adapter", "网卡总量统计"),
    ("off", "关闭"),
]


@dataclass
class HotspotConfig:
    ssid: str = "MyHotspot"
    passphrase: str = "12345678"
    security: str = "wpa2"          # open | wpa2 | wpa2wpa3 | wpa3
    band: str = "auto"              # auto | 2.4 | 5
    max_clients: int = 8            # 系统只读时作为软上限告警
    backend: str = "auto"           # auto | winrt | netsh
    wps_enabled: bool = False       # WPS 一键配对（仅作提示/记录，受系统接口限制）
    auto_start: bool = False        # 程序启动后自动开启热点
    enforce_max_clients: bool = True


@dataclass
class PortalConfig:
    enabled: bool = False
    template: str = "aurora"        # aurora | ocean | corporate | minimal | custom
    custom_html: str = ""
    port: int = 8080
    dns_redirect: bool = False
    title: str = "欢迎接入 {{ssid}}"
    subtitle: str = "连接成功，请阅读下方须知后开始上网"
    notice: str = "本无线网络仅供访客临时使用，请勿从事任何违反法律法规的行为。\n如需帮助请联系网络管理员。"
    button: str = "同意并开始上网"
    footer: str = "由 WiFi 热点管理器 提供服务"
    require_accept: bool = True
    auto_open_preview: bool = True
    access_password: str = ""       # 门户访问密码（空=不需要密码）
    schedule_start: int = 0         # 放行时段开始（小时，0-23），与结束相等=全天
    schedule_end: int = 24          # 放行时段结束（小时， exclusive）


@dataclass
class AppConfig:
    theme: str = "dark"             # dark | light
    language: str = "auto"          # auto（跟随系统）| zh_CN | en
    poll_interval: float = 4.0
    traffic_backend: str = "auto"
    demo_mode: bool = False
    sidebar_expanded: bool = True
    start_with_windows: bool = False
    close_to_tray: bool = False     # 关闭窗口时最小化到托盘而非退出
    confirm_exit_hotspot: bool = True   # 退出时热点仍开着 → 弹确认
    hotkey_enabled: bool = True     # 全局热键 Ctrl+Alt+H 开关热点
    temp_password: str = ""             # 临时密码（空=未启用）
    temp_password_until: float = 0.0    # 临时密码到期时间戳
    window_geometry: str = ""
    # 迷你悬浮窗状态：show=退出时开着→下次启动自动开；x/y=记录位置（物理像素）；
    # x/y 为 -1 表示无记录（首次默认放右下角）
    mini_window: Dict[str, Any] = field(
        default_factory=lambda: {"show": False, "x": -1, "y": -1})
    hotspot: HotspotConfig = field(default_factory=HotspotConfig)
    portal: PortalConfig = field(default_factory=PortalConfig)

    # ---------- 持久化 ----------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def _coerce(dc_type, data: Dict[str, Any]):
        kwargs: Dict[str, Any] = {}
        for f in fields(dc_type):
            if f.name not in data:
                continue
            raw = data[f.name]
            tname = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", "")
            try:
                if tname.startswith("int"):
                    kwargs[f.name] = int(raw)
                elif tname.startswith("float"):
                    kwargs[f.name] = float(raw)
                elif tname.startswith("bool"):
                    kwargs[f.name] = bool(raw)
                elif tname.startswith("str"):
                    kwargs[f.name] = str(raw)
            except (TypeError, ValueError):
                continue
        return dc_type(**kwargs)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        base = {k: v for k, v in data.items() if k not in ("hotspot", "portal")}
        cfg = cls._coerce(cls, base)
        if isinstance(data.get("hotspot"), dict):
            cfg.hotspot = cls._coerce(HotspotConfig, data["hotspot"])
        if isinstance(data.get("portal"), dict):
            cfg.portal = cls._coerce(PortalConfig, data["portal"])
        # mini_window 是 dict，_coerce 处理不了，手工合并（老配置缺字段→默认值）
        mw = data.get("mini_window")
        if isinstance(mw, dict):
            try:
                cfg.mini_window = {"show": bool(mw.get("show", False)),
                                   "x": int(mw.get("x", -1)),
                                   "y": int(mw.get("y", -1))}
            except (TypeError, ValueError):
                cfg.mini_window = {"show": False, "x": -1, "y": -1}
        cfg.normalize()
        return cfg

    def normalize(self) -> None:
        h = self.hotspot
        h.ssid = (h.ssid or "MyHotspot")[:32]
        if h.security != "open" and len(h.passphrase) < 8:
            h.passphrase = (h.passphrase + "12345678")[:8]
        h.max_clients = max(1, min(64, int(h.max_clients or 8)))
        if h.security not in dict(SECURITY_CHOICES):
            h.security = "wpa2"
        if h.band not in dict(BAND_CHOICES):
            h.band = "auto"
        if h.backend not in dict(BACKEND_CHOICES):
            h.backend = "auto"
        p = self.portal
        p.port = max(1, min(65535, int(p.port or 8080)))
        if self.theme not in ("dark", "light"):
            self.theme = "dark"
        if self.language not in ("auto", "zh_CN", "en"):
            self.language = "auto"
        self.poll_interval = max(1.0, min(30.0, float(self.poll_interval or 4.0)))
        if self.traffic_backend not in dict(TRAFFIC_CHOICES):
            self.traffic_backend = "auto"
        mw = self.mini_window
        mw["x"] = int(mw.get("x", -1))
        mw["y"] = int(mw.get("y", -1))
        mw["show"] = bool(mw.get("show", False))

    @classmethod
    def load(cls) -> "AppConfig":
        try:
            if CONFIG_FILE.exists():
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                cfg = cls.from_dict(data)
                log.info("已读取配置：%s", CONFIG_FILE)
                return cfg
        except Exception as exc:  # 配置损坏不应阻塞启动
            log.warning("配置读取失败(%s)，使用默认值", exc)
        cfg = cls()
        cfg.normalize()
        return cfg

    def save(self) -> bool:
        ensure_dirs()
        self.normalize()
        try:
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(CONFIG_FILE)
            return True
        except OSError as exc:
            log.error("配置保存失败：%s", exc)
            return False
