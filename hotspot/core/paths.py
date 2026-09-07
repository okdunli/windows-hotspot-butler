"""路径与日志。所有用户数据统一存放在 %APPDATA%\\WifiHotspotManager。"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "WifiHotspotManager"
APP_TITLE = "WiFi 热点管理器"
APP_VERSION = "1.0.0"


def _base_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / f".{APP_NAME}"


DATA_DIR: Path = _base_dir()
CONFIG_FILE: Path = DATA_DIR / "config.json"
DEVICES_FILE: Path = DATA_DIR / "devices.json"
TRAFFIC_DB: Path = DATA_DIR / "traffic.sqlite3"
LOG_DIR: Path = DATA_DIR / "logs"
CUSTOM_ICON_DIR: Path = DATA_DIR / "icons"
CUSTOM_PORTAL_DIR: Path = DATA_DIR / "portal"

PKG_DIR: Path = Path(__file__).resolve().parent.parent
TEMPLATE_DIR: Path = PKG_DIR / "templates"
SCRIPT_DIR: Path = PKG_DIR / "scripts"
TETHERING_PS1: Path = SCRIPT_DIR / "tethering.ps1"
ICS_PS1: Path = SCRIPT_DIR / "ics.ps1"

_ALL_DIRS = (DATA_DIR, LOG_DIR, CUSTOM_ICON_DIR, CUSTOM_PORTAL_DIR)


def ensure_dirs() -> None:
    for d in _ALL_DIRS:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


_logging_ready = False


def setup_logging(verbose: bool = False) -> logging.Logger:
    """初始化日志：文件 + 控制台。重复调用安全。"""
    global _logging_ready
    root = logging.getLogger()
    if _logging_ready:
        return root

    ensure_dirs()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    try:
        fh = RotatingFileHandler(
            LOG_DIR / "app.log", maxBytes=1024 * 512, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)
    except OSError:
        pass

    ch = logging.StreamHandler(stream=sys.stdout)
    ch.setFormatter(fmt)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(ch)

    _logging_ready = True
    return root
