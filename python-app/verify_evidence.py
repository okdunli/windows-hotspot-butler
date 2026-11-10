"""校验 Windows 回传的实跑证据，并把每个 FAIL/SKIP 映射到排查位置。

    python python-app/verify_evidence.py [路径或目录]

默认读 python-app/windows_selftest_report.json 与 python-app/desktop_probe_report.json；
传入 evidence\ 目录时同时读 evidence\selftest_apply_report.json。
判定（四项全满足才算「真实 Windows 实跑证据有效」）：
  1. platform == "win32"
  2. admin is True
  3. apply is True
  4. 至少 20 条结果且 FAIL == 0
退出码：0 = 有效；1 = 无效/缺文件/有 FAIL；2 = 证据来自非 Windows（明确区分）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 每个检查项 → 出问题时该看的源码位置
WHERE: dict[str, str] = {
    "管理员权限": "hotspot/core/pshell.py::is_admin（需以管理员身份运行）",
    "WinRT 移动热点 API": "hotspot/scripts/tethering.ps1 / core/hotspot.py::WinRTBackend（PowerShell 5.1 + WinRT）",
    "netsh wlan show drivers": "core/wificaps.py::probe_drivers / netsh 输出本地化解析",
    "无线网卡能力探测": "core/wificaps.py（HOSTED/SOFT AP/WPA3 正则）",
    "网关 / ARP 表": "core/netinfo.py::hotspot_gateway_ip / arp_entries",
    "ICS 上网连接枚举": "core/ics.py::internet_adapter_name（Get-NetConnectionProfile）",
    "ICS HNetCfg.HNetShare COM 可达": "core/ics.py + scripts/ics.ps1（COM 注册/权限）",
    "防火墙规则接口": "core/hotspot.py / webui/backend.py::block_device",
    "防火墙规则 add/query/delete（还原）": "webui/backend.py::block_device / _rule_name（netsh advfirewall）",
    "UDP 53 绑定": "core/captive.py::DnsProxy.bind / SharedAccess 占用",
    "DNS 劫持 UDP 53 真实查询/应答（还原）": "core/captive.py::parse_qname / _a_response / _empty_response",
    "强制门户 HTTP 80 启动/探测路径/停止（还原）": "core/captive.py::CaptivePortal / startCaptiveServer / PROBE_PATHS",
    "开机自启注册表": "python-app/server.py::_whm_apply_startup（HKCU Run）",
    "开机自启注册表写入 → run.py（还原）": "python-app/server.py::_whm_apply_startup 指向 run.py",
    "托盘 / 全局热键模块": "hotspot/webui/tray.py（pystray+Pillow）/ hotkey.py（Win32）",
    "托盘依赖 / Ctrl+Alt+H 真实注册": "python-app/desktop_webview.py::TrayIcon / hotkey.py::start_global_hotkey",
    "业务服务 /api/state": "python-app/server.py::do_GET / HotspotBackend.get_state",
    "统计报表": "python-app/server.py / HotspotBackend.get_stats_report",
    "配置保存回环": "python-app/server.py / HotspotBackend.save_config",
    "真实热点开关（幂等还原）": "core/hotspot.py::HotspotController.start/stop + webui/backend.py::start/stop",
    "PowerShell 脚本就位": "python-app/hotspot/scripts/*.ps1 是否随项目复制",
}

# 桌面界面探测的独立项（run.py --probe-ui 写 desktop_probe_report.json）
WHERE.update({
    "界面资源齐备": "public/index.html + style.css + app.js（+ enhance.*）",
    "窗口标题（托盘 FindWindow 匹配）": "public/index.html 的 <title> / desktop_webview.py::create_window(title=...)",
    "标题栏按钮（主题/设置/统计/工具/门户）": "public/index.html 的 .tb-right",
    "迷你浮窗": "public/mini.html（file:// 加载，第二个 pywebview 窗口）",
})


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  [BAD] 无法读取 {path}: {exc}")
        return None


def inspect(label: str, d: dict | None) -> tuple[int, int, int]:
    if d is None:
        return 0, 0, 0
    rows = d.get("results") or []
    p = sum(1 for r in rows if r.get("status") == "PASS")
    f = sum(1 for r in rows if r.get("status") == "FAIL")
    s = sum(1 for r in rows if r.get("status") == "SKIP")
    print(f"  {label}: platform={d.get('platform')} admin={d.get('admin')} "
          f"apply={d.get('apply')} | PASS={p} FAIL={f} SKIP={s}")
    for r in rows:
        if r.get("status") in ("FAIL", "SKIP"):
            name = r.get("name", "?")
            print(f"    [{r['status']}] {name}: {str(r.get('detail',''))[:160]}")
            if r["status"] == "FAIL":
                print(f"            → 查：{WHERE.get(name, 'python-app/windows_selftest.py 对应 check()')}")
    return p, f, s


def main(argv: list[str]) -> int:
    print("=" * 70)
    print("Windows 实跑证据校验器")
    print("=" * 70)

    target = Path(argv[1]).resolve() if len(argv) > 1 else None
    win_path = tk_path = None
    if target and target.suffix == ".zip":
        # 一个 zip = 用户从 Windows 贴回的全部证据；解到临时目录再校验。
        import tempfile
        import zipfile
        if not zipfile.is_zipfile(target):
            print(f"  [BAD] {target} 不是有效的 zip")
            return 1
        tmp = Path(tempfile.mkdtemp(prefix="whm-evidence-"))
        with zipfile.ZipFile(target) as z:
            z.extractall(tmp)
        # zip 里可能带时间戳目录或 evidence/ 前缀，两种都找
        cands = sorted(tmp.rglob("selftest_apply_report.json")) or \
            sorted(tmp.rglob("windows_selftest_report.json"))
        win_path = cands[0] if cands else None
        tk = sorted(tmp.rglob("desktop_probe_report.json"))
        tk_path = tk[0] if tk else None
        ex = sorted(tmp.rglob("exe_smoke.json"))
        exe_path = ex[0] if ex else None
        print(f"  zip 解包：{win_path} / {tk_path} / {exe_path}")
    elif target and target.is_dir():
        win_path = target / "selftest_apply_report.json"
        if not win_path.is_file():
            win_path = target / "windows_selftest_report.json"
        tk_path = target / "desktop_probe_report.json"
        exe_path = target / "exe_smoke.json"
        if not exe_path.is_file():
            exe_path = None
    elif target and target.is_file():
        win_path = target
        tk_path = target.parent / "desktop_probe_report.json"
        exe_path = target.parent / "exe_smoke.json"
        if not exe_path.is_file():
            exe_path = None
    else:
        # 无参：读 python-app\ 下的报告（Windows 上用户常把 zip 放进项目根）
        win_path = HERE / "windows_selftest_report.json"
        tk_path = HERE / "desktop_probe_report.json"
        exe_path = ROOT / "evidence" / "exe_smoke.json"
        if not exe_path.is_file():
            exe_path = None

    d = load(win_path)
    t = load(tk_path)
    if d is None:
        print("\n结论：INVALID（缺 windows_selftest_report.json）")
        return 1

    print("\n[1] Windows 自检报告")
    p, f, s = inspect(win_path.name, d)
    print("\n[2] 桌面界面探测报告")
    inspect(tk_path.name, t)

    platform = d.get("platform")
    admin = d.get("admin") is True
    apply_ = d.get("apply") is True
    enough = (p + f + s) >= 20

    print("\n[3] exe 黑盒验收（打包产物；未打包则缺失）")
    ex = load(exe_path) if exe_path else None
    exe_bad = False
    if ex is None:
        print("  （缺 exe_smoke.json —— 未运行 BuildExe.bat 或它的 smoke 步骤）")
    else:
        print(f"  verdict={ex.get('verdict')}  exe={ex.get('exe')}")
        for c in ex.get("checks", []):
            print(f"    [{c.get('status')}] {c.get('name')}: {str(c.get('detail',''))[:120]}")
        exe_bad = ex.get("verdict") != "PASS"

    print("\n[4] 判定")
    print(f"    platform == win32 : {platform == 'win32'}")
    print(f"    admin    is True  : {admin}")
    print(f"    apply    is True  : {apply_}")
    print(f"    >= 20 项结果      : {enough} (实际 {p+f+s})")
    print(f"    FAIL == 0         : {f == 0}")
    print(f"    exe smoke PASS    : {'缺失(未打包)' if ex is None else (not exe_bad)}")

    if platform != "win32":
        print("\n结论：NOT_WINDOWS —— 这份报告来自非 Windows 环境，不能作为 1:1 实跑证据。")
        print("      请在 Windows 10/11 以管理员身份运行 WindowsEvidence.bat。")
        return 2
    if not (admin and apply_):
        print("\n结论：INCOMPLETE —— 需管理员权限且带 --apply（真实热点开关）。")
        return 1
    if not enough:
        print("\n结论：INCOMPLETE —— 自检项不足 20，可能用了旧版脚本。")
        return 1
    if f:
        print(f"\n结论：FAILED —— {f} 个 FAIL，按上面每条的「查」定位修正。")
        return 1
    if exe_bad:
        print("\n结论：FAILED —— exe 黑盒验收未通过（递归/窗口/后端），见 [3]。")
        return 1
    print("\n结论：VALID —— 真实 Windows 管理员实跑证据有效，FAIL=0"
          + ("，exe 黑盒验收通过。" if ex is not None else "（未附 exe 验收）。"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
