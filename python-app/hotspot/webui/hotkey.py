"""全局热键：Ctrl+Alt+H 开关热点。

Win32 RegisterHotKey + 独立 daemon 线程跑 GetMessage 循环。
注册失败（冲突/被占用）只打日志，不影响主程序。
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_CONTROL, MOD_ALT = 0x0002, 0x0001
VK_H = 0x48
HOTKEY_ID = 0xB00B


def start_global_hotkey(on_toggle) -> bool:
    """注册 Ctrl+Alt+H；触发时在热键线程调用 on_toggle()。返回是否注册成功。"""
    if sys_check() is False:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_H):
        log.warning("全局热键 Ctrl+Alt+H 注册失败（可能被其他程序占用）")
        return False

    msg = wintypes.MSG()

    def loop() -> None:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                try:
                    on_toggle()
                except Exception:
                    log.exception("热键回调异常")
        # 消息循环退出时注销
        user32.UnregisterHotKey(None, HOTKEY_ID)

    threading.Thread(target=loop, name="whm-hotkey", daemon=True).start()
    log.info("全局热键已注册：Ctrl+Alt+H 开关热点")
    return True


def sys_check() -> bool:
    try:
        import sys as _s
        return _s.platform == "win32"
    except Exception:
        return False
