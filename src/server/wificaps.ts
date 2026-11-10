/**
 * 无线网卡能力探测（对应原项目 hotspot/core/wificaps.py）。
 * 数据来源：netsh wlan show drivers / show wirelesscapabilities + WinRT 移动热点上报。
 */
import { psJson, runConsole } from './pshell.ts';
import { getLogger } from './paths.ts';

const log = getLogger('wificaps');

// 无线电类型 -> 可能的频段
const RADIO_24 = new Set(['802.11b', '802.11g', '802.11n', '802.11ax', '802.11be', '802.11bn']);
const RADIO_5 = new Set(['802.11a', '802.11n', '802.11ac', '802.11ax', '802.11be', '802.11bn']);
const RADIO_6 = new Set(['802.11ax', '802.11be', '802.11bn']);

const KV_ALIASES: Record<string, string[]> = {
  driver: ['驱动程序', 'driver'],
  vendor: ['供应商', 'vendor'],
  radios: ['支持的无线电类型', 'radio types supported'],
  hosted: ['支持的承载网络', 'hosted network supported'],
};
const YES = ['yes', '是', 'true', '支持'];
const NO = ['no', '否', 'false', '不支持'];

export interface WlanAdapter {
  name: string;
  driver: string;
  vendor: string;
  radios: string[];
  hostedSupported: boolean;
  softApSupported: boolean | null; // null = 未能静态确认
  wifiDirectSupported: boolean | null;
  auths: string[];
  band24: boolean;
  band5: boolean;
  band6: boolean;
  band5Possible: boolean;

  prettyRadios(): string;
}

function makeAdapter(name: string): WlanAdapter {
  return {
    name,
    driver: '',
    vendor: '',
    radios: [],
    hostedSupported: false,
    softApSupported: null,
    wifiDirectSupported: null,
    auths: [],
    band24: true,
    band5: false,
    band6: false,
    band5Possible: false,
    prettyRadios() {
      return this.radios.length ? this.radios.join(' / ') : '未知';
    },
  };
}

export interface WlanCapabilities {
  adapters: WlanAdapter[];
  hostedSupported: boolean;
  softApSupported: boolean | null;
  wifiDirectSupported: boolean | null;
  band24Supported: boolean;
  band5Supported: boolean;
  band5Known: boolean;
  wpa3Supported: boolean;
  wpa2Supported: boolean;
  notes: string[];
  raw: string;
  activeNow: boolean;
  everActive: boolean;
  winrtAvailable: boolean | null;
}

export function newCapabilities(): WlanCapabilities {
  return {
    adapters: [],
    hostedSupported: false,
    softApSupported: null,
    wifiDirectSupported: null,
    band24Supported: true,
    band5Supported: false,
    band5Known: false,
    wpa3Supported: false,
    wpa2Supported: true,
    notes: [],
    raw: '',
    activeNow: false,
    everActive: false,
    winrtAvailable: null,
  };
}

export function primaryAdapter(caps: WlanCapabilities): WlanAdapter | null {
  return caps.adapters[0] ?? null;
}

export function band5Text(caps: WlanCapabilities): string {
  if (caps.band5Supported) return '支持';
  if (caps.band5Known) return '不支持';
  return '未知（需网卡驱动支持）';
}

export function supportsBand(caps: WlanCapabilities, band: string): boolean {
  if (band === '5') return caps.band5Supported || !caps.band5Known;
  if (band === '6') {
    const p = primaryAdapter(caps);
    return Boolean(p && p.band6);
  }
  return true;
}

export interface HostCapability {
  canHost: boolean;
  reason: string;
  softAp: boolean | null;
  wifiDirect: boolean | null;
  hosted: boolean | null;
  backend: string;
  detail: string;
}

export function hostHotspotCapability(caps: WlanCapabilities): HostCapability {
  const sa = caps.softApSupported;
  const wfd = caps.wifiDirectSupported;
  const hosted = caps.hostedSupported;

  // 权威信号优先：已实测开过 / 当前运行中 / WinRT 移动热点 API 可用
  if (caps.everActive || caps.activeNow || caps.winrtAvailable === true) {
    let detail = '本机已实测可正常开启热点。';
    if (caps.winrtAvailable === true && !(caps.everActive || caps.activeNow)) {
      detail = '本机 Windows 移动热点 API 可用，可正常开启热点。';
    }
    const backend =
      sa === true || wfd === true || caps.winrtAvailable === true
        ? 'winrt'
        : hosted
          ? 'netsh'
          : '';
    return {
      canHost: true,
      reason: '',
      softAp: sa,
      wifiDirect: wfd,
      hosted: hosted || null,
      backend,
      detail,
    };
  }

  if (!caps.adapters.length) {
    return {
      canHost: false,
      reason: '未检测到无线网卡，或 WLAN 服务未运行。',
      softAp: null,
      wifiDirect: caps.wifiDirectSupported || null,
      hosted: caps.hostedSupported || null,
      backend: '',
      detail: '请确认本机有无线网卡且已启用。',
    };
  }
  const p = primaryAdapter(caps)!;
  const drv = p.driver || p.name;
  const name = p.name;

  // 三条路都明确不支持 → 真实硬件限制
  if (sa === false && wfd === false && !hosted) {
    return {
      canHost: false,
      reason:
        `本机 WiFi 网卡「${name}」（${drv}）既不支持「软 AP(Soft AP)」、` +
        `也不支持「Wi-Fi Direct」、也不支持「承载网络(hostednetwork)」，` +
        `Windows 无法用它发射 WiFi 信号，任何软件都开不了热点。`,
      softAp: false,
      wifiDirect: false,
      hosted: false,
      backend: '',
      detail:
        '这是网卡硬件/驱动能力限制，并非软件问题。\n' +
        '解决办法：更换一块支持 Wi-Fi Direct 或承载网络(hostednetwork)的无线网卡' +
        '——多数笔记本内置无线网卡支持；当前这块属于只能连 WiFi、不能发射的客户端芯片。\n' +
        '若确实需要用本机共享网络，可尝试带虚拟网卡驱动的方案（如猎豹/360 免费 WiFi），' +
        '它们会在系统里安装一张虚拟 AP 网卡来绕过此限制。',
    };
  }

  // 软 AP 或 Wi-Fi Direct 支持 → 移动热点(WinRT) 后端可用
  if (sa === true || wfd === true) {
    const via = wfd === true && sa !== true ? 'Wi-Fi Direct' : '软 AP';
    return {
      canHost: true,
      reason: '',
      softAp: sa,
      wifiDirect: wfd,
      hosted: hosted || null,
      backend: 'winrt',
      detail:
        `网卡支持${via}，可使用「移动热点(WinRT)」后端开启热点` +
        `（Windows 10/11 移动热点正是基于${wfd === true ? 'Wi-Fi Direct' : '软 AP'}）。` +
        `若 WinRT 不可用，也可回退到「承载网络(netsh)」后端。`,
    };
  }

  // 软 AP / Wi-Fi Direct 不支持，但承载网络支持 → 走 netsh 后端
  if (hosted) {
    return {
      canHost: true,
      reason: '',
      softAp: sa,
      wifiDirect: wfd,
      hosted: true,
      backend: 'netsh',
      detail:
        `本机 WiFi 网卡「${name}」（${drv}）不支持软 AP / Wi-Fi Direct，` +
        `因此「移动热点(WinRT)」后端用不了；\n` +
        `但驱动支持「承载网络(hostednetwork)」，可改用「承载网络(netsh)」后端开启热点` +
        `（功能与猎豹/360 WiFi 走的方案相同，需再用 ICS 共享上网连接）。\n` +
        `请在「后端与行为」里把控制后端设为「承载网络(netsh)」或「自动选择」。`,
    };
  }

  // 都未能确认 → 保守放行，由实际开启结果决定
  return {
    canHost: true,
    reason: '',
    softAp: sa,
    wifiDirect: wfd,
    hosted: hosted || null,
    backend: '',
    detail: '未能静态确认热点能力，将以实际开启结果为准。',
  };
}

const KV_RE = /^\s*([^:：]{2,40})\s*[:：]\s*(.+?)\s*$/;
const IFACE_RE = /^\s*(?:接口名称|Interface name)\s*[:：]\s*(.+?)\s*$/i;

function matchAlias(key: string, fieldName: string): boolean {
  const aliases = KV_ALIASES[fieldName] ?? [];
  const low = key.toLowerCase();
  return aliases.some((a) => key.includes(a) || low.includes(a.toLowerCase()));
}

function isYes(value: string): boolean {
  const low = (value || '').trim().toLowerCase();
  return YES.some((y) => low.startsWith(y));
}

function isNo(value: string): boolean {
  const low = (value || '').trim().toLowerCase();
  return NO.some((n) => low.startsWith(n));
}

/** 解析 `netsh wlan show wirelesscapabilities` 的指定能力行。 */
async function probeCapabilityLine(
  needles: string[],
): Promise<Record<string, boolean | null>> {
  const text = await runConsole(['netsh', 'wlan', 'show', 'wirelesscapabilities'], 20);
  const result: Record<string, boolean | null> = {};
  let cur: string | null = null;
  for (const line of text.split('\n')) {
    const mIface = IFACE_RE.exec(line);
    if (mIface && mIface[1]) {
      cur = mIface[1].trim();
      if (!(cur in result)) result[cur] = null;
      continue;
    }
    const low = line.toLowerCase();
    if (needles.some((n) => low.includes(n))) {
      const mv = /[:：]\s*(.+?)\s*$/.exec(line);
      if (mv) {
        const v = mv[1].trim();
        if (isYes(v)) result[cur ?? ''] = true;
        else if (isNo(v)) result[cur ?? ''] = false;
        // 未知/Unknown 保持 null
      }
    }
  }
  return result;
}

const probeWirelessCapabilities = () =>
  probeCapabilityLine(['软 ap', 'soft ap', 'softap']);
const probeWifiDirect = () =>
  probeCapabilityLine(['wi-fi direct', 'wifi direct', 'wifidirect']);

/** 解析 `netsh wlan show drivers`（中英文双语，支持多网卡）。 */
export async function probeDrivers(): Promise<WlanCapabilities> {
  const text = await runConsole(['netsh', 'wlan', 'show', 'drivers'], 20);
  const caps = newCapabilities();
  caps.raw = text;
  if (!text.trim() || /(没有|no wireless|不是)/i.test(text)) {
    caps.notes.push('未检测到无线网卡，或 WLAN 服务未运行。');
    return caps;
  }

  let current: WlanAdapter | null = null;
  let inAuthBlock = false;

  for (const line of text.split('\n')) {
    const mIface = IFACE_RE.exec(line);
    if (mIface && mIface[1]) {
      if (current) caps.adapters.push(current);
      current = makeAdapter(mIface[1].trim());
      inAuthBlock = false;
      continue;
    }
    if (!current) continue;
    const low = line.toLowerCase();
    if (low.includes('authentication and cipher') || line.includes('身份验证和密码')) {
      inAuthBlock = true;
      continue;
    }
    // 认证/加密列表块内的行不含冒号
    if (inAuthBlock && !line.includes(':') && !line.includes('：')) {
      const auth = line.trim().split(/\s{2,}/);
      if (auth[0] && /[A-Za-z一-鿿]{2,}/.test(auth[0])) current.auths.push(auth[0].trim());
      continue;
    }
    const m = KV_RE.exec(line);
    if (!m || !m[1] || m[2] === undefined) continue;
    inAuthBlock = false;
    const key = m[1].trim();
    const val = m[2].trim();
    if (matchAlias(key, 'driver')) current.driver = val;
    else if (matchAlias(key, 'vendor')) current.vendor = val;
    else if (matchAlias(key, 'radios')) current.radios = val.match(/802\.11[a-zA-Z]{1,3}/g) ?? [];
    else if (matchAlias(key, 'hosted')) current.hostedSupported = isYes(val);
  }

  if (current) caps.adapters.push(current);
  if (!caps.adapters.length) {
    caps.notes.push('无法解析无线网卡信息（netsh 输出格式异常）。');
    return caps;
  }

  // 合并「软 AP」支持（按接口名匹配）
  const soft = await probeWirelessCapabilities();
  const softValues: (boolean | null)[] = [];
  for (const ad of caps.adapters) {
    if (ad.name in soft) {
      ad.softApSupported = soft[ad.name];
      softValues.push(soft[ad.name]);
    }
  }

  // 合并「Wi-Fi Direct」支持（按接口名匹配）
  const wfd = await probeWifiDirect();
  const wfdValues: (boolean | null)[] = [];
  for (const ad of caps.adapters) {
    if (ad.name in wfd) {
      ad.wifiDirectSupported = wfd[ad.name];
      wfdValues.push(wfd[ad.name]);
    }
  }

  for (const ad of caps.adapters) {
    const radios = new Set(ad.radios.map((r) => r.toLowerCase()));
    ad.band24 = radios.size === 0 || Array.from(radios).some((r) => RADIO_24.has(r));
    ad.band5 =
      Array.from(radios).some((r) => RADIO_5.has(r)) &&
      Array.from(radios).some((r) => ['802.11a', '802.11ac', '802.11ax', '802.11be', '802.11bn'].includes(r));
    ad.band6 =
      Array.from(radios).some((r) => RADIO_6.has(r)) &&
      Array.from(radios).some((r) => ['802.11be', '802.11ax'].includes(r));
    ad.band5Possible = !ad.band5 && (radios.has('802.11n') || radios.has('802.11ac'));
    const auths = ad.auths.join(' ').toLowerCase();
    caps.wpa3Supported = caps.wpa3Supported || auths.includes('wpa3');
    caps.wpa2Supported = caps.wpa2Supported || auths.includes('wpa2');
  }

  caps.hostedSupported = caps.adapters.some((a) => a.hostedSupported);
  caps.softApSupported = softValues.find((v) => v !== null) ?? null;
  caps.wifiDirectSupported = wfdValues.find((v) => v !== null) ?? null;
  caps.band24Supported = caps.adapters.some((a) => a.band24);
  caps.band5Supported = caps.adapters.some((a) => a.band5);
  caps.band5Known = caps.adapters.some((a) => a.radios.length > 0);

  if (caps.wifiDirectSupported === true) caps.notes.push('Wi-Fi Direct 可用 · 移动热点正常');
  else if (caps.softApSupported === true) caps.notes.push('软 AP 可用 · 移动热点正常');
  else if (caps.hostedSupported) caps.notes.push('承载网络可用 · 使用 netsh 后端');
  else if (caps.softApSupported === false && caps.wifiDirectSupported === false)
    caps.notes.push('未检测到可用的热点发射能力');
  if (caps.band5Known && !caps.band5Supported) caps.notes.push('网卡为 2.4 GHz 单频');
  return caps;
}

/** 合并 WinRT 移动热点上报的频段支持（热点开启时最准）。 */
export function mergeTethering(caps: WlanCapabilities, tetherStatus: Record<string, unknown>): WlanCapabilities {
  if (!tetherStatus) return caps;
  const active = String(tetherStatus['state'] ?? '').toLowerCase() === 'on';
  const b24 = tetherStatus['band24Supported'];
  const b5 = tetherStatus['band5Supported'];
  if (active) {
    if (b24 !== undefined && b24 !== null) caps.band24Supported = Boolean(b24);
    if (b5 !== undefined && b5 !== null) {
      caps.band5Supported = Boolean(b5);
      caps.band5Known = true;
    }
  } else if (b5 === true) {
    caps.band5Supported = true;
    caps.band5Known = true;
  }
  return caps;
}
