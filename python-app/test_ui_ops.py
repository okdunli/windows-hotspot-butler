"""UI ↔ 后端 op 契约测试：UI 调用的每个 `/api/op/<name>` 都必须有对应 backend 方法。

桌面窗口加载的是 1:1 原版界面 `public/app.js`，它通过 `api("<name>")` /
`fetch("/api/op/<name>")` 调后端。若前后端漂移导致某个 op 不存在，那个按钮
点下去会 404。这里静态交叉核对：
UI 里出现的 op 名 -> `server.ALIASES` 映射 -> `HotspotBackend.<method>` 是否存在。

    python python-app/test_ui_ops.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import server  # noqa: E402
from hotspot.webui.backend import HotspotBackend  # noqa: E402

SRC = HERE.parent / "public" / "app.js"
# op(("a" if x else "b"), ...) 这类动态分支里的 op 名（静态字面量扫描抓不到）
DYNAMIC = {"unblockDevice", "blockDevice", "portalRevoke", "portalAllow",
           "shareStart", "shareStop"}
# 正则可能从 dict 取值里误抓的假阳性
FALSE_POS = {"allowed", "blocked"}


def main() -> int:
    src = SRC.read_text(encoding="utf-8")
    # 原版 app.js 的桥：api("<name>") / fetch("/api/op/<name>")，
    # 以及 NAME_MAP（snake -> camel 的等价映射表）的两侧都要算作 UI 用到的 op。
    literals = set(re.findall(r'api\(\s*["\']([A-Za-z_]+)["\']', src))
    literals |= set(re.findall(r'/api/op/["\']([A-Za-z_]+)["\']', src))
    literals |= set(re.findall(r'call\(\s*["\']([A-Za-z_]+)["\']', src))
    # NAME_MAP 只取 camelCase 的值（snake -> camel），并排除主题/级别类普通词
    # 只扫 NAME_MAP 那个块（snake -> camel 的等价映射表）：
    # key 可能不带引号（toggle: 'toggle'），值一定带引号。
    mm = re.search(r'const NAME_MAP\s*=\s*\{(.*?)\};', src, re.S)
    assert mm, "未在 UI 中找到 NAME_MAP 映射表"
    pairs = re.findall(r'(?:["\']|^|\s)([A-Za-z_]+)["\']?\s*:\s*["\']([A-Za-z_]+)["\']',
                       mm.group(1), re.M)
    not_ops = {"dark", "light", "ok", "warn", "error", "success", "info"}
    for snake, camel in pairs:
        if camel in not_ops:
            continue
        literals.add(snake)
        literals.add(camel)
    ops = (literals | DYNAMIC) - FALSE_POS
    assert ops, "未在 UI 中找到任何 op() 调用"

    methods = set(dir(HotspotBackend))
    missing = []
    for n in sorted(ops):
        m = server.ALIASES.get(n, n)
        if m is None or m not in methods:
            missing.append(f"{n}->{m}")
    assert not missing, f"UI 调用但后端不存在的 op：{missing}"
    print(f"  [PASS] {len(ops)} 个 UI op 均映射到 backend 方法")

    for must in ("toggle", "saveConfigPatch", "getStatsReport",
                 "portalStart", "portalStop", "pfAdd", "pfRemove",
                 "setTheme", "setLanguage", "wifiQrcode"):
        assert must in ops, f"UI 应调用关键 op：{must}"
    print("  [PASS] 关键 op 齐全（开关/配置/统计/门户/端口转发/主题/语言/二维码）")

    print("ALL UI-OP CONTRACT TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
