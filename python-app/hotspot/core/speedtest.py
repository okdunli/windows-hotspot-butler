"""简易网速测试：下载 Cloudflare 测速端点的已知大小文件，计算稳定下载速率。

设计（ponytail）：
  * 不引第三方库（speedtest-cli 依赖服务器列表解析，重且脆）；
  * 用 https://speed.cloudflare.com/__down?bytes=N （全球 CDN，国内一般也可达）；
  * 分两段：先 1MB 预热（TCP 爬坡），再 8MB 计时取速率；
  * 纯标准库（urllib），在线程池里跑，不阻塞界面。
"""
from __future__ import annotations

import logging
import time
import urllib.request

log = logging.getLogger(__name__)

URL_DOWN = "https://speed.cloudflare.com/__down?bytes={n}"
UA = "WifiHotspotManager-SpeedTest/1.0"


def _download(n: int, timeout: float = 20.0) -> tuple[float, int]:
    """下载 n 字节，返回 (耗时秒, 实际字节数)。"""
    req = urllib.request.Request(URL_DOWN.format(n=n), headers={"User-Agent": UA})
    t0 = time.perf_counter()
    got = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            got += len(chunk)
    return time.perf_counter() - t0, got


def run_speed_test() -> dict:
    """执行测速，返回 {ok, mbps, msg}。"""
    try:
        # 预热：1MB，排除 TCP 慢启动
        try:
            _download(1_000_000, timeout=15)
        except Exception:
            pass
        secs, got = _download(8_000_000, timeout=30)
        if secs <= 0 or got < 1_000_000:
            return {"ok": False, "mbps": 0, "msg": "测速数据异常，请重试"}
        mbps = got * 8 / secs / 1_000_000
        return {"ok": True, "mbps": round(mbps, 1),
                "msg": f"{mbps:.1f} Mbps"}
    except Exception as exc:
        log.warning("测速失败: %s", exc)
        return {"ok": False, "mbps": 0, "msg": str(exc)}
