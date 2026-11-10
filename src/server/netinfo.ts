/**
 * 网络信息采集（对应原项目 hotspot/core/netinfo.py）：
 * 网卡列表、热点网卡定位、ARP 表、主机名反查、网卡流量计数。
 */
import { lookup as dnsLookup } from 'node:dns';
import { spawnSync } from 'node:child_process';
import { normalizeMac } from './util.ts';
import { IS_WIN, psJson, run, runConsole } from './pshell.ts';
import { getLogger } from './paths.ts';

const log = getLogger('netinfo');

/** ICS(Internet 连接共享) 默认给热点网卡分配的地址 */
export const ICS_DEFAULT_IP = '192.168.137.1';

const MAC_RE = /([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}/;
const IPV4_RE = /\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b/;

export interface Adapter {
  name: string;
  description: string;
  status: string;
  mac: string;
  index: number;
  ipv4: string;
  isHotspot: boolean;
}

export async function listAdapters(): Promise<Adapter[]> {
  const data = (await psJson(
    '$a=Get-NetAdapter -ErrorAction SilentlyContinue | ' +
      'Select-Object Name,InterfaceDescription,Status,MacAddress,InterfaceIndex;' +
      '$ip=Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ' +
      'Select-Object InterfaceIndex,IPAddress;' +
      'ConvertTo-Json -Depth 4 -Compress @{adapters=@($a);ips=@($ip)}',
    20,
  )) as { adapters?: unknown[]; ips?: unknown[] } | null;
  const out: Adapter[] = [];
  if (!data || typeof data !== 'object') return out;
  const ipMap = new Map<number, string>();
  for (const item of data.ips ?? []) {
    if (item && typeof item === 'object') {
      const it = item as Record<string, unknown>;
      const idx = Math.trunc(Number(it.InterfaceIndex) || 0);
      const addr = String(it.IPAddress ?? '');
      if (idx && addr && !addr.startsWith('169.254')) {
        if (!ipMap.has(idx)) ipMap.set(idx, addr);
      }
    }
  }
  const adapters = Array.isArray(data.adapters) ? data.adapters : [data.adapters].filter(Boolean);
  for (const item of adapters) {
    if (!item || typeof item !== 'object') continue;
    const it = item as Record<string, unknown>;
    const idx = Math.trunc(Number(it.InterfaceIndex) || 0);
    const name = String(it.Name ?? '');
    const description = String(it.InterfaceDescription ?? '');
    const status = String(it.Status ?? '');
    const ipv4 = ipMap.get(idx) ?? '';
    const low = (description + ' ' + name).toLowerCase();
    const isHotspot =
      low.includes('wi-fi direct') ||
      low.includes('virtual adapter') ||
      name.includes('本地连接*') ||
      /^local area connection\*\s*\d+/i.test(name) ||
      ipv4 === ICS_DEFAULT_IP;
    out.push({
      name,
      description,
      status,
      mac: normalizeMac(String(it.MacAddress ?? '')),
      index: idx,
      ipv4,
      isHotspot,
    });
  }
  return out;
}

/** 定位热点(AP)网卡：优先 192.168.137.1，其次 Wi-Fi Direct 虚拟网卡。 */
export async function findHotspotAdapter(adapters?: Adapter[]): Promise<Adapter | null> {
  const ads = adapters ?? (await listAdapters());
  for (const ad of ads) if (ad.ipv4 === ICS_DEFAULT_IP) return ad;
  const upHotspots = ads.filter((a) => a.isHotspot && a.status.toLowerCase() === 'up');
  if (upHotspots.length) {
    upHotspots.sort((a, b) => Number(!a.ipv4) - Number(!b.ipv4) || a.index - b.index);
    return upHotspots[0];
  }
  const hotspots = ads.filter((a) => a.isHotspot);
  return hotspots.length ? hotspots[0] : null;
}

export async function hotspotGatewayIp(): Promise<string> {
  const ad = await findHotspotAdapter();
  if (ad && ad.ipv4) return ad.ipv4;
  return ICS_DEFAULT_IP;
}

/** 返回网卡 [接收字节, 发送字节]。 */
export async function adapterStats(name: string): Promise<[number, number] | null> {
  if (!name) return null;
  const safe = name.replaceAll("'", "''");
  const data = (await psJson(
    `$s=Get-NetAdapterStatistics -Name '${safe}' -ErrorAction Stop;` +
      'ConvertTo-Json -Compress @{rx=[int64]$s.ReceivedBytes;tx=[int64]$s.SentBytes}',
    15,
  )) as { rx?: unknown; tx?: unknown } | null;
  if (data && typeof data === 'object' && 'rx' in data) {
    const rx = Number(data.rx);
    const tx = Number(data.tx);
    if (Number.isFinite(rx) && Number.isFinite(tx)) return [rx, tx];
  }
  return null;
}

/** 解析 `arp -a`，返回 [ip, mac][]，可按网段前缀过滤。 */
export async function arpEntries(subnetPrefix = ''): Promise<[string, string][]> {
  const text = await runConsole(['arp', '-a'], 12);
  const out: [string, string][] = [];
  for (const line of text.split('\n')) {
    const l = line.trim();
    if (!l || /^(interface|接口)/i.test(l)) continue;
    const mIp = IPV4_RE.exec(l);
    const mMac = MAC_RE.exec(l);
    if (!mIp || !mMac) continue;
    const ip = mIp[1];
    const mac = normalizeMac(mMac[0]);
    if (mac === 'ff:ff:ff:ff:ff:ff' || mac === '00:00:00:00:00:00') continue;
    if (ip.endsWith('.255') || ip.startsWith('224.') || ip.startsWith('239.') || ip.startsWith('169.254')) continue;
    if (subnetPrefix && !ip.startsWith(subnetPrefix)) continue;
    out.push([ip, mac]);
  }
  return out;
}

export function subnetPrefixOf(ip: string): string {
  const parts = (ip || '').split('.');
  if (parts.length === 4) return parts.slice(0, 3).join('.') + '.';
  return '';
}

/** DNS 反查主机名（快速失败）。 */
export function resolveHostname(ip: string, timeout = 600): Promise<string> {
  if (!ip) return Promise.resolve('');
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve(''), timeout);
    dnsLookup(ip, { verbatim: true }, (err, hostname) => {
      clearTimeout(t);
      resolve(err ? '' : String(hostname || '').split('.')[0]);
    });
  });
}

/** NetBIOS 名称查询（Windows 设备常能查到）。 */
export async function nbtName(ip: string): Promise<string> {
  if (!ip) return '';
  const text = await runConsole(['nbtstat', '-A', ip], 6);
  for (const line of text.split('\n')) {
    const m = /^\s*(\S+)\s*<00>\s*UNIQUE/.exec(line);
    if (m) return m[1].trim();
  }
  return '';
}

export async function bestHostname(ip: string, useNbt = false): Promise<string> {
  let name = await resolveHostname(ip);
  if (!name && useNbt) name = await nbtName(ip);
  return name;
}

export interface IcsState {
  sharedAdapter: string;
  hotspotAdapter: string;
  gatewayIp: string;
  notes: string[];
}

/** 粗略检查共享状态，给用户提示用。 */
export async function icsState(): Promise<IcsState> {
  const ads = await listAdapters();
  const ap = await findHotspotAdapter(ads);
  const st: IcsState = { sharedAdapter: '', hotspotAdapter: '', gatewayIp: ICS_DEFAULT_IP, notes: [] };
  if (ap) {
    st.hotspotAdapter = ap.name;
    st.gatewayIp = ap.ipv4 || ICS_DEFAULT_IP;
  } else {
    st.notes.push('未发现热点网卡，热点可能尚未开启。');
  }
  const ups = ads.filter((a) => a.status.toLowerCase() === 'up' && !a.isHotspot && a.ipv4);
  if (ups.length) st.sharedAdapter = ups[0].name;
  else st.notes.push('未发现可共享的上网连接。');
  return st;
}

// re-export 未使用但保持接口完整的导入
export { spawnSync, IS_WIN, run };
