"""端口转发（游戏模式）：netsh interface portproxy 的图形化封装。

竞品对标（MyPublicWiFi / Connectify 的 Port Forwarding / Game Mode）：
  * 把热点的某个端口转发到局域网内设备（如把 25565 转给游戏机的 25565）；
  * 规则持久化在 netsh（系统级，重启保留），程序里也存一份用于列表展示。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Dict, List, Tuple

from . import pshell
from .paths import DATA_DIR, ensure_dirs

log = logging.getLogger(__name__)

RULES_FILE: Path = DATA_DIR / "portproxy.json"


def _rule_name(name: str) -> str:
    return f"WHM-PORTPROXY-{name}"


class PortForwarder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.rules: Dict[str, dict] = {}
        self.load()

    # ---------- 本地存档 ----------
    def load(self) -> None:
        try:
            if RULES_FILE.exists():
                data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.rules = data
        except Exception:
            log.debug("端口转发规则读取失败")

    def save(self) -> None:
        try:
            ensure_dirs()
            RULES_FILE.write_text(
                json.dumps(self.rules, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.warning("端口转发规则保存失败")

    # ---------- 系统操作 ----------
    def add(self, name: str, listen_port: int, connect_ip: str,
            connect_port: int, proto: str = "tcp") -> Tuple[bool, str]:
        name = (name or "").strip() or f"rule-{listen_port}"
        proto = "tcp" if proto.lower() != "udp" else "udp"
        try:
            listen_port = int(listen_port)
            connect_port = int(connect_port)
        except (TypeError, ValueError):
            return False, "端口必须是数字"
        if not (1 <= listen_port <= 65535 and 1 <= connect_port <= 65535):
            return False, "端口范围 1-65535"
        if proto == "udp":
            # netsh portproxy 只支持 tcp；udp 用防火墙提示替代说明
            return False, "Windows portproxy 仅支持 TCP，UDP 请使用防火墙放行"
        args = ["netsh", "interface", "portproxy", "add", "v4tov4",
                f"listenport={listen_port}", "listenaddress=0.0.0.0",
                f"connectport={connect_port}", f"connectaddress={connect_ip}"]
        code, out, err = pshell.run(args, timeout=20)
        if code != 0:
            return False, (out or err).strip()[:200] or "添加失败（需要管理员权限）"
        # 顺手放行防火墙，避免规则加了却不通
        pshell.run(["netsh", "advfirewall", "firewall", "add", "rule",
                    f"name={_rule_name(name)}", "action=allow", "enable=yes",
                    f"localport={listen_port}", "protocol=tcp", "dir=in"],
                   timeout=20)
        with self._lock:
            self.rules[name] = {
                "listen_port": listen_port, "connect_ip": connect_ip,
                "connect_port": connect_port, "proto": proto,
            }
        self.save()
        return True, f"已添加转发：{listen_port} → {connect_ip}:{connect_port}"

    def remove(self, name: str) -> Tuple[bool, str]:
        with self._lock:
            rule = self.rules.pop(name, None)
        if rule is None:
            return False, "规则不存在"
        args = ["netsh", "interface", "portproxy", "delete", "v4tov4",
                f"listenport={rule['listen_port']}", "listenaddress=0.0.0.0"]
        code, out, err = pshell.run(args, timeout=20)
        pshell.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                    f"name={_rule_name(name)}"], timeout=20)
        self.save()
        if code != 0:
            msg = (out or err).strip()[:200]
            if "not found" not in msg.lower() and "找不到" not in msg:
                return False, msg or "删除失败（需要管理员权限）"
        return True, f"已删除转发规则 {name}"

    def list_rules(self) -> List[dict]:
        with self._lock:
            return [dict(v, name=k) for k, v in self.rules.items()]

    def clear_all(self) -> Tuple[bool, str]:
        names = list(self.rules)
        for n in names:
            self.remove(n)
        return True, f"已清空 {len(names)} 条规则"
