"""build_exe.build_cmd 的单测：锁住 Windows 打包命令（无需 Windows）。

最易错的是 `--add-data` 的平台分隔符（Windows ';' / POSIX ':'）与必须携带的
hidden-import / collect-all / 入口脚本。用注入 pathsep 的方式在 macOS 上模拟 Windows。

    python python-app/test_build_cmd.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_exe as B  # noqa: E402


def _add_data(cmd: list[str]) -> list[str]:
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "--add-data"]


def _has_pair(cmd: list[str], flag: str, value: str) -> bool:
    return any(cmd[i] == flag and cmd[i + 1] == value for i in range(len(cmd) - 1))


def main() -> int:
    # 1) Windows 分隔符
    win = B.build_cmd(pathsep=";")
    add = _add_data(win)
    assert len(add) >= 2, add  # hotspot/ + public/
    assert all(";" in a for a in add), f"Windows 应使用 ';'：{add}"
    assert any(a.endswith(";hotspot") for a in add), add
    assert not any(a.endswith(";pages") for a in add), f"pages/ 属 Tk 链路，应已移除：{add}"
    # 桌面窗口的 1:1 原版界面必须随包携带
    assert any(a.endswith(";public") for a in add), f"缺 public/ 界面资源：{add}"
    assert _has_pair(win, "--hidden-import", "desktop_webview"), "缺桌面窗口入口"
    print("  [PASS] Windows --add-data 用 ';'（含 1:1 原版界面 public/）")

    # 2) POSIX 分隔符
    posix = B.build_cmd(pathsep=":")
    padd = _add_data(posix)
    assert all(":" in a for a in padd), f"POSIX 应使用 ':'：{padd}"
    assert all(";" not in a for a in padd), padd
    print("  [PASS] POSIX --add-data 用 ':'")

    # 3) 模式开关
    assert "--onedir" in win and "--onefile" not in win
    assert "--onefile" in B.build_cmd(onefile=True)
    print("  [PASS] --onedir 默认 / --onefile 可选")

    # 4) 核心携带项
    assert _has_pair(win, "--collect-all", "hotspot")
    for m in ("server", "windows_selftest", "run"):
        assert _has_pair(win, "--hidden-import", m), f"缺 hidden-import {m}"
    # Tk 降级链路已整体移除：不得再打进包里
    for m in ("desktop_native", "app", "rosezm", "styles", "pages"):
        assert not _has_pair(win, "--hidden-import", m), f"Tk 链路应已移除：{m}"
    assert _has_pair(win, "--paths", str(HERE))
    assert "--clean" in win and "--noconfirm" in win
    print("  [PASS] collect-all/hidden-import/paths/flags 完整")

    # 5) 入口脚本在最后；默认静默（--windowed），--console 可切回控制台版
    assert win[-1] == str(HERE / "run.py"), win[-1]
    assert "--windowed" in win, "默认应静默启动（不弹 cmd 黑窗）"
    assert "--windowed" not in B.build_cmd(windowed=False), "--console 应去掉 --windowed"
    # run.py 必须有 windowed 流守卫：stdout/stderr 为 None 时 print 会闪退
    run_src = (HERE / "run.py").read_text(encoding="utf-8")
    assert "_guard_windowed_streams" in run_src and "_NullStream" in run_src, \
        "缺 windowed 哑流守卫（--windowed 下 print 会 AttributeError）"
    print("  [PASS] 入口 run.py；默认 --windowed 静默 + 哑流守卫；--console 可切回")

    # 6) name / icon 透传
    c = B.build_cmd(name="Foo", icon="x.ico")
    assert _has_pair(c, "--name", "Foo") and _has_pair(c, "--icon", "x.ico")
    print("  [PASS] --name / --icon 透传")

    # 7) 框架走 npm 包（rosezm@0.2.2），本地开发可 ROSEZM_FRAMEWORK 覆盖；
    #    打包不依赖 Web 预览版 dist/：BuildExe.bat 跳过前端也不阻断 exe。
    bat = (HERE.parent / "BuildExe.bat").read_text(encoding="utf-8")
    shim = (HERE.parent / "rosezm-cli.mjs").read_text(encoding="utf-8")
    pkg = (HERE.parent / "package.json").read_text(encoding="utf-8")
    assert '"rosezm": "0.2.2"' in pkg, "框架应从 npm 安装 rosezm@0.2.2"
    assert "node_modules/rosezm/dist-cli/rosezm.mjs" in shim, "应回落到 npm 包的 CLI"
    assert "ROSEZM_FRAMEWORK" in shim, "本地开发应可指向自己的框架 checkout"
    assert "vendor/rosezm" not in shim, "不得再引用已删除的 vendor 框架副本"
    assert not (HERE.parent / "vendor").is_dir(), "vendor/ 应已删除"
    assert "WHM_OPTIONAL_BUILD" in bat and "WHM_OPTIONAL_BUILD" in shim
    assert "node rosezm-cli.mjs build . || goto :failed" not in bat, "前端构建不得阻断 exe 打包"
    assert "ROSEZM_CLI" in shim, "应支持显式指定单点 CLI 路径"
    be = (HERE / "build_exe.py").read_text(encoding="utf-8")
    assert 'if dist_dir.is_dir() and (dist_dir / "index.html").is_file():' in be, \
        "dist/ 必须是可选携带（没有就不带）"
    add_no_dist = [a for a in _add_data(B.build_cmd(pathsep=";")) if a.endswith(";dist")]
    assert len(add_no_dist) <= 1, f"dist 最多带一份：{add_no_dist}"
    print("  [PASS] npm rosezm@0.2.2 / 可选 dist / exe 打包不阻断")

    # 8) 版本号一致性：界面徽标 / 服务层 / package.json / exe 属性四处必须同号
    import json as _json
    import re as _re
    from collections import namedtuple as _nt
    srv_src = (HERE / "server.py").read_text(encoding="utf-8")
    m = _re.search(r'PORT_VERSION\s*=\s*"([^"]+)"', srv_src)
    assert m, "server.py 缺 PORT_VERSION"
    ver = m.group(1)
    assert _json.loads((HERE.parent / "package.json").read_text(encoding="utf-8"))["version"] == ver
    enh = (HERE.parent / "public" / "enhance.js").read_text(encoding="utf-8")
    assert f'window.__WHM_VERSION || "{ver}"' in enh, f"enhance.js fallback 版本不同步：{ver}"
    # exe 的 Windows 属性版本（version_info.txt）也要同号
    vfile = HERE / "version_info.txt"
    assert vfile.is_file(), "缺 version_info.txt（exe 属性版本）"
    ns = {"namedtuple": _nt}
    exec(
        "FixedFileInfo=namedtuple('F','filevers prodvers mask flags OS fileType subtype date');"
        "StringStruct=namedtuple('S','key value');"
        "StringTable=namedtuple('T','langID value');"
        "StringFileInfo=namedtuple('FI','value');"
        "VarStruct=namedtuple('V','key value');"
        "VarFileInfo=namedtuple('VF','value');"
        "VSVersionInfo=namedtuple('VSI','ffi kids')", ns)
    obj = eval(vfile.read_text(encoding="utf-8-sig"), ns)
    pairs = {s.key: s.value for s in obj.kids[0].value[0].value}
    assert pairs["FileVersion"] == ver and pairs["ProductVersion"] == ver, \
        f"exe 属性版本不同步：{pairs} vs {ver}"
    assert obj.ffi.filevers[:3] == tuple(int(x) for x in ver.split(".")[:3]), obj.ffi.filevers
    assert _has_pair(B.build_cmd(pathsep=";"), "--version-file", str(vfile)), "未接入 --version-file"
    print(f"  [PASS] 版本号一致：{ver}（package.json / server / enhance.js / exe 属性）")

    print("ALL 7 BUILD-CMD TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
