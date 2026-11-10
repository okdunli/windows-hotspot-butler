"""Windows 批处理文件的换行/编码回归。

cmd.exe 遇到 LF-only 的 .bat 会把行拆碎，典型报错是
    'tler' 不是内部或外部命令
（来自 "Windows Hotspot Butler" 这类行的残片）。曾经把 BuildExe.bat
写回成 LF 导致整包编译不了，这里锁死：所有 .bat 必须纯 ASCII + CRLF，
且每个 goto 的 label 都存在。

    python python-app/test_bat_files.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAT_FILES = ("BuildExe.bat", "Run.bat", "WindowsEvidence.bat")


def main() -> int:
    for name in BAT_FILES:
        p = ROOT / name
        assert p.is_file(), f"缺 {name}"
        b = p.read_bytes()
        # 1) 不能有任何裸 LF（每个 \n 都必须是 \r\n）
        assert b.count(b"\n") == b.count(b"\r\n"), \
            f"{name}: 含裸 LF（cmd.exe 会拆行）；请转 CRLF"
        # 2) 纯 ASCII：chcp 65001 之下中文注释仍可能触发解析问题
        assert all(c < 128 for c in b), f"{name}: 含非 ASCII 字节"
        lines = b.decode("ascii").splitlines()
        # 3) goto 的 label 都存在
        labels = {l.strip()[1:].lower() for l in lines
                  if re.match(r"^\s*:[A-Za-z]", l)}
        gotos = {m.group(1).lower() for l in lines
                 for m in re.finditer(r"goto\s+:?([A-Za-z_]\w*)", l, re.I)}
        assert not (gotos - labels), f"{name}: 缺 label {gotos - labels}"
        print(f"  [PASS] {name}: CRLF / ASCII / {len(labels)} labels OK")
    # 4) .gitattributes 锁住 eol，防以后又被编辑器改回 LF
    ga = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in ga, "缺 .gitattributes 的 CRLF 规则"
    print("  [PASS] .gitattributes: *.bat text eol=crlf")
    print("ALL BAT-FILE TESTS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
