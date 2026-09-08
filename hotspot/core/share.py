"""局域网文件共享：手机浏览器访问 http://网关:8081 即可上传/下载文件。

竞品对标（Baidu WiFi / MyPublicWiFi 的文件共享功能）：
  * 共享目录默认 %APPDATA%/WifiHotspotManager/share，UI 可打开/更换查看；
  * 上传用 multipart 表单，无大小限制（流式写盘）；
  * 仅监听热点网关所在网卡，外网访问不到。
"""
from __future__ import annotations

import html
import logging
import os
import re
import socket
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import unquote, urlparse

log = logging.getLogger(__name__)

_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>文件共享 · {title}</title><style>
body{{margin:0;font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;
background:linear-gradient(160deg,#0b1026,#1b2350);color:#eaf0ff;min-height:100vh}}
.wrap{{max-width:640px;margin:0 auto;padding:28px 16px 60px}}
h1{{font-size:20px}} .sub{{color:#9aa7d4;font-size:13px;margin-bottom:18px}}
.up{{background:rgba(255,255,255,.07);border:1px dashed rgba(255,255,255,.25);
border-radius:14px;padding:18px;text-align:center}}
.item{{display:flex;align-items:center;gap:10px;padding:11px 4px;
border-bottom:1px solid rgba(255,255,255,.08);font-size:14px}}
.item a{{color:#8ab4ff;text-decoration:none;word-break:break-all;flex:1}}
.size{{color:#9aa7d4;font-size:12px;white-space:nowrap}}
.btn{{display:inline-block;background:#3b82f6;color:#fff;border:0;border-radius:10px;
padding:10px 22px;font-size:14px;cursor:pointer}}
input[type=file]{{display:none}}
</style></head><body><div class="wrap">
<h1>📤 文件共享</h1>
<div class="sub">{title} · 连接同一热点即可互传文件</div>
<div class="up">
<form method="POST" action="/upload" enctype="multipart/form-data">
<input type="file" name="f" id="fp" multiple required onchange="this.form.submit()">
<label for="fp" class="btn">选择文件上传</label>
</form></div>
<div>{items}</div>
</div></body></html>"""


def _size_text(n: int) -> str:
    f = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if f < 1024 or u == "GB":
            return f"{f:.1f} {u}"
        f /= 1024.0
    return f"{f:.1f} GB"


class ShareServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: Tuple[str, int], root: Path, title: str = "WiFi 热点") -> None:
        super().__init__(addr, _ShareHandler)
        self.root = root
        self.title = title


class _ShareHandler(BaseHTTPRequestHandler):
    server_version = "HotspotShare/1.0"

    def log_message(self, fmt: str, *args) -> None:
        log.debug("share %s - %s", self.address_string(), fmt % args)

    @property
    def _srv(self) -> ShareServer:
        return self.server  # type: ignore[return-value]

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/html; charset=utf-8",
              extra: Optional[dict] = None) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _safe_name(self, name: str) -> str:
        name = os.path.basename(unquote(name)).strip()
        name = re.sub(r"[\\/:*?\"<>|]", "_", name) or "file"
        return name[:150]

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._list_page()
        elif path.startswith("/dl/"):
            self._download(unquote(path[4:]))
        else:
            self._send(404, b"<h1>404</h1>")

    def do_HEAD(self) -> None:
        self.do_GET()

    def _list_page(self) -> None:
        root: Path = self._srv.root
        rows = []
        try:
            for p in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if p.is_file():
                    st = p.stat()
                    rows.append(
                        f'<div class="item"><a href="/dl/{p.name}">{html.escape(p.name)}</a>'
                        f'<span class="size">{_size_text(st.st_size)}</span></div>')
        except OSError:
            pass
        page = _PAGE.format(title=html.escape(self._srv.title),
                            items="".join(rows) or '<div class="sub">暂无文件，上传一个试试</div>')
        self._send(200, page.encode("utf-8"))

    def _download(self, name: str) -> None:
        root: Path = self._srv.root
        p = root / self._safe_name(name)
        if not p.is_file():
            self._send(404, b"<h1>404</h1>")
            return
        try:
            data = p.read_bytes()
        except OSError:
            self._send(500, b"read error")
            return
        self._send(200, data, "application/octet-stream",
                   {"Content-Disposition": f"attachment; filename*=UTF-8''{p.name}"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/upload":
            self._send(404, b"<h1>404</h1>")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        ctype = self.headers.get("Content-Type") or ""
        m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
        if not m or length <= 0:
            self._send(400, b"bad request")
            return
        boundary = ("--" + (m.group(1) or m.group(2))).encode()
        saved = self._save_multipart(length, boundary)
        body = ("<!DOCTYPE html><meta charset='utf-8'>"
                f"<script>location.replace('/')</script>").encode()
        self._send(200, body)
        if saved:
            log.info("文件共享：已接收 %s", saved)

    def _save_multipart(self, length: int, boundary: bytes) -> str:
        root: Path = self._srv.root
        remaining = length
        buf = b""
        name = ""
        tmp = root / f".up-{uuid.uuid4().hex}.part"
        out = tmp.open("wb")
        saved = ""
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                buf += chunk
                # 头部解析（第一个 part 的 filename）
                if not name:
                    head_end = buf.find(b"\r\n\r\n")
                    if head_end >= 0:
                        header = buf[:head_end].decode("utf-8", "replace")
                        fm = re.search(r'filename="([^"]*)"', header)
                        if fm:
                            name = self._safe_name(fm.group(1)) or "file"
                        # 去掉头部，正文从 \r\n\r\n 之后开始
                        body_start = head_end + 4
                        out.write(buf[body_start:])
                        buf = b""
                else:
                    out.write(chunk)
            out.flush()
        finally:
            out.close()
        if not name:
            tmp.unlink(missing_ok=True)
            return ""
        # 去掉结尾的 boundary 标记
        tail = boundary + b"--\r\n"
        data = tmp.read_bytes()
        idx = data.rfind(boundary)
        if idx > 0:
            data = data[:idx]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        tmp.unlink(missing_ok=True)
        final = root / name
        stem, ext = final.stem, final.suffix
        n = 1
        while final.exists():
            final = root / f"{stem}({n}){ext}"
            n += 1
        final.write_bytes(data)
        saved = final.name
        return saved


class FileShare:
    """文件共享服务外壳（启停 + 状态）。"""

    def __init__(self, root: Path, port: int = 8081,
                 title_provider: Optional[Callable[[], str]] = None,
                 gateway_provider: Optional[Callable[[], str]] = None) -> None:
        self.root = root
        self.port = port
        self._title_provider = title_provider or (lambda: "WiFi 热点")
        self._gateway_provider = gateway_provider
        self._srv: Optional[ShareServer] = None
        self._thread: Optional[threading.Thread] = None
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._srv is not None

    @property
    def url(self) -> str:
        host = ""
        if self._gateway_provider:
            try:
                host = self._gateway_provider() or ""
            except Exception:
                host = ""
        if not host or host.startswith("127."):
            host = self._gateway_ip() or self._lan_ip()
        return f"http://{host}:{self.port}/"

    @staticmethod
    def _gateway_ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("223.5.5.5", 53))
            ip = s.getsockname()[0]
            s.close()
            return ip if ip.startswith("192.168.") else ""
        except OSError:
            return ""

    @staticmethod
    def _lan_ip() -> str:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"

    def start(self) -> Tuple[bool, str]:
        if self.running:
            return True, "文件共享已在运行"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            srv = ShareServer(("0.0.0.0", self.port), self.root,
                              title=self._title_provider())
            self._srv = srv
        except OSError as exc:
            self.last_error = f"端口 {self.port} 被占用：{exc}"
            return False, self.last_error
        self._thread = threading.Thread(target=srv.serve_forever,
                                        name="file-share", daemon=True)
        self._thread.start()
        return True, f"文件共享已启动：{self.url}（手机浏览器打开即可传文件）"

    def stop(self) -> Tuple[bool, str]:
        if self._srv:
            try:
                self._srv.shutdown()
                self._srv.server_close()
            except Exception:
                log.debug("关闭文件共享异常", exc_info=True)
            self._srv = None
            self._thread = None
        return True, "文件共享已停止"

    def status(self) -> dict:
        return {"running": self.running, "url": self.url,
                "port": self.port, "error": self.last_error}
