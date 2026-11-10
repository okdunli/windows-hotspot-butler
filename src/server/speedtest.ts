/**
 * 简易网速测试（对应原项目 hotspot/core/speedtest.py）：
 * 下载 Cloudflare 测速端点的已知大小文件，计算稳定下载速率。
 * 先 1MB 预热（TCP 爬坡），再 8MB 计时取速率；纯 Node fetch，不引第三方库。
 */
const URL_DOWN = 'https://speed.cloudflare.com/__down?bytes={n}';
const UA = 'WifiHotspotManager-SpeedTest/1.0';

async function download(n: number, timeout = 20.0): Promise<[number, number]> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout * 1000);
  try {
    const resp = await fetch(URL_DOWN.split('{n}').join(String(n)), {
      headers: { 'User-Agent': UA },
      signal: ctrl.signal,
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
    const t0 = performance.now();
    let got = 0;
    const reader = resp.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      got += value.byteLength;
    }
    return [(performance.now() - t0) / 1000, got];
  } finally {
    clearTimeout(timer);
  }
}

export interface SpeedResult {
  ok: boolean;
  mbps: number;
  msg: string;
}

export async function runSpeedTest(): Promise<SpeedResult> {
  try {
    // 预热：1MB，排除 TCP 慢启动
    try {
      await download(1_000_000, 15);
    } catch {
      /* ignore */
    }
    const [secs, got] = await download(8_000_000, 30);
    if (secs <= 0 || got < 1_000_000) {
      return { ok: false, mbps: 0, msg: '测速数据异常，请重试' };
    }
    const mbps = (got * 8) / secs / 1_000_000;
    return { ok: true, mbps: Math.round(mbps * 10) / 10, msg: `${mbps.toFixed(1)} Mbps` };
  } catch (exc) {
    return { ok: false, mbps: 0, msg: String(exc instanceof Error ? exc.message : exc) };
  }
}
