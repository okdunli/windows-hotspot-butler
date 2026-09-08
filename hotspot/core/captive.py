"""强制门户（Captive Portal）：DNS 劫持 + 80 端口门户 + 白名单放行。

为什么要有这个模块：普通「欢迎页」只是起了个 HTTP 服务，设备连上热点后
压根不会去访问它，所以看起来「欢迎页没用、连上就能上网」。真正的强制门户
必须同时做到两件事：

  1. DNS 劫持：未同意的设备做域名解析时，把结果全部指向热点网关(192.168.137.1)；
     已同意的设备正常转发到上游 DNS，这样它们就真正恢复上网。
  2. 探测路径应答：iOS / Android / Windows 连上 WiFi 后会访问固定的连通性探测地址
     （/generate_204、/hotspot-detect.html、/ncsi.txt …）。
     未同意时必须返回 302 重定向到门户页（之前返回 204，等于告诉系统"已经联网了"，
     门户自然永远不会弹出）；已同意时按各家规定的内容返回，系统才判定"已联网"。

注意：Windows 的 ICS(SharedAccess) 自带 DNS 代理会占用 UDP 53，抢占它需要
管理员权限，且会短暂中断共享上网（几秒），所以只在用户主动开启时执行。
"""
from __future__ import annotations

import json
import logging
import socket
import struct
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from .config import PortalConfig
from .paths import DATA_DIR, ensure_dirs
from .portal import (PROBE_PATHS, PortalContext, build_values,
                     render_template, template_path)
from . import pshell

log = logging.getLogger(__name__)

UPSTREAM_DNS = ("223.5.5.5", "119.29.29.29")
ACCEPT_FILE: Path = DATA_DIR / "portal_accepted.json"

# 已放行设备的探测应答（各系统约定的"已联网"内容）
_PROBE_OK = {
    "/generate_204": (204, b""),
    "/gen_204": (204, b""),
    "/hotspot-detect.html": (200, b"<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"),
    "/library/test/success.html": (200, b"<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"),
    "/ncsi.txt": (200, b"Microsoft NCSI"),
    "/connecttest.txt": (200, b"Microsoft Connect Test"),
    "/success.txt": (200, b"success"),
    "/redirect": (200, b"<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"),
}


# --------------------------------------------------------------------------- #
#                                白名单存储                                   #
# --------------------------------------------------------------------------- #
class AllowList:
    """已同意条款、放行上网的设备（按 IP + MAC 双重记录）。"""

    def __init__(self, path: Path = ACCEPT_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.ips: Set[str] = set()
        self.macs: Set[str] = set()
        self.detail: Dict[str, Dict[str, str]] = {}
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                with self._lock:
                    self.ips = {str(i) for i in (data.get("ips") or [])}
                    self.macs = {str(m).upper() for m in (data.get("macs") or [])}
                    self.detail = dict(data.get("detail") or {})
        except Exception:
            log.debug("放行名单读取失败，按空名单处理")

    def save(self) -> None:
        try:
            ensure_dirs()
            with self._lock:
                data = {
                    "ips": sorted(self.ips),
                    "macs": sorted(self.macs),
                    "detail": self.detail,
                }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("放行名单保存失败：%s", exc)

    def has(self, ip: str = "", mac: str = "") -> bool:
        with self._lock:
            if mac and mac.upper() in self.macs:
                return True
            return bool(ip) and ip in self.ips

    def allow(self, ip: str, mac: str = "", ua: str = "") -> None:
        with self._lock:
            if ip:
                self.ips.add(ip)
                self.detail[ip] = {
                    "mac": (mac or "").upper(),
                    "ua": (ua or "")[:200],
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            if mac:
                self.macs.add(mac.upper())
        self.save()

    def revoke(self, ip: str = "", mac: str = "") -> None:
        with self._lock:
            if ip:
                self.ips.discard(ip)
                self.detail.pop(ip, None)
            if mac:
                self.macs.discard(mac.upper())
        self.save()

    def clear(self) -> None:
        with self._lock:
            self.ips.clear()
            self.macs.clear()
            self.detail.clear()
        self.save()

    def snapshot(self) -> List[Dict[str, str]]:
        with self._lock:
            return [dict(v, ip=k) for k, v in self.detail.items()]


# --------------------------------------------------------------------------- #
#                                 DNS 代理                                    #
# --------------------------------------------------------------------------- #
def parse_qname(data: bytes) -> Tuple[str, int]:
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


def _a_response(data: bytes, end: int, ip: str) -> bytes:
    tid = data[:2]
    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4) + socket.inet_aton(ip)
    return (tid + struct.pack("!H", 0x8180) + struct.pack("!HHHH", 1, 1, 0, 0)
            + data[12:end + 4] + answer)


def _empty_response(data: bytes, end: int) -> bytes:
    """NOERROR 但无记录（用于 AAAA / HTTPS 等，避免设备走 IPv6 绕过门户）。"""
    tid = data[:2]
    return (tid + struct.pack("!H", 0x8180) + struct.pack("!HHHH", 1, 0, 0, 0)
            + data[12:end + 4])


class DnsProxy(threading.Thread):
    """把未放行设备的域名解析全部指向网关；放行设备正常转发上游。"""

    def __init__(self, gateway: str, allow: AllowList,
                 upstream: Tuple[str, ...] = UPSTREAM_DNS) -> None:
        super().__init__(name="captive-dns", daemon=True)
        self.gateway = gateway
        self.allow = allow
        self.upstream = upstream
        self.error = ""
        self.query_count = 0
        self.hijack_count = 0
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="dns")
        self._local_cache: Optional[Set[str]] = None
        self.on_query: Optional[Callable[[str, str], None]] = None  # (ip, domain)

    # ---- 生命周期 ----
    def bind(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", 53))
            self._sock = s
            return True
        except OSError as exc:
            self.error = f"UDP 53 绑定失败：{exc}"
            return False

    def run(self) -> None:
        if self._sock is None and not self.bind():
            log.warning(self.error)
            return
        assert self._sock is not None
        sock = self._sock
        sock.settimeout(0.5)
        log.info("DNS 劫持已启动，未放行设备将被指向 %s", self.gateway)
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            self._pool.submit(self._handle, data, addr, sock)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        self._pool.shutdown(wait=False)

    # ---- 处理 ----
    def _handle(self, data: bytes, addr, sock: socket.socket) -> None:
        src = addr[0] if addr else ""
        try:
            resp = self._resolve(data, src)
            if resp:
                sock.sendto(resp, addr)
        except Exception:
            return

    def _local_ips(self) -> Set[str]:
        """本机自身的所有 IP（含网关、回环）。来自这些地址的 DNS 查询一律转发上游，
        绝不能被劫持，否则本机会跟着断网。"""
        if self._local_cache is not None:
            return self._local_cache
        ips: Set[str] = {"127.0.0.1", "::1", "localhost"}
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                ip = info[4][0]
                if ip and ip not in ("127.0.0.1", "::1"):
                    ips.add(ip)
        except Exception:
            pass
        if self.gateway:
            ips.add(self.gateway)
        self._local_cache = ips
        return ips

    def _resolve(self, data: bytes, src: str) -> Optional[bytes]:
        if len(data) < 12:
            return None
        flags = struct.unpack("!H", data[2:4])[0]
        if flags & 0x8000:            # 不是查询
            return None
        try:
            qname, end = parse_qname(data)
            qtype, _qclass = struct.unpack("!HH", data[end:end + 4])
        except Exception:
            return None
        self.query_count += 1
        if self.on_query and qname:
            try:
                self.on_query(src, qname)
            except Exception:
                pass
        # 本机自身（网关 / 各网卡 IP / 回环）的查询绝不劫持，否则会把自己也搞断网；
        # 已放行设备正常转发；其余未放行客户端的 A 记录劫持到网关，强制弹出门户。
        if src in self._local_ips() or self.allow.has(ip=src):
            return self._forward(data)
        if qtype == 1:                # A 记录 → 劫持到网关
            self.hijack_count += 1
            return _a_response(data, end, self.gateway)
        return _empty_response(data, end)

    def _forward(self, data: bytes) -> Optional[bytes]:
        for up in self.upstream:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(1.5)
                s.sendto(data, (up, 53))
                resp, _ = s.recvfrom(2048)
                s.close()
                return resp
            except OSError:
                continue
        return None


# --------------------------------------------------------------------------- #
#                                 HTTP 门户                                   #
# --------------------------------------------------------------------------- #
class _CaptiveHandler(BaseHTTPRequestHandler):
    server_version = "CaptivePortal/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        log.debug("captive %s - %s", self.address_string(), fmt % args)

    # --- 工具 ---
    @property
    def _ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _send(self, code: int, body: bytes = b"",
              ctype: str = "text/html; charset=utf-8",
              extra: Optional[Dict[str, str]] = None) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _redirect(self, location: str) -> None:
        self._send(302, b"", extra={"Location": location})

    def _portal_page(self) -> bytes:
        srv: "CaptiveServer" = self.server           # type: ignore[assignment]
        tpl_path = template_path(srv.template_name)
        if tpl_path is None:
            tpl_path = template_path("aurora")
        if tpl_path is None:
            return b"<h1>Portal template missing</h1>"
        raw = tpl_path.read_text(encoding="utf-8", errors="replace")
        return render_template(raw, build_values(srv.cfg, srv.ctx)).encode("utf-8")

    def _password_page(self, error: str = "") -> bytes:
        """门户访问密码页（PortalConfig.access_password 非空时显示）。"""
        err = f'<p class="err">{error}</p>' if error else ""
        return (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>访问验证</title><style>"
            "body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:"
            "-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
            "background:linear-gradient(150deg,#0b1026,#1b2350);color:#eaf0ff}"
            ".card{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.16);"
            "border-radius:20px;padding:32px 28px;width:min(90vw,360px);text-align:center;"
            "backdrop-filter:blur(14px)}"
            "h1{font-size:19px;margin-bottom:8px}p{color:#9aa7d4;font-size:13px;margin-bottom:18px}"
            "input{width:100%;padding:12px 14px;border-radius:11px;border:1px solid rgba(255,255,255,.2);"
            "background:rgba(0,0,0,.25);color:#fff;font-size:15px;outline:none;margin-bottom:14px}"
            "button{width:100%;padding:13px;border:0;border-radius:12px;cursor:pointer;font-size:15px;"
            "font-weight:600;color:#fff;background:linear-gradient(135deg,#5b8cff,#7c5cff)}"
            ".err{color:#ff8a80;font-size:12.5px;margin-top:10px}"
            "</style></head><body><div class='card'>"
            "<h1>🔒 访问验证</h1><p>本网络需要访问密码，请向网络所有者获取</p>"
            "<form method='post' action='/verify'>"
            "<input type='password' name='pw' placeholder='访问密码' autofocus required>"
            "<button type='submit'>验证并继续</button></form>" + err +
            "</div></body></html>"
        ).encode("utf-8")

    def _schedule_denied_page(self) -> bytes:
        srv: "CaptiveServer" = self.server           # type: ignore[assignment]
        s, e = srv.cfg.schedule_start, srv.cfg.schedule_end
        return (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>不在上网时段</title><style>"
            "body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:"
            "-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
            "background:linear-gradient(150deg,#0b1026,#1b2350);color:#eaf0ff;text-align:center}"
            ".box{padding:40px}.emo{font-size:52px;margin-bottom:14px}"
            "h1{font-size:20px;margin-bottom:10px}p{color:#9aa7d4;font-size:14px;line-height:1.8}"
            "</style></head><body><div class='box'><div class='emo'>🌙</div>"
            f"<h1>当前不在上网时段</h1><p>本网络开放时间：{s:02d}:00 - {e % 24:02d}:00<br>"
            "请在开放时段内再连接。</p></div></body></html>"
        ).encode("utf-8")

    def _success_page(self) -> bytes:
        srv: "CaptiveServer" = self.server           # type: ignore[assignment]
        gw = srv.ctx.get_gateway() or "192.168.137.1"
        return (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>已接入</title><style>"
            "body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:"
            "-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
            "background:linear-gradient(150deg,#0b1026,#1b2350);color:#eaf0ff}"
            ".box{text-align:center;padding:40px}"
            ".tick{width:74px;height:74px;border-radius:50%;margin:0 auto 22px;display:grid;"
            "place-items:center;background:linear-gradient(135deg,#22c55e,#16a34a);"
            "box-shadow:0 12px 34px rgba(34,197,94,.4);font-size:36px;color:#fff;"
            "animation:pop .45s cubic-bezier(.2,1.4,.4,1)}"
            "@keyframes pop{from{transform:scale(.4);opacity:0}to{transform:none;opacity:1}}"
            "h1{font-size:22px;margin-bottom:10px}p{color:#9aa7d4;font-size:14px;line-height:1.8}"
            "</style></head><body><div class='box'>"
            "<div class='tick'>&#10003;</div>"
            "<h1>已成功接入网络</h1>"
            "<p>你现在可以自由上网了。<br>本页面由 WiFi 热点管理器提供。</p>"
            "</div></body></html>"
        ).encode("utf-8")

    # --- 路由 ---
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        srv: "CaptiveServer" = self.server           # type: ignore[assignment]
        ip = self._ip
        path = urlparse(self.path).path

        if path == "/favicon.ico":
            self._send(204, b"")
            return

        allowed = srv.allow.has(ip=ip) or self._mac_allowed(srv, ip)

        if path in PROBE_PATHS:
            if allowed:
                code, body = _PROBE_OK.get(path, (204, b""))
                self._send(code, body, "text/html; charset=utf-8")
            else:
                # 关键：未同意时必须重定向，系统才会弹出门户页
                self._redirect(f"http://{srv.ctx.get_gateway() or '192.168.137.1'}/?from=probe")
            return

        if allowed:
            if path in ("/", "/index.html", "/portal"):
                self._send(200, self._success_page())
            else:
                self._send(404, b"<h1>404</h1>")
            return

        # 时段限制：不在开放时间内直接拒绝（未放行设备才走到这里）
        if not srv.schedule_open():
            self._send(200, self._schedule_denied_page())
            return
        # 门户访问密码：先验密码再看欢迎页
        if srv.cfg.access_password and not srv.verified.has(ip=ip):
            self._send(200, self._password_page())
            return
        self._send(200, self._portal_page())

    def _mac_allowed(self, srv: "CaptiveServer", ip: str) -> bool:
        try:
            mac = srv.ctx.mac_of_ip(ip) if ip else ""
        except Exception:
            mac = ""
        return bool(mac) and srv.allow.has(mac=mac)

    def do_POST(self) -> None:
        srv: "CaptiveServer" = self.server           # type: ignore[assignment]
        path = urlparse(self.path).path
        ip = self._ip
        ua = str(self.headers.get("User-Agent") or "")

        # 门户访问密码验证
        if path == "/verify":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length else b""
            pw = ""
            for pair in body.decode("utf-8", "replace").split("&"):
                if pair.startswith("pw="):
                    from urllib.parse import unquote_plus
                    pw = unquote_plus(pair[3:])
                    break
            if pw and pw == srv.cfg.access_password:
                srv.verified.allow(ip)
                self._send(200, self._portal_page())
            else:
                self._send(200, self._password_page("密码不正确，请重试"))
            return

        if path not in ("/accept", "/__portal_accept"):
            self._send(404, b"<h1>404</h1>")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
        except (ValueError, OSError):
            pass
        mac = ""
        try:
            mac = srv.ctx.mac_of_ip(ip) if ip else ""
        except Exception:
            mac = ""
        # 时段限制：accept 也拒绝
        if not srv.schedule_open():
            self._send(200, self._schedule_denied_page())
            return
        srv.allow.allow(ip, mac, ua)
        try:
            if srv.on_accept:
                srv.on_accept(ip, mac, ua)
        except Exception:
            log.exception("放行回调失败")
        log.info("强制门户：%s(%s) 已同意并开始上网", ip, mac or "未知MAC")
        self._send(200, self._success_page())


class CaptiveServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: Tuple[str, int], cfg: PortalConfig, ctx: PortalContext,
                 allow: AllowList, on_accept: Optional[Callable[[str, str, str], None]] = None,
                 template_name: str = "aurora") -> None:
        super().__init__(addr, _CaptiveHandler)
        self.cfg = cfg
        self.ctx = ctx
        self.allow = allow
        self.on_accept = on_accept
        self.template_name = template_name
        self.verified = AllowList(path=DATA_DIR / "portal_verified.json")  # 已通过访问密码的 IP

    def schedule_open(self) -> bool:
        """当前是否在门户放行时段内（start==end 视为全天开放）。"""
        s, e = int(self.cfg.schedule_start), int(self.cfg.schedule_end)
        if s == e:
            return True
        h = time.localtime().tm_hour
        if s < e:
            return s <= h < e
        return h >= s or h < e          # 跨午夜（如 22:00-7:00）


# --------------------------------------------------------------------------- #
#                                  门户外壳                                   #
# --------------------------------------------------------------------------- #
def _port_busy(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def _service(action: str) -> Tuple[bool, str]:
    """启停 SharedAccess(ICS) 服务，用于让出/恢复 UDP 53。"""
    code, out, err = pshell.run(["sc", action, "SharedAccess"], timeout=25)
    ok = code == 0 or "already" in (out + err).lower()
    return ok, (out or err).strip()[:200]


class CaptivePortal:
    """强制门户总入口：DNS 劫持 + 80 端口门户 + 放行名单。"""

    def __init__(self, ctx: PortalContext,
                 on_accept: Optional[Callable[[str, str, str], None]] = None,
                 on_dns_query: Optional[Callable[[str, str], None]] = None) -> None:
        self.ctx = ctx
        self.on_accept = on_accept
        self.on_dns_query = on_dns_query
        self.allow = AllowList()
        self.cfg = PortalConfig()
        self._http: Optional[CaptiveServer] = None
        self._thread: Optional[threading.Thread] = None
        self._dns: Optional[DnsProxy] = None
        self.last_error = ""
        self.dns_note = ""
        self.port = 80

    # ---- 状态 ----
    @property
    def running(self) -> bool:
        return self._http is not None

    @property
    def dns_running(self) -> bool:
        return self._dns is not None and self._dns.error == ""

    @property
    def url(self) -> str:
        gw = self.ctx.get_gateway() or "192.168.137.1"
        return f"http://{gw}/" if self.port == 80 else f"http://{gw}:{self.port}/"

    def status(self) -> Dict[str, object]:
        return {
            "running": self.running,
            "dns": self.dns_running,
            "dns_error": (self._dns.error if self._dns else "") or self.dns_note,
            "url": self.url,
            "allowed": len(self.allow.ips),
            "queries": (self._dns.query_count if self._dns else 0),
            "hijacked": (self._dns.hijack_count if self._dns else 0),
            "error": self.last_error,
            "need_password": bool(self.cfg.access_password),
            "schedule": f"{int(self.cfg.schedule_start):02d}:00-{int(self.cfg.schedule_end) % 24:02d}:00",
            "schedule_open": (self._http.schedule_open() if self._http else True),
        }

    # ---- 控制 ----
    def start(self, cfg: Optional[PortalConfig] = None,
              force_dns: bool = False) -> Tuple[bool, str]:
        if self.running:
            return True, "强制门户已在运行"
        cfg = cfg or self.cfg
        self.cfg = cfg
        self.last_error = ""
        self.dns_note = ""
        self.port = 80

        gw = self.ctx.get_gateway() or "192.168.137.1"

        # 1) DNS 劫持（可选，但只有它才能真正弹出门户）
        if cfg.dns_redirect or force_dns:
            self._start_dns(gw)

        # 2) HTTP 门户
        try:
            srv = CaptiveServer(("0.0.0.0", self.port), cfg, self.ctx,
                                self.allow, self.on_accept,
                                template_name=cfg.template or "aurora")
        except OSError as exc:
            self.last_error = f"端口 {self.port} 无法监听：{exc}"
            return False, self.last_error
        self._http = srv
        self._thread = threading.Thread(target=srv.serve_forever,
                                        name="captive-http", daemon=True)
        self._thread.start()

        msg = f"强制门户已启动：{self.url}"
        if cfg.dns_redirect or force_dns:
            msg += "（DNS 劫持生效中）" if self.dns_running else \
                   f"（DNS 劫持未生效：{self.dns_note or '未知原因'}，需手动访问门户页）"
        return True, msg

    def _start_dns(self, gateway: str) -> None:
        dns = DnsProxy(gateway, self.allow)
        dns.on_query = self.on_dns_query
        if dns.bind():
            dns.start()
            self._dns = dns
            return
        # 53 通常被 ICS 的 DNS 代理(SharedAccess)占用：停服务 → 抢占 → 恢复
        if not pshell.is_admin():
            self.dns_note = "UDP 53 被占用，且当前非管理员，无法抢占"
            return
        log.info("UDP 53 被占用，尝试暂停 SharedAccess 以抢占 DNS")
        _service("stop")
        time.sleep(1.2)
        dns2 = DnsProxy(gateway, self.allow)
        dns2.on_query = self.on_dns_query
        if dns2.bind():
            dns2.start()
            self._dns = dns2
            _service("start")
            time.sleep(0.8)
            # 服务重启后重新确保共享生效（失败不影响门户本身）
            try:
                from . import ics
                ics.enable()
            except Exception:
                log.debug("ICS 重新启用失败（可稍后在界面上重试）")
            return
        _service("start")
        self.dns_note = "UDP 53 仍被其它程序占用，门户需手动访问"

    def stop(self) -> Tuple[bool, str]:
        if self._dns:
            self._dns.stop()
            self._dns = None
        if self._http:
            try:
                self._http.shutdown()
                self._http.server_close()
            except Exception:
                log.debug("关闭门户服务异常", exc_info=True)
            self._http = None
            self._thread = None
        return True, "强制门户已停止"

    def restart(self, cfg: PortalConfig, force_dns: bool = False) -> Tuple[bool, str]:
        self.stop()
        time.sleep(0.3)
        return self.start(cfg, force_dns=force_dns)

    # ---- 放行管理 ----
    def allow_ip(self, ip: str, mac: str = "") -> None:
        self.allow.allow(ip, mac)

    def revoke_ip(self, ip: str, mac: str = "") -> None:
        self.allow.revoke(ip, mac)

    def clear_allowed(self) -> None:
        self.allow.clear()

    def allowed_list(self) -> List[Dict[str, str]]:
        return self.allow.snapshot()
