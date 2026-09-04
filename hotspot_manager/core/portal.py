"""欢迎页（Captive Portal）服务。

- 内置 4 套预制模板：极光 / 海风 / 商务 / 极简
- 支持选择本地自定义 HTML 文件（连同同目录的 css/js/图片一起托管）
- 可选 DNS 重定向（把客户端的域名解析全部指向本机，触发系统自动弹窗）
- 访客同意记录写入 SQLite（含 IP / MAC / User-Agent）
"""
from __future__ import annotations

import html
import logging
import mimetypes
import socket
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .config import PortalConfig
from .paths import CUSTOM_PORTAL_DIR, TEMPLATE_DIR
from .storage import TrafficDB, normalize_mac

log = logging.getLogger(__name__)

TEMPLATES: List[Tuple[str, str, str]] = [
    ("aurora", "极光", "深蓝紫渐变 · 玻璃拟态"),
    ("ocean", "海风", "青绿渐变 · 清爽卡片"),
    ("corporate", "商务", "浅色专业 · 信息表格"),
    ("minimal", "极简", "黑白等宽 · 终端风格"),
    ("custom", "自定义", "使用本地 HTML 文件"),
]

# 各类系统检测网络连通性的探测路径
PROBE_PATHS = (
    "/generate_204",
    "/gen_204",
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/ncsi.txt",
    "/connecttest.txt",
    "/redirect",
    "/success.txt",
)


def list_templates() -> List[Tuple[str, str, str]]:
    return list(TEMPLATES)


def template_path(name: str) -> Optional[Path]:
    if name == "custom":
        return None
    p = TEMPLATE_DIR / f"{name}.html"
    return p if p.exists() else None


class PortalContext:
    """渲染页面需要的动态信息，由主程序提供。"""

    def __init__(
        self,
        get_ssid: Callable[[], str] = lambda: "",
        get_gateway: Callable[[], str] = lambda: "",
        get_clients: Callable[[], int] = lambda: 0,
        mac_of_ip: Callable[[str], str] = lambda ip: "",
    ) -> None:
        self.get_ssid = get_ssid
        self.get_gateway = get_gateway
        self.get_clients = get_clients
        self.mac_of_ip = mac_of_ip


def render_template(tpl: str, values: Dict[str, str]) -> str:
    """占位符替换：{{key}}。不使用 str.format，避免与 CSS 花括号冲突。"""
    out = tpl
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", val)
    return out


def _notice_li(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return "".join(f"<li>{html.escape(ln)}</li>" for ln in lines) or "<li>请合理使用网络资源。</li>"


def build_values(cfg: PortalConfig, ctx: PortalContext) -> Dict[str, str]:
    ssid = ctx.get_ssid() or cfg.title
    return {
        "ssid": html.escape(ssid),
        "title": html.escape(cfg.title.replace("{{ssid}}", ssid)),
        "subtitle": html.escape(cfg.subtitle),
        "notice": html.escape(cfg.notice or ""),
        "notice_li": _notice_li(cfg.notice),
        "button": html.escape(cfg.button or "同意并开始上网"),
        "footer": html.escape(cfg.footer or ""),
        "gateway": html.escape(ctx.get_gateway() or ""),
        "clients": str(ctx.get_clients()),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# --------------------------------------------------------------------------- #
#                              HTTP 服务                                      #
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    server_version = "HotspotPortal/1.0"
    protocol_version = "HTTP/1.1"

    # --- 基础 ---
    def log_message(self, fmt: str, *args) -> None:  # 静音默认日志
        log.debug("portal %s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/html; charset=utf-8",
              extra: Optional[Dict[str, str]] = None) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            if extra:
                for k, v in extra.items():
                    self.send_header(k, v)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_file(self, path: Path) -> bool:
        if not path.is_file():
            return False
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            body = path.read_bytes()
        except OSError:
            return False
        self._send(200, body, ctype)
        return True

    # --- 路由 ---
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        srv: "PortalServer" = self.server  # type: ignore[assignment]

        # 系统连通性探测：返回 204 让客户端认为“需要登录”
        if srv.redirect_enabled and path in PROBE_PATHS:
            self._send(204, b"")
            return
        if path in ("/favicon.ico",):
            self._send(204, b"")
            return

        if path.startswith("/assets/"):
            name = path[len("/assets/"):].lstrip("/\\")
            root = srv.assets_root
            if root and self._send_file(root / name.replace("/", "\\") if "\\" in name else root / name):
                return
            self._send(404, b"<h1>404</h1>")
            return

        if srv.template_name == "custom":
            idx = srv.custom_index
            if idx and idx.is_file():
                raw = idx.read_text(encoding="utf-8", errors="replace")
                self._send(200, render_template(raw, srv.values()).encode("utf-8"))
            else:
                self._send(500, b"<h1>Custom HTML not found</h1>")
            return

        tpl_path = template_path(srv.template_name)
        if tpl_path is None:
            tpl_path = template_path("aurora")
        if tpl_path is None:
            self._send(500, b"<h1>No template</h1>")
            return
        raw = tpl_path.read_text(encoding="utf-8", errors="replace")
        self._send(200, render_template(raw, srv.values()).encode("utf-8"))

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        srv: "PortalServer" = self.server  # type: ignore[assignment]
        if path != "/accept":
            self._send(404, b"<h1>404</h1>")
            return
        length = 0
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length:
            try:
                self.rfile.read(length)
            except OSError:
                pass
        ip = self.client_address[0] if self.client_address else ""
        ua = str(self.headers.get("User-Agent") or "")
        mac = srv.ctx.mac_of_ip(ip)
        srv.on_accept(ip, mac, ua)
        ok_html = srv.success_page()
        self._send(200, ok_html.encode("utf-8"))


class PortalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: Tuple[str, int], cfg: PortalConfig, ctx: PortalContext,
                 db: TrafficDB) -> None:
        super().__init__(addr, _Handler)
        self.cfg = cfg
        self.ctx = ctx
        self.db = db
        self.redirect_enabled = bool(cfg.dns_redirect)
        self.template_name = cfg.template
        self.custom_index: Optional[Path] = None
        self.assets_root: Optional[Path] = None

    # --- 由外部调用 ---
    def values(self) -> Dict[str, str]:
        return build_values(self.cfg, self.ctx)

    def success_page(self) -> str:
        return (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>已连接</title><style>"
            "body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:"
            "-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
            "background:linear-gradient(150deg,#0b1026,#1b2350);color:#eaf0ff}"
            ".box{text-align:center;padding:40px}"
            ".tick{width:74px;height:74px;border-radius:50%;margin:0 auto 22px;display:grid;"
            "place-items:center;background:linear-gradient(135deg,#22c55e,#16a34a);"
            "box-shadow:0 12px 34px rgba(34,197,94,.4);font-size:36px;color:#fff}"
            "h1{font-size:22px;margin-bottom:10px}p{color:#9aa7d4;font-size:14px;line-height:1.8}"
            "a{display:inline-block;margin-top:22px;color:#79c0ff;text-decoration:none;font-size:13px}"
            "</style></head><body><div class='box'>"
            "<div class='tick'>&#10003;</div>"
            "<h1>已成功接入网络</h1>"
            "<p>你现在可以关闭本页，开始使用网络。<br>本页面由 WiFi 热点管理器提供。</p>"
            "<a href='/'>返回欢迎页</a>"
            "</div></body></html>"
        )

    def on_accept(self, ip: str, mac: str, ua: str) -> None:
        try:
            self.db.record_portal_visit(ip, mac, ua, True)
        except Exception:
            log.exception("记录欢迎页同意失败")
        log.info("欢迎页：%s(%s) 已同意并接入", ip, mac or "未知MAC")


# --------------------------------------------------------------------------- #
#                              DNS 重定向                                     #
# --------------------------------------------------------------------------- #
class DnsRedirector(threading.Thread):
    """极简 DNS 服务器：所有 A 查询都回答本机地址，诱导系统弹出门户页。"""

    def __init__(self, answer_ip: str, port: int = 53) -> None:
        super().__init__(name="portal-dns", daemon=True)
        self.answer_ip = answer_ip
        self.port = port
        self._sock: Optional[socket.socket] = None
        self.error = ""

    def run(self) -> None:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self.port))
        except OSError as exc:
            self.error = f"UDP {self.port} 绑定失败：{exc}（可能被系统 DNS 服务占用）"
            log.warning(self.error)
            return
        log.info("DNS 重定向已启动，应答地址 %s", self.answer_ip)
        while True:
            try:
                data, addr = self._sock.recvfrom(512)
            except OSError:
                break
            try:
                resp = self._build_response(data)
                if resp:
                    self._sock.sendto(resp, addr)
            except Exception:
                continue

    @staticmethod
    def _parse_qname(data: bytes) -> Tuple[str, int]:
        parts: List[str] = []
        i = 12
        while i < len(data):
            ln = data[i]
            if ln == 0:
                i += 1
                break
            parts.append(data[i + 1:i + 1 + ln].decode("latin-1", "replace"))
            i += 1 + ln
        return ".".join(parts), i

    def _build_response(self, data: bytes) -> Optional[bytes]:
        if len(data) < 12:
            return None
        try:
            tid = data[:2]
            flags = struct.unpack("!H", data[2:4])[0]
            if flags & 0x8000:
                return None
            qname, end = self._parse_qname(data)
            if end + 4 > len(data):
                return None
            qtype, qclass = struct.unpack("!HH", data[end:end + 4])
            if qtype != 1:  # 只处理 A 记录
                return None
        except Exception:
            return None
        rid = struct.pack("!H", 0x8180)
        qd = struct.pack("!H", 1)
        an = struct.pack("!H", 1)
        ns = struct.pack("!H", 0)
        ar = struct.pack("!H", 0)
        question = data[12:end + 4]
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4)
        answer += socket.inet_aton(self.answer_ip)
        return tid + rid + qd + an + ns + ar + question + answer

    def stop(self) -> None:
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
#                              统一管理入口                                   #
# --------------------------------------------------------------------------- #
class PortalManager:
    def __init__(self, db: TrafficDB, ctx: PortalContext) -> None:
        self.db = db
        self.ctx = ctx
        self._server: Optional[PortalServer] = None
        self._thread: Optional[threading.Thread] = None
        self._dns: Optional[DnsRedirector] = None
        self.cfg = PortalConfig()
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return int(self.cfg.port or 8080)

    def start(self, cfg: Optional[PortalConfig] = None) -> Tuple[bool, str]:
        if self.running:
            return True, "欢迎页已在运行"
        self.cfg = cfg or self.cfg or PortalConfig()
        custom_index: Optional[Path] = None
        assets_root: Optional[Path] = None
        if cfg.template == "custom":
            p = Path(cfg.custom_html or "")
            if not p.is_file():
                return False, "未选择有效的自定义 HTML 文件"
            custom_index = p
            assets_root = p.parent
        try:
            srv = PortalServer(("0.0.0.0", self.port), cfg, self.ctx, self.db)
            srv.custom_index = custom_index
            srv.assets_root = assets_root
        except OSError as exc:
            self.last_error = str(exc)
            return False, f"端口 {self.port} 无法监听：{exc}"
        self._server = srv
        self._thread = threading.Thread(target=srv.serve_forever,
                                        name="portal-http", daemon=True)
        self._thread.start()

        if cfg.dns_redirect:
            gw = self.ctx.get_gateway() or "192.168.137.1"
            self._dns = DnsRedirector(gw)
            self._dns.start()
            time.sleep(0.3)
            dns_note = "" if not self._dns.error else f"（DNS 重定向未生效：{self._dns.error}）"
        else:
            dns_note = ""
        url = f"http://{self.ctx.get_gateway() or '192.168.137.1'}:{self.port}/"
        return True, f"欢迎页已启动：{url} {dns_note}".strip()

    def stop(self) -> Tuple[bool, str]:
        if not self.running:
            return True, "欢迎页未运行"
        try:
            self._server.shutdown()      # type: ignore[union-attr]
            self._server.server_close()  # type: ignore[union-attr]
        except Exception as exc:
            log.debug("关闭门户服务异常：%s", exc)
        self._server = None
        self._thread = None
        if self._dns:
            self._dns.stop()
            self._dns = None
        return True, "欢迎页已停止"

    def restart(self, cfg: PortalConfig) -> Tuple[bool, str]:
        self.stop()
        time.sleep(0.25)
        return self.start(cfg)

    def url(self) -> str:
        return f"http://{self.ctx.get_gateway() or '127.0.0.1'}:{self.port}/"

    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


def copy_custom_html(src: Path) -> Path:
    """把用户选择的 HTML 复制到数据目录，避免原文件被移动后失效。"""
    dst = CUSTOM_PORTAL_DIR / src.name
    try:
        CUSTOM_PORTAL_DIR.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dst.resolve():
            dst.write_bytes(src.read_bytes())
        return dst
    except OSError as exc:
        log.warning("复制自定义 HTML 失败：%s", exc)
        return src


def preview_html(cfg: PortalConfig, ctx: PortalContext) -> str:
    """给「预览」按钮使用：直接渲染出 HTML 字符串。"""
    if cfg.template == "custom":
        p = Path(cfg.custom_html or "")
        raw = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else "<h1>未选择文件</h1>"
    else:
        tpl = template_path(cfg.template) or template_path("aurora")
        raw = tpl.read_text(encoding="utf-8", errors="replace") if tpl else "<h1>模板缺失</h1>"
    return render_template(raw, build_values(cfg, ctx))
