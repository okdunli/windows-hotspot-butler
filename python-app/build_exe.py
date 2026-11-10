"""一键打包成单个 exe（PyInstaller）。

在 **Windows** 上运行（exe 只能在目标平台打包）：

    pip install pyinstaller
    python python-app/build_exe.py                 # 产物 dist/WiFiHotspotManager/WiFiHotspotManager.exe
    python python-app/build_exe.py --onefile       # 单文件版（启动稍慢）

打包内容：
  - run.py 为入口：同进程后台线程起 server.py，主线程起桌面窗口。
  - public/（1:1 界面）与 dist/（构建前端）作为数据文件随包携带。
  - hotspot/（原版业务包）+ 它的 ui/模板/PowerShell 脚本作为 **数据文件** 原样携带。

为什么单进程：PyInstaller 打包后 sys.executable 是 exe 自身，若用
[exe, "server.py"] 重新拉起子进程会递归启动整个 app（banner 刷屏、窗口出不来）。
改成同进程线程化后，这一问题从根上消失。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent  # python-app/


def build_cmd(*, onefile: bool = False, name: str = "WiFiHotspotManager",
              icon: str = "", pathsep: str | None = None,
              windowed: bool = True) -> list[str]:
    """构造 PyInstaller 命令行。pathsep 可注入，便于单测模拟 Windows 的 ';'。"""
    sep = os.pathsep if pathsep is None else pathsep
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", name,
        # python-app/ 与 desktop/ 都进导入搜索路径：run.py 用 import 直接引用
        # server / app / rosezm / pages / styles。
        "--paths", str(HERE),
        "--paths", str(HERE / "desktop"),
        # 隐式导入：这些都是运行时 import，PyInstaller 静态分析未必跟到。
        "--hidden-import", "server",
        "--hidden-import", "windows_selftest",
        "--hidden-import", "run",
        # 原版业务包整包携带（含动态导入的子模块与 .ps1/.html 资源）。
        "--collect-all", "hotspot",
        # 数据：PowerShell 脚本 / 模板 / ui 资源。分隔符必须是平台分隔符
        # （Windows ';'，macOS/Linux ':'），否则 PyInstaller 解析错。
        "--add-data", f"{HERE / 'hotspot'}{sep}hotspot",
        # 桌面窗口加载的 1:1 原版界面（逐字节 index.html/mini.html/style.css
        # + 桥接版 app.js）。framework build 不复制 html，故直接从源码 public/ 携带。
        "--add-data", f"{HERE.parent / 'public'}{sep}public",
        # rosezm build 生成的 Web 前端（dist/）：若已构建则一并携带，
        # 使 / 与 /client.js 等 SPA 路由在打包后也可用。
    ]
    dist_dir = HERE.parent / "dist"
    if dist_dir.is_dir() and (dist_dir / "index.html").is_file():
        cmd += ["--add-data", f"{dist_dir}{sep}dist"]
    # pywebview 与桌面窗口入口（静态分析跟不到，显式声明）
    cmd += ["--hidden-import", "desktop_webview", "--hidden-import", "webview"]
    cmd.append("--onefile" if onefile else "--onedir")
    if icon:
        cmd += ["--icon", icon]
    # Windows 资源管理器「属性」里的版本号（与界面显示的一致）
    ver_file = HERE / "version_info.txt"
    if ver_file.is_file():
        cmd += ["--version-file", str(ver_file)]
    # 默认静默启动（不弹 cmd 黑窗）。运行日志在 %APPDATA%\Windows热点管理器\logs\，
    # selftest / 排障用 --console 出控制台版。run.py 顶部有 windowed 流守卫，
    # stdout/stderr 为 None 时替换成哑流，print/logging 不会闪退。
    if windowed:
        cmd.append("--windowed")
    cmd.append(str(HERE / "run.py"))
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onefile", action="store_true", help="单文件 exe（首启慢，解压到临时目录）")
    ap.add_argument("--console", action="store_true",
                    help="保留控制台窗口（排障/selftest 用；默认静默不弹黑窗）")
    ap.add_argument("--name", default="WiFiHotspotManager")
    ap.add_argument("--icon", default=str(HERE / "hotspot" / "assets" / "icon.ico"),
                    help="exe 图标；默认用原版 icon.ico（传空串跳过）")
    args = ap.parse_args()

    cmd = build_cmd(onefile=args.onefile, name=args.name, icon=args.icon,
                    windowed=not args.console)
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(HERE))


if __name__ == "__main__":
    sys.exit(main())
