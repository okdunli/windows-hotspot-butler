/**
 * 流量统计引擎（对应原项目 hotspot/core/traffic.py）。
 * 三种数据源（自动降级）：scapy 抓包（Node 侧为 cap 包，可选）→ 网卡总量 → 演示数据。
 * 统计结果定期写入 SQLite，重启后仍可查看累计用量。
 */
import * as netinfo from './netinfo.ts';
import { normalizeMac } from './util.ts';
import type { TrafficDB } from './storage.ts';
import { getLogger } from './paths.ts';

const log = getLogger('traffic');

/** 演示模式使用的固定 MAC，方便一键清理 */
export const DEMO_CLIENTS: [string, string, string][] = [
  ['8e:8f:82:3f:74:d4', '192.168.137.24', 'iPhone-Demo'],
  ['92:ba:de:5e:48:66', '192.168.137.31', 'Redmi-Demo'],
  ['92:33:82:23:dc:78', '192.168.137.57', 'esp32-sensor'],
  ['d2:c0:8f:d0:6f:20', '192.168.137.12', 'DESKTOP-DEMO'],
];
const DEMO_MACS = DEMO_CLIENTS.map(([m]) => m);

const FLUSH_INTERVAL = 5.0; // 每 5 秒把增量落库
const SAMPLE_INTERVAL = 1.0; // 每秒计算一次速率
const HISTORY_LEN = 180; // 保留 3 分钟整体速率曲线

export type TrafficBackend = 'scapy' | 'adapter' | 'demo' | 'off';

interface HistoryPoint {
  t: number;
  down: number;
  up: number;
}

export class TrafficMonitor {
  db: TrafficDB;
  requestedBackend: string;
  demoMode: boolean;
  private ifaceProvider: () => Promise<netinfo.Adapter | null>;

  backendName: TrafficBackend | 'off' = 'off';
  backendNote = '未启动';
  perDevice: boolean;

  private acc: Map<string, [number, number]> = new Map(); // mac -> [rx, tx] 未落库增量
  private session: Map<string, [number, number]> = new Map(); // mac -> [rx, tx] 本次运行累计
  private rates: Map<string, [number, number]> = new Map(); // mac -> [down_bps, up_bps]
  private totalRate: [number, number] = [0, 0];
  private history: HistoryPoint[] = [];
  private known: Set<string> = new Set();

  private prevAcc: Map<string, [number, number]> = new Map();
  private stopFlag = false;
  private timer: ReturnType<typeof setInterval> | null = null;
  private adapterLast: [number, number, number] | null = null;
  private starting = false;

  constructor(
    db: TrafficDB,
    opts: { trafficBackend?: string; demoMode?: boolean; ifaceProvider?: () => Promise<netinfo.Adapter | null> } = {},
  ) {
    this.db = db;
    this.requestedBackend = opts.trafficBackend ?? 'auto';
    this.demoMode = Boolean(opts.demoMode);
    this.ifaceProvider = opts.ifaceProvider ?? netinfo.findHotspotAdapter;
    this.perDevice = this.requestedBackend !== 'adapter';
  }

  // ----------------------------- 生命周期 ----------------------------- //
  async start(): Promise<void> {
    if (this.timer || this.starting) return;
    this.starting = true;
    this.stopFlag = false;
    await this.selectBackend();
    this.timer = setInterval(() => {
      void this.loopTick();
    }, SAMPLE_INTERVAL * 1000);
    log.info(`流量统计已启动：${this.backendName}（${this.backendNote}）`);
    this.starting = false;
  }

  async stop(): Promise<void> {
    this.stopFlag = true;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    await this.flush();
  }

  async restart(trafficBackend?: string, demoMode?: boolean): Promise<void> {
    await this.stop();
    if (trafficBackend !== undefined) this.requestedBackend = trafficBackend;
    if (demoMode !== undefined) this.demoMode = demoMode;
    this.rates.clear();
    this.totalRate = [0, 0];
    await this.start();
  }

  // ----------------------------- 后端选择 ----------------------------- //
  private async selectBackend(): Promise<void> {
    const want = this.requestedBackend;
    if (this.demoMode) {
      this.backendName = 'demo';
      this.backendNote = '演示数据（非真实流量）';
      this.perDevice = true;
      return;
    }
    if (want === 'off') {
      this.backendName = 'off';
      this.backendNote = '已关闭统计';
      this.perDevice = false;
      return;
    }
    if (want === 'auto' || want === 'scapy') {
      // Node 版抓包：可选依赖 `cap`（Npcap）。未安装则自动降级。
      const [ok, note] = await this.tryStartSniffer();
      if (ok) {
        this.backendName = 'scapy';
        this.backendNote = note;
        this.perDevice = true;
        return;
      }
      if (want === 'scapy') {
        this.backendName = 'adapter';
        this.backendNote = `抓包不可用（${note}），已退回网卡总量统计`;
        this.perDevice = false;
        return;
      }
      log.info(`抓包不可用：${note}`);
    }
    this.backendName = 'adapter';
    this.backendNote = '网卡总量统计（无法区分单台设备）';
    this.perDevice = false;
  }

  /** 尝试启动抓包（可选依赖：npm i cap + Npcap）。 */
  private async tryStartSniffer(): Promise<[boolean, string]> {
    interface CapModule {
      deviceList(): { name: string; mac?: string | null; description?: string | null }[];
      Session: new (iface: string, opts: { filter?: string }) => {
        next(buf: Buffer): Promise<number>;
        close?(): void;
      };
    }
    let Cap: CapModule | null = null;
    try {
      // 可选依赖，未安装则自动降级到网卡总量统计
      Cap = (await import('cap' as string)) as unknown as CapModule;
    } catch (exc) {
      return [false, `未安装 cap 包（${(exc as Error)?.name ?? 'ImportError'}）`];
    }
    let adapter: netinfo.Adapter | null = null;
    try {
      adapter = await this.ifaceProvider();
    } catch {
      adapter = null;
    }
    if (adapter === null) return [false, '未找到热点网卡（热点未开启？）'];

    try {
      const devices = Cap!.deviceList() as { name: string; mac?: string | null; description?: string | null }[];
      let target: string | null = null;
      const amac = normalizeMac(adapter.mac);
      for (const d of devices) {
        const dmac = normalizeMac(String(d.mac ?? ''));
        if (amac && dmac && dmac === amac) {
          target = d.name;
          break;
        }
        if (adapter.name && (d.name === adapter.name || (d.description ?? '') === adapter.name)) {
          target = d.name;
          break;
        }
      }
      if (!target) return [false, `Npcap 未识别到热点网卡 ${adapter.name}`];

      const session = new Cap.Session(target, { filter: 'ip' });
      void (async () => {
        try {
          const buf = Buffer.alloc(65535);
          while (!this.stopFlag) {
            const n = await session.next(buf);
            if (n <= 0) {
              await new Promise((r) => setTimeout(r, 10));
              continue;
            }
            // 以太网帧：目的 MAC[0:6] 源 MAC[6:12]，ethertype[12:14]
            if (buf.length < 14) continue;
            const src = normalizeMac(buf.subarray(6, 12).toString('hex'));
            const dst = normalizeMac(buf.subarray(0, 6).toString('hex'));
            const size = n;
            if (src) {
              const a = this.acc.get(src) ?? [0, 0];
              a[1] += size; // 设备上行
              this.acc.set(src, a);
            }
            if (dst && !dst.startsWith('ff:ff') && !dst.startsWith('01:00:5e') && !dst.startsWith('33:33')) {
              const a = this.acc.get(dst) ?? [0, 0];
              a[0] += size; // 设备下行
              this.acc.set(dst, a);
            }
          }
        } catch (exc) {
          log.warn(`抓包线程结束：${String(exc)}`);
          this.backendNote = `抓包中断：${String(exc)}`;
        }
      })();
      this.backendNote = `抓包统计（${target}）`;
      return [true, this.backendNote];
    } catch (exc) {
      return [false, `抓包启动失败（${String(exc)}）`];
    }
  }

  // ----------------------------- 采样主循环 ----------------------------- //
  private async loopTick(): Promise<void> {
    try {
      if (this.backendName === 'demo') this.sampleDemo();
      else if (this.backendName === 'adapter') await this.sampleAdapter();
      this.recalcRates();
      if (this.acc.size && (this.acc.get('__flush__')?.[0] ?? 0)) {
        /* noop - flush 由时间驱动 */
      }
    } catch (exc) {
      log.exception('流量采样异常', exc);
    }
    if (this.stopFlag) return;
  }

  /** 5 秒落库计时（由外部 timer 或 start 内嵌调用）。 */
  flushTimer: ReturnType<typeof setInterval> | null = null;

  startFlushTimer(): void {
    if (this.flushTimer) return;
    this.flushTimer = setInterval(() => {
      void this.flush().catch((e) => log.exception('自动落库失败', e));
    }, FLUSH_INTERVAL * 1000);
  }

  stopFlushTimer(): void {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }
  }

  private sampleDemo(): void {
    for (const mac of DEMO_MACS) {
      if (Math.random() < 0.25) continue;
      const down = Math.trunc(gauss(180_000, 120_000));
      const up = Math.trunc(gauss(28_000, 20_000));
      const a = this.acc.get(mac) ?? [0, 0];
      a[0] += Math.max(0, down);
      a[1] += Math.max(0, up);
      this.acc.set(mac, a);
    }
  }

  private async sampleAdapter(): Promise<void> {
    let adapter: netinfo.Adapter | null = null;
    try {
      adapter = await this.ifaceProvider();
    } catch {
      adapter = null;
    }
    if (adapter === null || !adapter.name) return;
    const stats = await netinfo.adapterStats(adapter.name);
    if (stats === null) return;
    const [rx, tx] = stats;
    const now = Date.now() / 1000;
    const prev = this.adapterLast;
    this.adapterLast = [rx, tx, now];
    if (prev === null) return;
    const dRx = rx - prev[0];
    const dTx = tx - prev[1];
    const dt = Math.max(0.2, now - prev[2]);
    if (dRx < 0 || dTx < 0) return; // 网卡重置
    // 网卡视角：ReceivedBytes 是设备上行；SentBytes 是设备下行
    const acc = this.acc.get('__total__') ?? [0, 0];
    acc[0] += dTx;
    acc[1] += dRx;
    this.acc.set('__total__', acc);
    this.totalRate = [dTx / dt, dRx / dt];
  }

  private recalcRates(): void {
    const now = Date.now() / 1000;
    let totalDown = 0;
    let totalUp = 0;
    for (const [mac, cur] of Array.from(this.acc.entries())) {
      const old = this.prevAcc.get(mac) ?? [0, 0];
      const dDown = Math.max(0, cur[0] - old[0]);
      const dUp = Math.max(0, cur[1] - old[1]);
      const downBps = dDown / SAMPLE_INTERVAL;
      const upBps = dUp / SAMPLE_INTERVAL;
      const prev = this.rates.get(mac) ?? [0, 0];
      // 指数平滑，曲线更好看
      this.rates.set(mac, [prev[0] * 0.35 + downBps * 0.65, prev[1] * 0.35 + upBps * 0.65]);
      if (mac !== '__total__') {
        totalDown += this.rates.get(mac)![0];
        totalUp += this.rates.get(mac)![1];
      }
    }
    this.prevAcc = new Map(Array.from(this.acc.entries()).map(([m, v]) => [m, [...v] as [number, number]]));
    if (this.backendName === 'adapter') {
      // totalRate 已由 sampleAdapter 直接给出
    } else {
      this.totalRate = [totalDown, totalUp];
    }
    this.history.push({ t: now, down: this.totalRate[0], up: this.totalRate[1] });
    if (this.history.length > HISTORY_LEN) this.history.splice(0, this.history.length - HISTORY_LEN);
    // 清理长时间无流量的速率项
    for (const [mac, r] of Array.from(this.rates.entries())) {
      if (r[0] < 1 && r[1] < 1) this.rates.set(mac, [0, 0]);
    }
  }

  /** 把增量写入数据库并累加到本次会话。 */
  async flush(): Promise<void> {
    const pending: Record<string, [number, number]> = {};
    for (const [m, v] of this.acc.entries()) {
      if (v[0] || v[1]) pending[m] = [v[0], v[1]];
    }
    this.acc = new Map();
    this.prevAcc = new Map();
    for (const [mac, [rx, tx]] of Object.entries(pending)) {
      const s = this.session.get(mac) ?? [0, 0];
      s[0] += rx;
      s[1] += tx;
      this.session.set(mac, s);
    }
    for (const [mac, [rx, tx]] of Object.entries(pending)) {
      if (mac === '__total__') continue;
      try {
        await this.db.add(mac, rx, tx);
      } catch (exc) {
        log.exception(`流量落库失败 ${mac}`, exc);
      }
    }
    if ('__total__' in pending) {
      const [rx, tx] = pending['__total__'];
      try {
        await this.db.add('__total__', rx, tx);
      } catch {
        /* ignore */
      }
    }
  }

  // ----------------------------- 查询接口 ----------------------------- //
  setKnownMacs(macs: string[]): void {
    this.known = new Set(macs.map((m) => normalizeMac(m)).filter(Boolean));
  }

  rateOf(mac: string): [number, number] {
    const r = this.rates.get(normalizeMac(mac));
    return r ? [r[0], r[1]] : [0, 0];
  }

  sessionOf(mac: string): [number, number] {
    const s = this.session.get(normalizeMac(mac));
    return s ? [s[0], s[1]] : [0, 0];
  }

  totalRateNow(): [number, number] {
    return [this.totalRate[0], this.totalRate[1]];
  }

  historyPoints(): HistoryPoint[] {
    return [...this.history];
  }

  sessionTotals(): [number, number] {
    let rx = 0;
    let tx = 0;
    for (const [m, v] of this.session.entries()) {
      if (m !== '__total__') {
        rx += v[0];
        tx += v[1];
      }
    }
    if (this.backendName === 'adapter') {
      const tot = this.session.get('__total__') ?? [0, 0];
      rx = tot[0];
      tx = tot[1];
    }
    return [Math.trunc(rx), Math.trunc(tx)];
  }

  get statusText(): string {
    const label: Record<string, string> = {
      scapy: '精确抓包',
      adapter: '网卡总量',
      demo: '演示数据',
      off: '已关闭',
    };
    return `${label[this.backendName] ?? this.backendName} · ${this.backendNote}`;
  }
}

/** Box-Muller 正态分布（对应 python random.gauss）。 */
function gauss(mu: number, sigma: number): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return mu + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
