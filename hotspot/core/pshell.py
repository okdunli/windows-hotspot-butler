"""PowerShell / 命令行调用封装（隐藏黑窗、统一编码、JSON 解析）。"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
POWERSHELL = "powershell.exe" if sys.platform == "win32" else "powershell"


def _flags() -> int:
    return CREATE_NO_WINDOW


def b64(text: str) -> str:
    return base64.b64encode((text or "").encode("utf-8")).decode("ascii")


def run(cmd: Sequence[str], timeout: float = 25.0) -> tuple[int, str, str]:
    """执行外部命令，返回 (returncode, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            timeout=timeout,
            creationflags=_flags(),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        log.warning("命令超时：%s", " ".join(cmd[:3]))
        return -1, "", "TIMEOUT"
    except FileNotFoundError:
        return -2, "", "NOT_FOUND"
    except OSError as exc:
        return -3, "", str(exc)


def run_console(cmd: Sequence[str], timeout: float = 20.0) -> str:
    """执行 netsh/arp 等本地化输出命令，自动尝试 GBK 解码。"""
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            timeout=timeout,
            creationflags=_flags(),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.debug("命令失败 %s: %s", cmd, exc)
        return ""
    raw = (proc.stdout or b"") + b"\n" + (proc.stderr or b"")
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def ps_json(command: str, timeout: float = 20.0) -> Optional[Any]:
    """执行一段 PowerShell 并把输出当 JSON 解析。"""
    full = (
        "$ErrorActionPreference='Stop';"
        "try{[Console]::OutputEncoding=[System.Text.Encoding]::UTF8}catch{};"
        + command
    )
    code, out, err = run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", full],
        timeout=timeout,
    )
    if code != 0 and not out.strip():
        log.debug("PowerShell 失败(%s)：%s", code, (err or "")[:200])
        return None
    return _parse_json(out)


def ps_script(script_path, params: Dict[str, str], timeout: float = 45.0) -> Dict[str, Any]:
    """执行 .ps1 脚本文件并解析 JSON 输出。"""
    args: List[str] = [
        POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(script_path),
    ]
    for key, value in params.items():
        args += [f"-{key}", str(value)]
    code, out, err = run(args, timeout=timeout)
    data = _parse_json(out)
    if isinstance(data, dict):
        return data
    return {
        "ok": False,
        "error": (err or out or "NO_OUTPUT").strip()[:300],
        "errorType": "TIMEOUT" if err == "TIMEOUT" else "PS_FAILED",
        "returncode": code,
    }


def _parse_json(text: str) -> Optional[Any]:
    text = (text or "").strip().lstrip("\ufeff")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 输出里可能混有警告行，尝试截取首个 JSON 片段
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    log.debug("JSON 解析失败：%s", text[:200])
    return None


def is_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """以管理员身份重新启动当前程序，成功则调用方应退出。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        params = " ".join(f'"{a}"' for a in sys.argv)
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
        return int(rc) > 32
    except Exception as exc:
        log.error("提权失败：%s", exc)
        return False
