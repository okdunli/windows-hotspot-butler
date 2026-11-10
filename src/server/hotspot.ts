/**
 * 热点控制（对应原项目 hotspot/core/hotspot.py）：
 * Windows 10/11 移动热点(WinRT) + 承载网络(netsh) 双后端，统一控制器自动挑选。
 */
import { enableIcs, disableIcs } from './ics.ts';
import * as netinfo from './netinfo.ts';
import { b64, isAdmin, psScript, runConsole } from './pshell.ts';
import { normalizeMac } from './util.ts';
import { TETHERING_PS1, getLogger } from './paths.ts';
import type { HotspotConfig } from './config.ts';
import {
  type WlanCapabilities,
  probeDrivers,
  mergeTethering,
  primaryAdapter,
  band5Text,
  hostHotspotCapability,
} from './wificaps.ts';

const log = getLogger('hotspot');

const MAC_RE = /([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}/g;

export interface HotspotStatus {
  ok: boolean;
  active: boolean;
  state: string; // on | off | transition | unknown
  ssid: string;
  passphrase: string;
  band: string;
  clientCount: number;
  maxClients: number | null;
  backend: string;
  message: string;
  errorType: string;
  band24Supported: boolean;
  band5Supported: boolean;
  clientsSupported: boolean;
  updated: number;
}

export function hotspotStatus(init: Partial<HotspotStatus> = {}): HotspotStatus {
  return {
    ok: true,
    active: false,
    state: 'unknown',
    ssid: '',
    passphrase: '',
    band: '',
    clientCount: 0,
    maxClients: null,
    backend: 'none',
    message: '',
    errorType: '',
    band24Supported: true,
    band5Supported: false,
    clientsSupported: false,
    updated: Date.now() / 1000,
    ...init,
  };
}

export function stateText(st: HotspotStatus): string {
  return ({ on: '已开启', off: '已关闭', transition: '切换中…' } as Record<string, string>)[st.state] ?? '未知';
}

export interface RawClient {
  mac: string;
  ip: string;
  source: string;
}

// --------------------------------------------------------------------------- //
//                          后端 1：移动热点 (WinRT)                            //
// --------------------------------------------------------------------------- //
interface PsData extends Record<string, unknown> {
  ok?: boolean;
  error?: string;
  errorType?: string;
  state?: string;
  ssid?: string;
  passphrase?: string;
  band?: string;
  clientCount?: unknown;
  maxClientCount?: unknown;
  clients?: unknown;
  band24Supported?: boolean;
  band5Supported?: boolean;
  bandWarning?: string;
}

export class WinRTBackend {
  readonly name = 'winrt';
  readonly label = '移动热点 (WinRT)';
  private availableCache: boolean | null = null;
  lastError = '';

  private async call(action: string, timeout = 45.0, params: Record<string, string> = {}): Promise<PsData> {
    const args = { Action: action, ...params };
    const data = await psScript(TETHERING_PS1, args, timeout);
    if (!data.ok) this.lastError = String(data.error ?? '未知错误');
    return data;
  }

  private static toStatus(data: PsData): HotspotStatus {
    const stateRaw = String(data.state ?? '').toLowerCase();
    const state = ({ on: 'on', off: 'off', intransition: 'transition' } as Record<string, string>)[stateRaw] ?? 'unknown';
    let maxC: number | null = null;
    if (data.maxClientCount) {
      const n = Number(data.maxClientCount);
      if (Number.isFinite(n)) maxC = n;
    }
    const st = hotspotStatus({
      ok: Boolean(data.ok),
      active: state === 'on',
      state,
      ssid: String(data.ssid ?? ''),
      passphrase: String(data.passphrase ?? ''),
      band: String(data.band ?? ''),
      clientCount: Number(data.clientCount ?? 0) || 0,
      maxClients: maxC,
      backend: 'winrt',
      message: String(data.error ?? ''),
      errorType: String(data.errorType ?? ''),
      band24Supported: data.band24Supported !== undefined ? Boolean(data.band24Supported) : true,
      band5Supported: Boolean(data.band5Supported ?? false),
      clientsSupported: Boolean((data as Record<string, unknown>).clientsSupported ?? false),
    });
    // 热点未开启时 IsBandSupported 常返回 false，视为"未知"而非不支持
    if (!st.active && !st.band24Supported && !st.band5Supported) st.band24Supported = true;
    return st;
  }

  /** 供 HotspotController 能力探测复用的 probe 入口。 */
  probe(timeout = 30): Promise<PsData> {
    return this.call('probe', timeout);
  }

  async available(): Promise<boolean> {
    if (this.availableCache === null) {
      const data = await this.call('probe', 30);
      this.availableCache = Boolean(data.ok) || data.errorType === 'NO_INTERNET_PROFILE';
      if (!this.availableCache) log.info(`WinRT 移动热点不可用：${this.lastError}`);
    }
    return this.availableCache;
  }

  async snapshot(): Promise<[HotspotStatus, RawClient[]]> {
    const data = await this.call('all', 40);
    const st = WinRTBackend.toStatus(data);
    const clients: RawClient[] = [];
    for (const item of (data.clients as unknown[]) ?? []) {
      if (!item || typeof item !== 'object') continue;
      const it = item as Record<string, unknown>;
      const mac = normalizeMac(String(it.mac ?? ''));
      const ips = ((it.ips as unknown[]) ?? []).map((i) => String(i)).filter(Boolean);
      const ipv4 = ips.find((i) => /^\d+\.\d+\.\d+\.\d+$/.test(i)) ?? '';
      if (mac) clients.push({ mac, ip: ipv4, source: 'winrt' });
    }
    return [st, clients];
  }

  async apply(cfg: HotspotConfig): Promise<[boolean, string]> {
    const params = {
      SsidB64: b64(cfg.ssid),
      PassB64: b64(cfg.security === 'open' ? '' : cfg.passphrase),
      Band: cfg.band,
    };
    const data = await this.call('configure', 60, params);
    if (data.ok) {
      const warn = data.bandWarning;
      return [true, `配置已写入系统移动热点。${warn ? '频段提示：' + String(warn) : ''}`];
    }
    return [false, this.explain(data)];
  }

  async start(): Promise<[boolean, string]> {
    const data = await this.call('start', 90);
    if (data.ok) return [true, '热点已开启'];
    return [false, this.explain(data)];
  }

  async stop(): Promise<[boolean, string]> {
    const data = await this.call('stop', 90);
    if (data.ok) return [true, '热点已关闭'];
    return [false, this.explain(data)];
  }

  private explain(data: PsData): string {
    const etype = String(data.errorType ?? '');
    const err = String(data.error ?? '未知错误');
    const table: Record<string, string> = {
      NO_INTERNET_PROFILE: '未检测到可共享的网络连接，请先连上有线/无线网络再开启热点。',
      API_UNAVAILABLE: '当前系统不支持移动热点 API（需 Windows 10 1607 以上）。',
      TIMEOUT: '系统响应超时，请稍后重试。',
    };
    let detail = table[etype];
    const stateMap: Record<string, string> = {
      WiFiDeviceOff: '无线网卡已关闭（飞行模式/无线开关），请先打开 WLAN。',
      MobileBroadbandDeviceOff: '移动宽带设备已关闭。',
      NetworkLimitedConnectivity: '当前网络无法共享（无 Internet 连接）。',
      TetheringOperationInProgress: '上一次操作仍在进行中，请稍等。',
      OperationInProgress: '上一次操作仍在进行中，请稍等。',
      EntitlementCheckFailure: '运营商/系统策略不允许共享网络。',
      Unknown: '系统返回未知错误，建议在「设置 → 网络 → 移动热点」里手动试一次。',
    };
    if (stateMap[err]) detail = stateMap[err];
    return detail ?? err;
  }
}

// --------------------------------------------------------------------------- //
//                        后端 2：承载网络 (netsh)                              //
// --------------------------------------------------------------------------- //
export class NetshBackend {
  readonly name = 'netsh';
  readonly label = '承载网络 (netsh)';

  private static readonly YES = ['yes', '是', '允许', '已启动', 'started', 'available', '可用'];

  private availableCache: boolean | null = null;
  lastError = '';

  private static kv(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const line of text.split('\n')) {
      if (line.includes(':')) {
        const idx = line.indexOf(':');
        const k = line.slice(0, idx).trim().toLowerCase();
        const v = line.slice(idx + 1).trim();
        out[k] = v;
      }
    }
    return out;
  }

  async available(): Promise<boolean> {
    if (this.availableCache === null) {
      const text = await runConsole(['netsh', 'wlan', 'show', 'drivers'], 15);
      const kv = NetshBackend.kv(text);
      let val = '';
      for (const key of Object.keys(kv)) {
        if (key.includes('hosted network') || key.includes('承载网络')) {
          val = kv[key].toLowerCase();
          break;
        }
      }
      this.availableCache = NetshBackend.YES.some((y) => val.includes(y));
      if (!this.availableCache) {
        this.lastError = '无线网卡驱动不支持承载网络(hostednetwork)';
        log.info(`netsh 承载网络不可用：${val || '未知'}`);
      }
    }
    return this.availableCache;
  }

  async snapshot(): Promise<[HotspotStatus, RawClient[]]> {
    const text = await runConsole(['netsh', 'wlan', 'show', 'hostednetwork'], 15);
    const kv = NetshBackend.kv(text);
    let statusVal = '';
    let ssid = '';
    let maxC: number | null = null;
    for (const [key, val] of Object.entries(kv)) {
      if (key === 'status' || key === '状态') statusVal = val.toLowerCase();
      else if (key.includes('ssid') && !key.includes('name') && !key.includes('bssid')) ssid = val.replaceAll('"', '');
      else if (key.includes('max number of clients') || key.includes('客户端最大数目') || key.includes('最大客户端')) {
        const digits = val.replace(/\D/g, '');
        const n = Number(digits);
        if (Number.isFinite(n) && n > 0) maxC = n;
      }
    }
    const active = NetshBackend.YES.slice(0, 5).some((y) => statusVal.includes(y.toLowerCase()));
    const clients: RawClient[] = [];
    let inClients = false;
    for (const line of text.split('\n')) {
      const low = line.toLowerCase();
      if (low.includes('client') || line.includes('客户端')) inClients = true;
      MAC_RE.lastIndex = 0;
      const m = MAC_RE.exec(line);
      if (m && inClients) {
        const mac = normalizeMac(m[0]);
        if (!clients.some((c) => c.mac === mac)) clients.push({ mac, ip: '', source: 'netsh' });
      }
    }
    const st = hotspotStatus({
      ok: Boolean(text.trim()),
      active,
      state: active ? 'on' : 'off',
      ssid,
      band: '2.4',
      clientCount: clients.length,
      maxClients: maxC,
      backend: 'netsh',
      clientsSupported: true,
      band24Supported: true,
      band5Supported: false,
      message: text.trim() ? '' : 'netsh 无输出',
    });
    return [st, clients];
  }

  async apply(cfg: HotspotConfig): Promise<[boolean, string]> {
    const args = ['netsh', 'wlan', 'set', 'hostednetwork', 'mode=allow', `ssid=${cfg.ssid}`];
    if (cfg.security !== 'open') {
      args.push(`key=${cfg.passphrase}`);
      args.push('keyUsage=persistent');
    }
    const text = await runConsole(args, 20);
    const ok = Boolean(text) && !/(fail|失败|错误|无效)/i.test(text);
    return [ok, (text.trim().split('\n').pop() || '已写入承载网络配置').slice(0, 160)];
  }

  async start(): Promise<[boolean, string]> {
    const text = await runConsole(['netsh', 'wlan', 'start', 'hostednetwork'], 30);
    const ok = /(started|已启动)/i.test(text);
    let msg = (text.trim().split('\n').pop() || '无输出').slice(0, 160);
    if (!ok && text.includes('无法启动')) msg += '（承载网络需要网卡驱动支持，且需管理员权限）';
    return [ok, msg];
  }

  async stop(): Promise<[boolean, string]> {
    const text = await runConsole(['netsh', 'wlan', 'stop', 'hostednetwork'], 30);
    const ok = /(stopped|已停止)/i.test(text);
    return [ok, (text.trim().split('\n').pop() || '无输出').slice(0, 160)];
  }
}

// --------------------------------------------------------------------------- //
//                              统一控制器                                      //
// --------------------------------------------------------------------------- //
export class HotspotController {
  cfg: HotspotConfig;
  winrt = new WinRTBackend();
  netsh = new NetshBackend();
  private backendRef: WinRTBackend | NetshBackend | null = null;
  lastStatus: HotspotStatus = hotspotStatus({ state: 'unknown', backend: 'none' });
  diagnostics: string[] = [];
  wlanCaps: WlanCapabilities | null = null;
  /** 事实标记：一旦真的开成功过，后续一切能力提示都以此为准 */
  everActive = false;

  constructor(cfg: HotspotConfig) {
    this.cfg = cfg;
  }

  /** 探测本机无线网卡能力（驱动 + 移动热点 API），结果供 UI 动态启用选项。 */
  async detectCapabilities(refresh = false): Promise<WlanCapabilities> {
    if (this.wlanCaps !== null && !refresh) return this.wlanCaps;
    const caps = await probeDrivers();
    try {
      const data = await this.winrt.probe(30);
      if (data.ok || data.ssid !== undefined && data.ssid !== null) {
        mergeTethering(caps, data as Record<string, unknown>);
      }
      // WinRT 移动热点 API 可用 = 本机可开热点（权威信号，盖过 netsh 误报）
      caps.winrtAvailable = Boolean(data.ok) || data.errorType === 'NO_INTERNET_PROFILE';
    } catch {
      /* probe 失败不阻塞 */
    }
    caps.activeNow = Boolean(this.lastStatus.active);
    caps.everActive = Boolean(this.everActive || caps.activeNow);
    this.wlanCaps = caps;
    return caps;
  }

  async resolveBackend(force = ''): Promise<WinRTBackend | NetshBackend | null> {
    const want = force || this.cfg.backend || 'auto';
    if (want === 'winrt') {
      this.backendRef = (await this.winrt.available()) ? this.winrt : null;
    } else if (want === 'netsh') {
      this.backendRef = (await this.netsh.available()) ? this.netsh : null;
    } else {
      // auto：优先用静态能力结论选对后端
      const caps = await this.detectCapabilities();
      const sa = caps.softApSupported;
      const wfd = caps.wifiDirectSupported;
      const hosted = caps.hostedSupported;
      if (sa === true || wfd === true) {
        this.backendRef = (await this.winrt.available())
          ? this.winrt
          : (await this.netsh.available())
            ? this.netsh
            : null;
      } else if (sa === false && wfd === false && hosted) {
        this.backendRef = (await this.netsh.available()) ? this.netsh : null;
      } else if (sa === false && wfd === false && !hosted) {
        this.backendRef = null;
      } else {
        if (await this.winrt.available()) this.backendRef = this.winrt;
        else if (await this.netsh.available()) this.backendRef = this.netsh;
        else this.backendRef = null;
      }
    }
    return this.backendRef;
  }

  get backend(): Promise<WinRTBackend | NetshBackend | null> {
    return this.backendRef ? Promise.resolve(this.backendRef) : this.resolveBackend();
  }

  backendLabelSync(): string {
    return this.backendRef ? this.backendRef.label : '不可用';
  }

  /** 告诉界面哪些功能真正可用（全部来自实际探测）。 */
  async capabilities(refresh = false): Promise<Record<string, unknown>> {
    const be = await this.backend;
    const st = this.lastStatus;
    const bname = be ? be.name : 'none';
    const winrt = bname === 'winrt';
    const caps = await this.detectCapabilities(refresh);
    const hc = hostHotspotCapability(caps);
    const p = primaryAdapter(caps);
    return {
      backend: bname,
      backend_label: this.backendLabelSync(),
      recommended_backend: hc.backend,
      ssid: be !== null,
      passphrase: be !== null,
      band: winrt,
      band_24: caps.band24Supported,
      band_5: winrt && supportsBand5(caps),
      band_5_known: caps.band5Known,
      band_5_text: band5Text(caps),
      hosted_supported: caps.hostedSupported,
      soft_ap_supported: caps.softApSupported,
      wifi_direct_supported: caps.wifiDirectSupported,
      winrt_available: caps.winrtAvailable,
      can_host_hotspot: hc.canHost,
      host_block_reason: hc.reason,
      host_block_detail: hc.detail,
      wpa3: caps.wpa3Supported,
      wpa2: caps.wpa2Supported,
      max_clients_writable: false, // Windows 未开放写入 API
      max_clients_readable: st.maxClients !== null,
      max_clients_system: st.maxClients,
      client_list: true, // 退化用 ARP 也能拿到
      security_choice: bname === 'netsh',
      adapters: caps.adapters.map((a) => a.name),
      radios: p ? p.prettyRadios() : '未知',
      driver: p ? p.driver : '',
      notes: [...caps.notes],
      admin: isAdmin(),
    };
  }

  /** 状态与客户端。 */
  async snapshot(): Promise<[HotspotStatus, RawClient[]]> {
    const be = await this.backend;
    if (be === null) {
      const st = hotspotStatus({
        ok: false,
        state: 'unknown',
        backend: 'none',
        message:
          this.winrt.lastError || this.netsh.lastError || '系统未提供可用的热点后端',
      });
      this.lastStatus = st;
      return [st, []];
    }
    let st: HotspotStatus;
    let clients: RawClient[];
    try {
      [st, clients] = await be.snapshot();
    } catch (exc) {
      log.exception('获取热点状态失败', exc);
      st = hotspotStatus({ ok: false, backend: be.name, message: `获取状态异常：${String(exc)}` });
      clients = [];
    }
    st.backend = be.name;
    this.lastStatus = st;
    if (st.active) {
      this.everActive = true;
      if (this.wlanCaps !== null) {
        this.wlanCaps.activeNow = true;
        this.wlanCaps.everActive = true;
      }
    }

    // 用 ARP 表补齐 IP（WinRT 有时只给 MAC）
    try {
      const gw = await netinfo.hotspotGatewayIp();
      const prefix = netinfo.subnetPrefixOf(gw);
      const arp = await netinfo.arpEntries(prefix);
      const byMac = new Map(arp.map(([ip, mac]) => [mac, ip]));
      const known = new Set(clients.map((c) => c.mac));
      for (const c of clients) {
        if (!c.ip && byMac.has(c.mac)) c.ip = byMac.get(c.mac)!;
      }
      if (st.active) {
        for (const [ip, mac] of arp) {
          if (!known.has(mac) && ip !== gw) {
            clients.push({ mac, ip, source: 'arp' });
            known.add(mac);
          }
        }
      }
    } catch (exc) {
      log.debug(`ARP 补全失败：${String(exc)}`);
    }
    st.clientCount = Math.max(st.clientCount, clients.length);
    return [st, clients];
  }

  async applyConfig(cfg?: HotspotConfig): Promise<[boolean, string]> {
    cfg = cfg ?? this.cfg;
    const be = await this.backend;
    if (be === null) return [false, '没有可用的热点后端'];
    const [ok, msg0] = await be.apply(cfg);
    let msg = msg0;
    const extra: string[] = [];
    if (be.name === 'winrt') {
      if (cfg.security !== 'wpa2') {
        extra.push('系统移动热点固定使用 WPA2-个人加密，其它加密选项仅本地记录');
      }
      if (cfg.band === '5' && !this.lastStatus.band5Supported) {
        extra.push('当前无线网卡可能不支持 5 GHz，系统会回退到 2.4 GHz');
      }
    }
    if (extra.length) msg = msg + '（' + extra.join('；') + '）';
    return [ok, msg];
  }

  async start(): Promise<[boolean, string]> {
    const be = await this.backend;
    if (be === null) return [false, '没有可用的热点后端'];
    const [ok, msg0] = await be.start();
    if (!ok) {
      const msg = await this.enrichStartError(msg0);
      return [ok, msg];
    }
    this.everActive = true;
    if (this.wlanCaps !== null) {
      this.wlanCaps.everActive = true;
      this.wlanCaps.activeNow = true;
    }
    let msg = msg0;
    // netsh 后端只建 AP、不共享上网，必须显式开 ICS 设备才能上网
    if (be.name === 'netsh') {
      try {
        const [okIcs, mIcs] = await enableIcs();
        if (okIcs) msg += '；已开启互联网连接共享(ICS)，设备可正常上网。';
        else {
          msg += `；但互联网连接共享未开启（${mIcs}），设备可能连上却无法上网，请确认以管理员身份运行。`;
          log.warn(`ICS 启用失败：${mIcs}`);
        }
      } catch (exc) {
        log.exception('ICS 启用异常', exc);
        msg += '；互联网连接共享启用时发生异常，设备可能无法上网。';
      }
    }
    return [ok, msg];
  }

  private async enrichStartError(msg: string): Promise<string> {
    const caps = await this.detectCapabilities(false);
    const hc = hostHotspotCapability(caps);
    if (!hc.canHost && hc.detail) {
      if (hc.reason) return hc.reason + '\n' + hc.detail;
    }
    return msg;
  }

  async stop(): Promise<[boolean, string]> {
    const be = await this.backend;
    if (be === null) return [false, '没有可用的热点后端'];
    const name = be.name;
    let priv: netinfo.Adapter | null = null;
    if (name === 'netsh') {
      // 关闭前先拿到热点网卡名，关闭后该虚拟网卡可能被卸载
      try {
        priv = await netinfo.findHotspotAdapter();
      } catch {
        priv = null;
      }
    }
    const [ok, msg] = await be.stop();
    if (ok && name === 'netsh') {
      try {
        await disableIcs(priv ? priv.name : null);
      } catch (exc) {
        log.exception('ICS 关闭异常', exc);
      }
    }
    return [ok, msg];
  }

  async toggle(): Promise<[boolean, string]> {
    return this.lastStatus.active ? this.stop() : this.start();
  }

  /** 收集环境诊断信息，供「关于/诊断」页面显示。 */
  async diagnose(): Promise<string[]> {
    const lines: string[] = [];
    const caps = await this.detectCapabilities(true);
    const p = primaryAdapter(caps);
    if (p) {
      lines.push(`无线网卡：${p.name} — ${p.driver}`);
      lines.push(`无线电类型：${p.prettyRadios()}`);
      lines.push(
        `频段支持：2.4GHz ${caps.band24Supported ? '√' : '×'}，` +
          `5GHz ${band5Text(caps)}，6GHz ${p.band6 ? '√' : '×'}`,
      );
      lines.push(`加密能力：WPA2 ${caps.wpa2Supported ? '√' : '×'}，WPA3 ${caps.wpa3Supported ? '√' : '×'}`);
      const wfd = caps.wifiDirectSupported;
      lines.push(`Wi-Fi Direct：${wfd === true ? '支持' : wfd === false ? '不支持' : '未知'}`);
    }
    lines.push(...caps.notes);
    lines.push(`管理员权限：${isAdmin() ? '是' : '否（部分操作会失败）'}`);
    lines.push(
      `移动热点 API(WinRT)：${(await this.winrt.available()) ? '可用' : '不可用 - ' + this.winrt.lastError}`,
    );
    lines.push(
      `承载网络(netsh)：${(await this.netsh.available()) ? '可用' : '不可用 - ' + this.netsh.lastError}`,
    );
    const st = this.lastStatus;
    lines.push(`当前后端：${this.backendLabelSync()}，状态：${stateText(st)}`);
    if (st.maxClients) lines.push(`系统上报的最大客户端数：${st.maxClients}`);
    try {
      const ics = await netinfo.icsState();
      lines.push(`热点网卡：${ics.hotspotAdapter || '未发现'}，网关 IP：${ics.gatewayIp}`);
      lines.push(`被共享的上网连接：${ics.sharedAdapter || '未发现'}`);
      lines.push(...ics.notes);
    } catch (exc) {
      lines.push(`网卡信息获取失败：${String(exc)}`);
    }
    this.diagnostics = lines;
    return lines;
  }
}

function supportsBand5(caps: WlanCapabilities): boolean {
  return caps.band5Supported || !caps.band5Known;
}
