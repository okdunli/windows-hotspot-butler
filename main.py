"""WiFi 热点管理器 —— 程序入口。

默认启动【网页界面】（PyWebView + Edge WebView2），更流畅、动画更现代。
传统 Tkinter 界面仍可通过 --tk / --legacy 启用（作为兼容回退，但较卡顿）。

用法：
    python main.py                 启动网页界面（默认，更流畅）
    python main.py --tk            启动传统 Tkinter 界面
    python main.py --selftest      仅做自检（不弹窗）
    python main.py --no-elevate    不提示提权
    python main.py --verbose       输出详细日志
    python main.py --debug         （仅网页界面）开启 WebView 调试
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import traceback
from pathlib import Path

# 保证从任意目录运行时都能 import 到包
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _check_platform() -> None:
    if sys.platform != "win32":
        print("本工具仅支持 Windows 10 / 11。")
        sys.exit(1)


def selftest() -> int:
    """不弹窗的自检：验证各核心模块能否正常工作。"""
    from hotspot.core import netinfo, portal as portal_core, pshell, wificaps
    from hotspot.core.config import AppConfig
    from hotspot.core.deviceman import DeviceManager
    from hotspot.core.hotspot import HotspotController
    from hotspot.core.oui import identify
    from hotspot.core.storage import DeviceStore, TrafficDB, normalize_mac
    from hotspot.core.paths import TEMPLATE_DIR
    from hotspot.core.traffic import TrafficMonitor

    ok = True

    def check(name: str, fn):
        nonlocal ok
        try:
            result = fn()
            print(f"  [OK]   {name}: {result}")
            return result
        except Exception as exc:
            ok = False
            print(f"  [FAIL] {name}: {exc}")
            traceback.print_exc()
            return None

    print("=" * 62)
    print("WiFi 热点管理器 — 自检")
    print("=" * 62)

    cfg = check("配置读写", lambda: AppConfig.load().hotspot.ssid) or ""
    caps = check("网卡能力探测", lambda: wificaps.probe_drivers())
    if caps and caps.primary:
        print(f"         网卡={caps.primary.driver}")
        print(f"         无线电={caps.primary.pretty_radios}  5GHz={caps.band_5_text}")
        print(f"         承载网络={'支持' if caps.hosted_supported else '不支持'}  "
              f"WPA3={'支持' if caps.wpa3_supported else '不支持'}")

    db = TrafficDB()
    store = DeviceStore()
    traffic = TrafficMonitor(db, "off", demo_mode=False)
    devman = DeviceManager(store, db, traffic)
    controller = HotspotController(AppConfig.load().hotspot)

    check("存储初始化", lambda: str(db.grand_total()))
    check("设备档案数量", lambda: len(store.all()))
    check("MAC 归一化", lambda: normalize_mac("A1B2C3D4E5F6"))
    check("厂商识别", lambda: identify("8c:55:4a:11:22:33", "DESKTOP-TEST"))

    status = check("热点状态读取", lambda: controller.snapshot()[0])
    if status:
        print(f"         状态={status.state_text} 后端={controller.backend_label} "
              f"SSID={status.ssid or '(空)'} 客户端={status.client_count}")

    real = check("实际能力矩阵", lambda: controller.capabilities(refresh=True))
    if real:
        for k in ("backend_label", "radios", "band_24", "band_5_text",
                  "max_clients_system", "admin"):
            print(f"         {k} = {real.get(k)}")

    check("网卡枚举", lambda: len(netinfo.list_adapters()))
    gw = check("热点网关", lambda: netinfo.hotspot_gateway_ip())
    if gw:
        print(f"         网关 IP={gw}  ARP 条目={len(netinfo.arp_entries())}")

    tpl_count = check("欢迎页模板", lambda: len(list(TEMPLATE_DIR.glob('*.html'))))
    if tpl_count:
        from hotspot.core.config import PortalConfig
        from hotspot.core.portal import PortalContext, preview_html

        check("模板渲染", lambda: len(preview_html(
            PortalConfig(template="aurora"),
            PortalContext(get_ssid=lambda: "TestAP", get_gateway=lambda: "192.168.137.1",
                          get_clients=lambda: 3, mac_of_ip=lambda ip: ""))))

    print("-" * 62)
    print("自检结果：" + ("全部通过" if ok else "存在失败项（见上）"))
    print("=" * 62)
    db.close()
    store.save(force=True)
    return 0 if ok else 1


def main() -> int:
    _check_platform()
    parser = argparse.ArgumentParser(description="WiFi 热点管理器")
    parser.add_argument("--selftest", action="store_true", help="仅自检，不启动界面")
    parser.add_argument("--tk", "--legacy", dest="tk", action="store_true",
                        help="使用传统 Tkinter 界面（默认是更流畅的网页界面）")
    parser.add_argument("--no-elevate", action="store_true", help="不提示提权")
    parser.add_argument("--verbose", action="store_true", help="输出详细日志")
    parser.add_argument("--debug", action="store_true",
                        help="（仅网页界面）开启 WebView 调试")
    args = parser.parse_args()

    from hotspot.core.paths import setup_logging

    setup_logging(verbose=args.verbose)
    log = logging.getLogger("main")

    if args.selftest:
        return selftest()

    if args.tk:
        return _main_tk(args, log)
    return _main_web(args, log)


def _main_web(args, log) -> int:
    """默认界面：网页（PyWebView + Edge WebView2），更流畅、动画更现代。"""
    try:
        import webview  # noqa: F401
    except ImportError:
        print("缺少 pywebview，无法启动网页界面。")
        print("  请先执行：pip install pywebview")
        print("  或改用传统界面：python main.py --tk")
        return 2

    from hotspot.webui.app import main as web_main

    web_argv = []
    if args.verbose:
        web_argv.append("--verbose")
    if args.debug:
        web_argv.append("--debug")
    return web_main(web_argv)


def _main_tk(args, log) -> int:
    """传统 Tkinter 界面（旧版，作为兼容回退）。"""
    from hotspot.core import netinfo, pshell
    from hotspot.core.config import AppConfig
    from hotspot.core.deviceman import DeviceManager
    from hotspot.core.hotspot import HotspotController
    from hotspot.core.portal import PortalContext, PortalManager
    from hotspot.core.storage import DeviceStore, TrafficDB
    from hotspot.core.traffic import TrafficMonitor

    if sys.platform == "win32" and not pshell.is_admin() and not args.no_elevate:
        print("提示：当前不是管理员，热点开关可能失败。")
        print("      右键以管理员身份运行，或在「设置」页点击「以管理员身份重启」。")

    # ---- 组装核心对象 ----
    cfg = AppConfig.load()
    store = DeviceStore()
    db = TrafficDB()
    traffic = TrafficMonitor(
        db,
        traffic_backend=cfg.traffic_backend,
        demo_mode=cfg.demo_mode,
        iface_provider=netinfo.find_hotspot_adapter,
    )
    devman = DeviceManager(store, db, traffic)
    controller = HotspotController(cfg.hotspot)
    portal_ctx = PortalContext(
        get_ssid=lambda: controller.last_status.ssid or cfg.hotspot.ssid,
        get_gateway=lambda: netinfo.hotspot_gateway_ip(),
        get_clients=lambda: sum(1 for d in devman.all() if d.online),
        mac_of_ip=lambda ip: next(
            (m for i, m in netinfo.arp_entries() if i == ip), ""),
    )
    portal = PortalManager(db, portal_ctx)
    traffic.start()

    if cfg.hotspot.auto_start and not controller.last_status.active:
        threading.Thread(target=lambda: controller.start(), daemon=True).start()
    if cfg.portal.enabled:
        threading.Thread(target=lambda: portal.start(cfg.portal), daemon=True).start()

    # ---- 启动界面 ----
    try:
        import tkinter as tk
    except ImportError:
        print("错误：当前 Python 未包含 tkinter，无法启动图形界面。")
        print("      请安装官方 Python（勾选 tcl/tk 组件）后重试。")
        return 2

    from hotspot.ui.app import AppWindow

    root = tk.Tk()
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    AppWindow(root, cfg, controller, store, db, traffic, devman, portal)
    log.info("界面已启动")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
