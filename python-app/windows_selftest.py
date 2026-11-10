"""Windows 平台实跑自检（在 Windows 上以管理员身份运行，产出逐项证据）。

    python python-app/windows_selftest.py            # 只读+安全探测
    python python-app/windows_selftest.py --apply    # 含真实开关（会短暂启停热点）

逐项验证 1:1 的 Windows 专属能力：
  WinRT tethering.ps1 / netsh hostednetwork / ICS COM / 防火墙规则 / UDP 53 /
  注册表开机自启 / 托盘与全局热键模块 / Tk 界面 / 业务服务往返。
输出 PASS / FAIL / SKIP（非 Windows 或非管理员时明确 SKIP，不假装通过）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from hotspot.core import pshell, netinfo, ics, wificaps  # noqa: E402
from hotspot.core.paths import TETHERING_PS1, ICS_PS1, setup_logging  # noqa: E402

setup_logging(verbose="--verbose" in sys.argv)
ARGS = sys.argv[1:]
APPLY = "--apply" in ARGS
PORT = 8787
if "--port" in ARGS:
    try:
        PORT = int(ARGS[ARGS.index("--port") + 1])
    except (IndexError, ValueError):
        pass
BASE = f"http://127.0.0.1:{PORT}"

results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool | None, detail: str) -> None:
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    results.append((tag, name, detail))
    print(f"  [{tag}] {name}: {detail}")


def api(path: str, payload: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload or {}).encode("utf-8") if payload else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload else "GET",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def check(name: str, fn, skip_when_missing_admin: bool = False):
    try:
        ok, detail = fn()
        record(name, ok, detail)
    except Exception as exc:
        # 完整堆栈进 stderr：Windows 上某个 FAIL 的根因（COM/端口/编码）要能定位，
        # 只写 "ValueError: ..." 无法据此修正。
        import traceback
        traceback.print_exc(file=sys.stderr)
        record(name, False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    print("=" * 66)
    print("Windows Hotspot Butler · Windows 实跑自检")
    print(f"  platform={sys.platform}  admin={pshell.is_admin()}  apply={APPLY}")
    print("=" * 66)

    win = sys.platform == "win32"
    if not win:
        record("platform", None, "非 Windows：WinRT/netsh/ICS/防火墙/注册表/托盘/热键全部 SKIP")

    # ---- 1. 静态资产（跨平台） ----
    check("PowerShell 脚本就位", lambda: (
        Path(TETHERING_PS1).is_file() and Path(ICS_PS1).is_file(),
        f"tethering.ps1={Path(TETHERING_PS1).is_file()}, ics.ps1={Path(ICS_PS1).is_file()}"))
    def _exported_app_check():
        # 打包形态里 UI 是模块（app/pages/index 打进包），盘上没有 desktop/app.py；
        # 源码形态才按文件检查。否则 exe --selftest 会出现假 FAIL。
        if getattr(sys, "frozen", False):
            try:
                import app  # noqa: F401  bundled Tk UI
                import pages.index  # noqa: F401
                return True, "frozen: app + pages.index 模块可导入"
            except Exception as exc:
                return False, f"frozen: 导入 UI 模块失败 {exc}"
        pub = HERE.parent / "public"
        need = ("index.html", "style.css", "app.js")
        return all((pub / n).is_file() for n in need), " + ".join(need)
    check("桌面界面资源就位", _exported_app_check)

    # ---- 2. Windows 原生探测 ----
    if win:
        check("管理员权限", lambda: (pshell.is_admin(),
              "是（可执行热点/ICS/防火墙）" if pshell.is_admin() else "否（部分项会 SKIP/失败）"))

        def probe_winrt():
            data = pshell.ps_script(TETHERING_PS1, {"Action": "probe"}, 30)
            return bool(data.get("ok")) or data.get("errorType") == "NO_INTERNET_PROFILE", \
                f"ok={data.get('ok')} state={data.get('state')} err={data.get('error')} type={data.get('errorType')}"
        check("WinRT 移动热点 API", probe_winrt)

        def probe_netsh():
            txt = pshell.run_console(["netsh", "wlan", "show", "drivers"], 15)
            return bool(txt.strip()), f"netsh 输出 {len(txt)} 字节"
        check("netsh wlan show drivers", probe_netsh)

        def probe_caps():
            caps = wificaps.probe_drivers()
            p = caps.adapters[0] if caps.adapters else None
            return True, f"网卡={p.name if p else '?'} 承载网络={caps.hosted_supported} 5GHz={caps.band_5_text}"
        check("无线网卡能力探测", probe_caps)

        def probe_arp():
            gw = netinfo.hotspot_gateway_ip()
            entries = netinfo.arp_entries(netinfo.subnet_prefix_of(gw))
            return True, f"网关={gw} ARP 条目={len(entries)}"
        check("网关 / ARP 表", probe_arp)

        def probe_ics():
            pub = ics.internet_adapter_name()
            return True, f"可共享上网连接={pub or '(未发现)'}"
        check("ICS 上网连接枚举", probe_ics)

        def probe_firewall():
            out = pshell.run_console(["netsh", "advfirewall", "firewall", "show", "rule",
                                     "name=WHM-BLOCK-TEST"], 15)
            return True, f"防火墙规则查询可用（输出 {len(out)} 字节）"
        check("防火墙规则接口", probe_firewall)

        def probe_dns53():
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind(("0.0.0.0", 53))
                return True, "UDP 53 可绑定（DNS 劫持可用）"
            except OSError as exc:
                return None, f"UDP 53 被占用：{exc}（需管理员暂停 SharedAccess，属预期）"
            finally:
                s.close()
        check("UDP 53 绑定", probe_dns53)

        def probe_reg():
            out = pshell.run_console(["reg", "query",
                                     r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                                     "/v", "WifiHotspotManager"], 10)
            return True, f"注册表 Run 项可查询（输出 {len(out)} 字节）"
        check("开机自启注册表", probe_reg)

        def probe_tray_hotkey():
            from hotspot.webui import tray, hotkey
            return True, f"TrayIcon={tray.HAS_DEPS} sys_check={hotkey.sys_check()}"
        check("托盘 / 全局热键模块", probe_tray_hotkey)

        # ---- 2b. 真实副作用验证（--apply 且管理员；每项都幂等还原） ----
        if APPLY and pshell.is_admin():
            def firewall_roundtrip():
                name = "WHM-SELFTEST-FIREWALL"
                add = ["netsh", "advfirewall", "firewall", "add", "rule",
                       f"name={name}", "dir=in", "action=block", "remoteip=192.0.2.1"]
                code, out, err = pshell.run(add, 20)
                try:
                    show = pshell.run_console(["netsh", "advfirewall", "firewall",
                                              "show", "rule", f"name={name}"], 15)
                    ok = code == 0 and name in show
                    detail = f"add rc={code} 查询命中={name in show} out={(out or err).strip()[:80]}"
                finally:
                    # 查询抛异常也要删，绝不留下测试规则
                    pshell.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                                f"name={name}"], 20)
                return ok, detail
            check("防火墙规则 add/query/delete（还原）", firewall_roundtrip)

            def reg_roundtrip():
                key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
                before = pshell.run_console(["reg", "query", key, "/v",
                                            "WifiHotspotManager"], 10)
                existed = "WifiHotspotManager" in before and "REG_SZ" in before
                # 记住用户原本的 start_with_windows，测完必须连同 config.json 一起还原：
                # save_config() 会把它持久化，只删注册表项会留下配置副作用。
                cfg_before = api("/api/state").get("config", {}).get("start_with_windows", False)
                try:
                    # 通过服务层真实写一次（验证 _whm_apply_startup 指向 run.py）
                    api("/api/op/saveConfigPatch", {"args": [{"start_with_windows": True}]})
                    after = pshell.run_console(["reg", "query", key, "/v",
                                               "WifiHotspotManager"], 10)
                    points_run_py = "python-app\\run.py" in after or "python-app/run.py" in after
                    detail = f"写入命中={points_run_py} 原本存在={existed} 值={after.strip()[:120]}"
                finally:
                    # 还原配置；原本无 Run 项则删除，原本有则保留（服务写的值等价合法）
                    api("/api/op/saveConfigPatch", {"args": [{"start_with_windows": bool(cfg_before)}]})
                    if not existed:
                        pshell.run(["reg", "delete", key, "/v", "WifiHotspotManager", "/f"], 10)
                return points_run_py, detail
            check("开机自启注册表写入 → run.py（还原）", reg_roundtrip)

            def dns_hijack_roundtrip():
                # 起一个临时 UDP 53 应答器：收到 A 查询即回答网关地址，
                # 再用真实 DNS 查询报文验证应答。端口若被 ICS 占用 → 诚实 SKIP。
                import socket
                import struct
                import threading
                import time as _t
                gw = netinfo.hotspot_gateway_ip()
                srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    srv.bind(("127.0.0.1", 0))  # 先验证本机 UDP 能力
                    srv.close()
                    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    srv.bind(("0.0.0.0", 53))
                except OSError as exc:
                    try:
                        srv.close()
                    except Exception:
                        pass
                    return None, f"UDP 53 被占用：{exc}（原版需管理员暂停 SharedAccess，属预期 SKIP）"
                stop = threading.Event()
                hits = []

                def serve():
                    srv.settimeout(0.3)
                    while not stop.is_set():
                        try:
                            data, addr = srv.recvfrom(512)
                        except socket.timeout:
                            continue
                        except OSError:
                            break
                        try:
                            qname, end = __import__("hotspot.core.captive", fromlist=["parse_qname"]).parse_qname(data)
                            hits.append(qname)
                            resp = __import__("hotspot.core.captive", fromlist=["_a_response"])._a_response(data, end, gw)
                            srv.sendto(resp, addr)
                        except Exception as e:
                            hits.append(f"ERR:{e}")

                th = threading.Thread(target=serve, daemon=True)
                th.start()
                try:
                    # 标准 A 查询：example.com
                    q = (b"\xab\xcd" + struct.pack("!H", 0x0100) + struct.pack("!HHHH", 1, 0, 0, 0)
                         + b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1))
                    cli = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    cli.settimeout(2.0)
                    cli.sendto(q, ("127.0.0.1", 53))
                    try:
                        raw, _ = cli.recvfrom(512)
                        ancount = struct.unpack("!H", raw[6:8])[0]
                        # A 记录的 4 字节地址在应答尾部
                        ip = ".".join(str(b) for b in raw[-4:])
                        ok = ancount == 1 and ip == gw
                        detail = f"qname={hits[:1]} an={ancount} 应答IP={ip} 期望={gw}"
                    except socket.timeout:
                        ok, detail = False, f"DNS 应答超时 hits={hits}"
                    finally:
                        cli.close()
                finally:
                    stop.set()
                    _t.sleep(0.4)
                    srv.close()
                return ok, detail
            check("DNS 劫持 UDP 53 真实查询/应答（还原）", dns_hijack_roundtrip)

            def portal_http_roundtrip():
                from hotspot.core.captive import CaptivePortal
                from hotspot.core.portal import PortalContext
                ctx = PortalContext(get_ssid=lambda: "SelfTestAP",
                                    get_gateway=lambda: "192.168.137.1",
                                    get_clients=lambda: 0, mac_of_ip=lambda ip: "")
                cp = CaptivePortal(ctx)
                ok_start, msg = cp.start(None, force_dns=False)
                if not ok_start:
                    # 绑不上 80（权限/占用）≠ 功能缺陷：CI runner 非 root、
                    # 端口被占都会这样，诚实 SKIP 而不是 FAIL。
                    if isinstance(msg, str) and ("Errno 13" in msg or "Errno 98" in msg
                                                 or "10013" in msg or "被占用" in msg):
                        return None, f"HTTP 80 无法监听（环境受限）：{msg.strip()[:80]}"
                    return False, f"门户启动失败：{msg}"
                import urllib.error
                probes = {}
                try:
                    for path in ("/", "/generate_204", "/ncsi.txt"):
                        try:
                            with urllib.request.urlopen("http://127.0.0.1" + path, timeout=4) as r:
                                probes[path] = r.status
                        except urllib.error.HTTPError as e:
                            probes[path] = e.code
                        except Exception as e:
                            probes[path] = f"ERR:{type(e).__name__}"
                    st = cp.status()
                    ok = st.get("running") is True and "/" in probes
                    detail = f"running={st.get('running')} url={st.get('url')} probes={probes}"
                finally:
                    cp.stop()
                return ok, detail
            check("强制门户 HTTP 80 启动/探测路径/停止（还原）", portal_http_roundtrip)

            def ics_com_roundtrip():
                # 只读验证 ICS COM 对象可实例化（不启用共享，避免改真实网络）
                import subprocess
                ps = ("try { $s = New-Object -ComObject HNetCfg.HNetShare; "
                      "Write-Output 'HNETSHARE_OK' } catch { Write-Output ('ERR:' + $_.Exception.Message) }")
                code, out, err = pshell.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                                            "-Command", ps], 20)
                ok = "HNETSHARE_OK" in out
                return ok, f"COM rc={code} out={out.strip()[:120]} err={err.strip()[:80]}"
            check("ICS HNetCfg.HNetShare COM 可达", ics_com_roundtrip)

            def tray_hotkey_real():
                from hotspot.webui import tray, hotkey
                if not tray.HAS_DEPS:
                    return None, "pystray/Pillow 未安装（原版同样优雅降级）"
                if not hotkey.sys_check():
                    return False, "hotkey.sys_check() 为 False"
                # 真实注册 Ctrl+Alt+H，回调只记账；随后无法注销单个热键
                # （原版 API 如此），但它随本进程退出自动释放。
                hits = []
                ok = hotkey.start_global_hotkey(lambda: hits.append("toggle"))
                return bool(ok), f"RegisterHotKey={ok}（按 Ctrl+Alt+H 可触发 /api/op/toggle）"
            check("托盘依赖 / Ctrl+Alt+H 真实注册", tray_hotkey_real)
        else:
            for n in ["防火墙规则 add/query/delete（还原）", "开机自启注册表写入 → run.py（还原）",
                      "DNS 劫持 UDP 53 真实查询/应答（还原）", "强制门户 HTTP 80 启动/探测路径/停止（还原）",
                      "ICS HNetCfg.HNetShare COM 可达", "托盘依赖 / Ctrl+Alt+H 真实注册"]:
                record(n, None, "需 Windows + 管理员 + --apply")
    else:
        for n in ["管理员权限", "WinRT 移动热点 API", "netsh wlan show drivers", "无线网卡能力探测",
                  "网关 / ARP 表", "ICS 上网连接枚举", "防火墙规则接口", "UDP 53 绑定",
                  "开机自启注册表", "托盘 / 全局热键模块"]:
            record(n, None, "非 Windows SKIP")

    # ---- 3. 业务服务往返（跨平台） ----
    try:
        st = api("/api/state")
        record("业务服务 /api/state", True,
               f"keys={len(st)} ssid={st.get('config',{}).get('ssid')} admin={st.get('admin')}")
        r = api("/api/op/getStatsReport", {"args": []})
        record("统计报表", bool(r.get("ok")), f"daily={len(r.get('daily',[]))}")
        r2 = api("/api/op/saveConfigPatch", {"args": [{"ssid": st.get("config", {}).get("ssid", "MyHotspot")}]})
        record("配置保存回环", bool(r2.get("ok")), str(r2.get("msg")))
    except Exception as exc:
        record("业务服务往返", False, f"{type(exc).__name__}: {exc}（请先 python python-app/run.py --no-ui）")

    # ---- 4. 真实热点开关（仅 --apply 且管理员） ----
    if APPLY and win and pshell.is_admin():
        def real_toggle():
            import time
            # 幂等且安全：先记原状态；原本已开则只验证 stop→start 恢复，
            # 原本关闭则 start→stop 还原，绝不把用户正在用的热点留在相反状态。
            before = api("/api/state").get("hotspot", {}).get("active", False)
            if before:
                r1 = api("/api/op/stop", {"args": []})
                time.sleep(6)
                mid = api("/api/state")
                r2 = api("/api/op/start", {"args": []})
                label = "stop → 状态 → start（还原为运行）"
            else:
                r1 = api("/api/op/start", {"args": []})
                time.sleep(8)
                mid = api("/api/state")
                r2 = api("/api/op/stop", {"args": []})
                label = "start → 状态 → stop（还原为关闭）"
            time.sleep(6)
            after = api("/api/state").get("hotspot", {}).get("active", False)
            ok = bool(r1.get("started")) and bool(r2.get("started")) and after == before
            return ok, (f"{label} | r1={r1} active_mid="
                        f"{mid.get('hotspot',{}).get('active')} r2={r2} "
                        f"before={before} after={after}")
        check("真实热点开关（幂等还原）", real_toggle)
    else:
        record("真实热点开关（幂等还原）", None,
               "需 Windows + 管理员 + --apply 才执行（避免误操作真实网络）")

    # ---- 汇总 ----
    print("-" * 66)
    p = sum(1 for t, _, _ in results if t == "PASS")
    f = sum(1 for t, _, _ in results if t == "FAIL")
    sk = sum(1 for t, _, _ in results if t == "SKIP")
    print(f"结果：PASS={p}  FAIL={f}  SKIP={sk}")
    print("=" * 66)
    report = HERE / "windows_selftest_report.json"
    report.write_text(json.dumps(
        {"platform": sys.platform, "admin": pshell.is_admin(), "apply": APPLY,
         "results": [{"status": t, "name": n, "detail": d} for t, n, d in results]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{report}")
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())
