"""server._whm_apply_startup 单测：验证「开机自启」注册表值（Windows 专属逻辑）。

打包形态必须写 exe 自身；源码形态写 `python run.py`；关闭时 reg delete；
非 Windows 直接 no-op。用 mock 在 macOS 上模拟 win32/frozen，无需真 Windows。

    python python-app/test_startup_reg.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import server as S  # noqa: E402  导入时会挂上 _apply_start_with_windows


def _capture(enabled: bool, *, platform: str = "win32", frozen: bool | None = None,
             exe: str = "C:\\Apps\\WiFiHotspotManager.exe"):
    box: dict = {}

    def fake_run(args, **kw):
        box["args"] = list(args)
        return mock.Mock(returncode=0)

    patches = [
        mock.patch.object(sys, "platform", platform),
        mock.patch.object(sys, "executable", exe),
        mock.patch.object(S.subprocess, "run", fake_run),
    ]
    if frozen is not None:
        patches.append(mock.patch.object(sys, "frozen", frozen, create=True))
    for p in patches:
        p.start()
    try:
        S._whm_apply_startup(object(), enabled)
    finally:
        for p in reversed(patches):
            p.stop()
    return box.get("args")


def _value(args: list[str]) -> str:
    return args[args.index("/d") + 1]


def main() -> int:
    exe = "C:\\Apps\\WiFiHotspotManager.exe"

    a = _capture(True, frozen=True, exe=exe)
    assert a[:2] == ["reg", "add"], a
    v = _value(a)
    assert v == f'"{exe}"', v
    assert "run.py" not in v, v
    print("  [PASS] frozen：注册表值 = exe 自身（无 run.py）")

    a = _capture(True, frozen=False, exe="C:\\Python\\python.exe")
    v = _value(a)
    assert v.startswith('"C:\\Python\\python.exe" "'), v
    assert v.endswith('run.py"'), v
    print("  [PASS] source：注册表值 = python + run.py")

    a = _capture(False, frozen=True, exe=exe)
    assert a[:2] == ["reg", "delete"], a
    assert "/v" in a and "WifiHotspotManager" in a and "/f" in a
    assert "/d" not in a, a
    print("  [PASS] disable：reg delete（无 /d）")

    a = _capture(True, platform="darwin", frozen=False, exe=exe)
    assert a is None, a
    print("  [PASS] non-Windows：no-op（不调用 reg）")

    print("ALL 4 STARTUP-REG TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
