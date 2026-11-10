/**
 * 设备管理器（对应原项目 hotspot/core/deviceman.py）：
 * 把「热点上报的客户端 / ARP / 设备档案 / 流量」合并成统一视图。
 */
import * as netinfo from './netinfo.ts';
import * as oui from './oui.ts';
import { normalizeMac, timeNow } from './util.ts';
import type { DeviceStore } from './storage.ts';
import type { TrafficDB } from './storage.ts';
import type { TrafficMonitor } from './traffic.ts';
import type { RawClient } from './hotspot.ts';
import { getLogger } from './paths.ts';

const log = getLogger('deviceman');

export interface DeviceInfo {
  mac: string;
  ip: string;
  online: boolean;
  /** 展示名（自定义名 > 主机名 > 厂商+尾号 > MAC） */
  name: string;
  customName: string;
  hostname: string;
  vendor: string;
  devType: string;
  icon: string;
  customIcon: string;
  note: string;
  limited: boolean;
  firstSeen: number;
  lastSeen: number;
  portalAccepted: number;
  rateDown: number;
  rateUp: number;
  sessionRx: number;
  sessionTx: number;
  totalRx: number;
  totalTx: number;
  source: string;
}

function emptyInfo(): DeviceInfo {
  return {
    mac: '',
    ip: '',
    online: false,
    name: '',
    customName: '',
    hostname: '',
    vendor: '',
    devType: 'unknown',
    icon: 'auto',
    customIcon: '',
    note: '',
    limited: false,
    firstSeen: 0,
    lastSeen: 0,
    portalAccepted: 0,
    rateDown: 0,
    rateUp: 0,
    sessionRx: 0,
    sessionTx: 0,
    totalRx: 0,
    totalTx: 0,
    source: '',
  };
}

function num(v: unknown, dflt = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : dflt;
}

export class DeviceManager {
  store: DeviceStore;
  db: TrafficDB;
  traffic: TrafficMonitor;
  private cache: Map<string, DeviceInfo> = new Map();
  onlineMacs: Set<string> = new Set();

  constructor(store: DeviceStore, db: TrafficDB, traffic: TrafficMonitor) {
    this.store = store;
    this.db = db;
    this.traffic = traffic;
  }

  /** 合并热点客户端 + ARP + 档案 + 流量，返回按在线优先排序的设备列表。 */
  async sync(
    rawClients: readonly RawClient[],
    opts: { active?: boolean; gatewayIp?: string; resolveNames?: boolean; arpCache?: Record<string, string> | null } = {},
  ): Promise<DeviceInfo[]> {
    const active = opts.active ?? true;
    const resolveNames = opts.resolveNames ?? true;
    const gw = opts.gatewayIp || (await netinfo.hotspotGatewayIp());
    const prefix = netinfo.subnetPrefixOf(gw);

    const merged = new Map<string, string>();
    const sources = new Map<string, string>();
    for (const rc of rawClients) {
      const mac = normalizeMac(rc.mac);
      if (!mac) continue;
      if (!merged.has(mac) || !merged.get(mac)) merged.set(mac, rc.ip || merged.get(mac) || '');
      sources.set(mac, rc.source || 'hotspot');
    }

    // ARP 补充同网段设备（有些设备 WinRT 不报）
    if (active) {
      try {
        const entries =
          opts.arpCache ?? Object.fromEntries((await netinfo.arpEntries(prefix)).map(([ip, mac]) => [ip, mac]));
        for (const [ip, mac] of Object.entries(entries)) {
          if (ip === gw) continue;
          if (!merged.has(mac) || !merged.get(mac)) merged.set(mac, ip);
          if (!sources.has(mac)) sources.set(mac, 'arp');
        }
      } catch (exc) {
        log.debug(`ARP 补充失败：${String(exc)}`);
      }
    }

    const online = new Set(merged.keys());
    this.onlineMacs = online;

    const result: DeviceInfo[] = [];
    for (const [mac, ip] of merged.entries()) {
      const rec = this.store.touch(mac, { ip });
      const info = this.build(mac, ip, rec, true);
      info.source = sources.get(mac) ?? '';
      if (!info.hostname && resolveNames && ip) {
        info.hostname = await netinfo.bestHostname(ip, false);
        if (!info.hostname) info.hostname = await netinfo.nbtName(ip);
        if (info.hostname) {
          this.store.update(mac, { hostname: info.hostname });
          info.name = this.store.displayName(this.store.get(mac));
        }
      }
      result.push(info);
    }

    // 历史设备（离线）
    for (const rec of this.store.all()) {
      const mac = String(rec['mac'] ?? '');
      if (!mac || merged.has(mac)) continue;
      result.push(this.build(mac, String(rec['last_ip'] ?? ''), rec, false));
    }

    result.sort((a, b) => {
      if (a.online !== b.online) return a.online ? -1 : 1;
      const ra = a.rateDown + a.rateUp;
      const rb = b.rateDown + b.rateUp;
      if (ra !== rb) return rb - ra;
      return (a.name || a.mac).toLowerCase() < (b.name || b.mac).toLowerCase() ? -1 : 1;
    });
    this.cache = new Map(result.map((d) => [d.mac, d]));
    try {
      this.traffic.setKnownMacs(result.map((d) => d.mac));
    } catch {
      /* ignore */
    }
    return result;
  }

  private build(mac: string, ip: string, rec: Record<string, unknown>, online: boolean): DeviceInfo {
    let vendor = String(rec['vendor'] ?? '');
    if (!vendor) {
      vendor = oui.lookupVendor(mac);
      if (vendor) this.store.update(mac, { vendor });
    }
    let devType = String(rec['type'] ?? 'unknown');
    if (!rec['type_locked']) {
      const guessed = oui.guessType(mac, String(rec['hostname'] ?? ''), vendor);
      if (guessed !== devType) {
        devType = guessed;
        this.store.update(mac, { type: devType });
      }
    }
    const [rateDown, rateUp] = this.traffic.rateOf(mac);
    const [srx, stx] = this.traffic.sessionOf(mac);
    const info = emptyInfo();
    info.mac = mac;
    info.ip = ip || String(rec['last_ip'] ?? '');
    info.online = online;
    info.customName = String(rec['name'] ?? '');
    info.hostname = String(rec['hostname'] ?? '');
    info.vendor = vendor;
    info.devType = devType;
    info.icon = String(rec['icon'] ?? 'auto');
    info.customIcon = String(rec['custom_icon'] ?? '');
    info.note = String(rec['note'] ?? '');
    info.limited = Boolean(rec['limited']);
    info.firstSeen = num(rec['first_seen']);
    info.lastSeen = num(rec['last_seen']);
    info.portalAccepted = num(rec['portal_accepted']);
    info.rateDown = rateDown;
    info.rateUp = rateUp;
    info.sessionRx = srx;
    info.sessionTx = stx;
    info.name = this.store.displayName(rec);
    return info;
  }

  /** totalRx/totalTx 需查库，与原版一致放到 build 之外的补充步骤。 */
  async attachTotals(infos: DeviceInfo[]): Promise<void> {
    const totals = await this.db.totalsMap();
    for (const d of infos) {
      const t = totals[d.mac];
      if (t) {
        d.totalRx = t[0];
        d.totalTx = t[1];
      } else {
        d.totalRx = 0;
        d.totalTx = 0;
      }
    }
  }

  all(): DeviceInfo[] {
    return Array.from(this.cache.values());
  }

  get(mac: string): DeviceInfo | null {
    return this.cache.get(normalizeMac(mac)) ?? null;
  }

  rename(mac: string, name: string): void {
    this.store.update(mac, { name: (name || '').trim() });
    this.store.save();
  }

  setIcon(mac: string, icon: string, customPath = ''): void {
    this.store.update(mac, { icon, custom_icon: customPath || '' });
    if (icon !== 'auto' && icon !== 'custom') {
      this.store.update(mac, { type: icon, type_locked: true });
    }
    this.store.save();
  }

  setType(mac: string, devType: string, lock = true): void {
    this.store.update(mac, { type: devType, type_locked: Boolean(lock) });
    this.store.save();
  }

  setNote(mac: string, note: string): void {
    this.store.update(mac, { note });
    this.store.save();
  }

  setLimited(mac: string, limited: boolean): void {
    this.store.update(mac, { limited: Boolean(limited) });
    this.store.save();
  }

  forget(mac: string): void {
    this.store.delete(mac);
    this.cache.delete(normalizeMac(mac));
  }

  async resolveHostname(mac: string, useNbt = true): Promise<string> {
    const info = this.get(mac);
    if (!info || !info.ip) return '';
    const name = await netinfo.bestHostname(info.ip, useNbt);
    if (name) this.store.update(mac, { hostname: name });
    return name;
  }

  /** 返回 [在线数, 设备总数, 本次会话总下行, 本次会话总上行]。 */
  statsSummary(): [number, number, number, number] {
    const list = this.all();
    const online = list.filter((d) => d.online).length;
    const srx = list.reduce((a, d) => a + d.sessionRx, 0);
    const stx = list.reduce((a, d) => a + d.sessionTx, 0);
    return [online, list.length, srx, stx];
  }
}

export { timeNow };
