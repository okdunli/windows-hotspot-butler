/**
 * 本地存储（对应原项目 hotspot/core/storage.py）：设备档案(JSON) + 流量库(SQLite)。
 * SQLite 走 Node 22+ 自带的 node:sqlite —— 零依赖、零原生模块，懒加载（build
 * 时不得创建数据库文件）。
 */
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { normalizeMac } from './util.ts';
import { DEVICES_FILE, TRAFFIC_DB, ensureDirs, getLogger } from './paths.ts';

const log = getLogger('storage');

// --------------------------------------------------------------------------- //
//                              设备档案                                        //
// --------------------------------------------------------------------------- //
export const DEVICE_DEFAULTS: Record<string, unknown> = {
  mac: '',
  name: '',
  icon: 'auto',
  custom_icon: '',
  type: 'unknown',
  type_locked: false,
  vendor: '',
  hostname: '',
  note: '',
  last_ip: '',
  first_seen: 0,
  last_seen: 0,
  limited: false,
  portal_accepted: 0,
  seen_count: 0,
};

type DeviceRec = Record<string, unknown>;

export class DeviceStore {
  private data: Map<string, DeviceRec> = new Map();
  private dirty = false;

  constructor() {
    this.load();
  }

  load(): void {
    try {
      if (existsSync(DEVICES_FILE)) {
        const raw = JSON.parse(readFileSync(DEVICES_FILE, 'utf-8')) as Record<string, DeviceRec>;
        if (raw && typeof raw === 'object') {
          for (const [mac, rec] of Object.entries(raw)) {
            if (!rec || typeof rec !== 'object') continue;
            const item: DeviceRec = { ...DEVICE_DEFAULTS, ...rec };
            const norm = normalizeMac(mac);
            item['mac'] = norm;
            this.data.set(norm, item);
          }
        }
        log.info(`已读取 ${this.data.size} 条设备档案`);
      }
    } catch (exc) {
      log.warn(`设备档案读取失败：${String(exc)}`);
    }
  }

  save(force = false): boolean {
    if (!(this.dirty || force)) return true;
    ensureDirs();
    try {
      const tmp = DEVICES_FILE.replace(/\.json$/, '.tmp');
      const out: Record<string, DeviceRec> = {};
      for (const [k, v] of this.data) out[k] = v;
      writeFileSync(tmp, JSON.stringify(out, null, 2), 'utf-8');
      renameSync(tmp, DEVICES_FILE);
      this.dirty = false;
      return true;
    } catch (exc) {
      log.error(`设备档案保存失败：${String(exc)}`);
      return false;
    }
  }

  get(mac: string): DeviceRec {
    mac = normalizeMac(mac);
    let rec = this.data.get(mac);
    if (!rec) {
      rec = { ...DEVICE_DEFAULTS, mac, first_seen: Date.now() / 1000 };
      this.data.set(mac, rec);
      this.dirty = true;
    }
    return { ...rec };
  }

  exists(mac: string): boolean {
    return this.data.has(normalizeMac(mac));
  }

  all(): DeviceRec[] {
    return Array.from(this.data.values()).map((v) => ({ ...v }));
  }

  update(mac: string, kw: DeviceRec): DeviceRec {
    mac = normalizeMac(mac);
    let rec = this.data.get(mac);
    if (!rec) {
      rec = { ...DEVICE_DEFAULTS, mac, first_seen: Date.now() / 1000 };
      this.data.set(mac, rec);
    }
    for (const [k, v] of Object.entries(kw)) {
      if (k in DEVICE_DEFAULTS && rec[k] !== v) {
        rec[k] = v;
        this.dirty = true;
      }
    }
    return { ...rec };
  }

  touch(
    mac: string,
    opts: { ip?: string; hostname?: string; vendor?: string; devType?: string } = {},
  ): DeviceRec {
    mac = normalizeMac(mac);
    const now = Date.now() / 1000;
    let rec = this.data.get(mac);
    const isNew = !rec;
    if (isNew) {
      rec = { ...DEVICE_DEFAULTS, mac, first_seen: now };
      this.data.set(mac, rec);
    }
    rec!['last_seen'] = now;
    rec!['seen_count'] = Number(rec!['seen_count'] || 0) + (isNew ? 1 : 0);
    if (opts.ip) rec!['last_ip'] = opts.ip;
    if (opts.hostname && !rec!['hostname']) rec!['hostname'] = opts.hostname;
    if (opts.vendor && !rec!['vendor']) rec!['vendor'] = opts.vendor;
    if (opts.devType && !rec!['type_locked']) rec!['type'] = opts.devType;
    this.dirty = true;
    return { ...rec! };
  }

  delete(mac: string): void {
    mac = normalizeMac(mac);
    if (this.data.delete(mac)) {
      this.dirty = true;
      this.save(true);
    }
  }

  displayName(rec: DeviceRec): string {
    if (rec['name']) return String(rec['name']);
    if (rec['hostname']) return String(rec['hostname']);
    if (rec['vendor']) {
      const mac = String(rec['mac'] || '');
      return `${rec['vendor']} ${mac.slice(-5).replace(/:/g, '')}`;
    }
    return String(rec['mac'] || '未知设备');
  }
}

// --------------------------------------------------------------------------- //
//                                流量库                                        //
// --------------------------------------------------------------------------- //
interface SqliteDb {
  exec(sql: string): void;
  prepare(sql: string): {
    run(...args: unknown[]): { lastInsertRowid: number | bigint; changes: number | bigint };
    get(...args: unknown[]): unknown;
    all(...args: unknown[]): unknown[];
  };
  close(): void;
}

let conn: SqliteDb | undefined;
const getConn = async (): Promise<SqliteDb> => {
  if (!conn) {
    const { DatabaseSync } = await import('node:sqlite');
    ensureDirs();
    conn = new DatabaseSync(TRAFFIC_DB) as unknown as SqliteDb;
    conn.exec(`
                CREATE TABLE IF NOT EXISTS totals(
                    mac TEXT PRIMARY KEY,
                    rx INTEGER NOT NULL DEFAULT 0,
                    tx INTEGER NOT NULL DEFAULT 0,
                    updated REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS daily(
                    mac TEXT NOT NULL,
                    day TEXT NOT NULL,
                    rx INTEGER NOT NULL DEFAULT 0,
                    tx INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(mac, day)
                );
                CREATE TABLE IF NOT EXISTS sessions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mac TEXT NOT NULL,
                    started REAL NOT NULL,
                    ended REAL,
                    rx INTEGER NOT NULL DEFAULT 0,
                    tx INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS portal_visits(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    ip TEXT, mac TEXT, ua TEXT,
                    accepted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS dns_queries(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    ip TEXT, mac TEXT, domain TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_daily_day ON daily(day);
            `);
  }
  return conn;
};

export class TrafficDB {
  private q = async <T>(
      sql: string,
      args: unknown[] = [],
      _placeholder?: undefined,
      mode: 'run' | 'get' | 'all' = 'all',
    ): Promise<T> => {
      const db = await getConn();
      const stmt = db.prepare(sql);
      if (mode === 'run') return stmt.run(...args) as unknown as T;
      if (mode === 'get') return stmt.get(...args) as unknown as T;
      return stmt.all(...args) as unknown as T;
    };

  async add(mac: string, rx: number, tx: number, ts?: number): Promise<void> {
    rx = Math.max(0, Math.trunc(rx));
    tx = Math.max(0, Math.trunc(tx));
    if (!mac || (rx === 0 && tx === 0)) return;
    mac = normalizeMac(mac);
    ts = ts || Date.now() / 1000;
    const day = dayString(ts);
    await this.q(
      `INSERT INTO totals(mac, rx, tx, updated) VALUES(?,?,?,?) ` +
        `ON CONFLICT(mac) DO UPDATE SET rx=rx+excluded.rx, tx=tx+excluded.tx, updated=excluded.updated`,
      [mac, rx, tx, ts],
      undefined,
      'run',
    );
    await this.q(
      `INSERT INTO daily(mac, day, rx, tx) VALUES(?,?,?,?) ` +
        `ON CONFLICT(mac, day) DO UPDATE SET rx=rx+excluded.rx, tx=tx+excluded.tx`,
      [mac, day, rx, tx],
      undefined,
      'run',
    );
  }

  async addBatch(items: Record<string, [number, number]>): Promise<void> {
    for (const [mac, [rx, tx]] of Object.entries(items)) await this.add(mac, rx, tx);
  }

  async openSession(mac: string): Promise<number> {
    const r = await this.q<{ lastInsertRowid: number | bigint }>(
      'INSERT INTO sessions(mac, started, rx, tx) VALUES(?,?,0,0)',
      [normalizeMac(mac), Date.now() / 1000],
      undefined,
      'run',
    );
    return Number(r.lastInsertRowid || 0);
  }

  async closeSession(sid: number, rx: number, tx: number): Promise<void> {
    if (!sid) return;
    await this.q(
      'UPDATE sessions SET ended=?, rx=?, tx=? WHERE id=?',
      [Date.now() / 1000, Math.trunc(rx), Math.trunc(tx), sid],
      undefined,
      'run',
    );
  }

  async recordPortalVisit(ip: string, mac: string, ua: string, accepted: boolean): Promise<void> {
    await this.q(
      'INSERT INTO portal_visits(ts, ip, mac, ua, accepted) VALUES(?,?,?,?,?)',
      [Date.now() / 1000, ip, normalizeMac(mac), (ua || '').slice(0, 220), accepted ? 1 : 0],
      undefined,
      'run',
    );
  }

  async totalFor(mac: string): Promise<[number, number]> {
    const row = (await this.q<{ rx: number; tx: number } | undefined>(
      'SELECT rx, tx FROM totals WHERE mac=?',
      [normalizeMac(mac)],
      undefined,
      'get',
    )) as { rx: number; tx: number } | undefined;
    return row ? [Number(row.rx), Number(row.tx)] : [0, 0];
  }

  async totalsMap(): Promise<Record<string, [number, number]>> {
    const rows = (await this.q<{ mac: string; rx: number; tx: number }[]>(
      'SELECT mac, rx, tx FROM totals',
    )) as { mac: string; rx: number; tx: number }[];
    const out: Record<string, [number, number]> = {};
    for (const r of rows) out[r.mac] = [Number(r.rx), Number(r.tx)];
    return out;
  }

  async grandTotal(): Promise<[number, number]> {
    const row = (await this.q<{ rx: number; tx: number } | undefined>(
      'SELECT COALESCE(SUM(rx),0) rx, COALESCE(SUM(tx),0) tx FROM totals',
      undefined,
      undefined,
      'get',
    )) as { rx: number; tx: number } | undefined;
    return row ? [Number(row.rx), Number(row.tx)] : [0, 0];
  }

  async daily(mac: string, days = 14): Promise<[string, number, number][]> {
    mac = normalizeMac(mac);
    const start = dateOffset(-(days - 1));
    const rows = (await this.q<{ day: string; rx: number; tx: number }[]>(
      'SELECT day, rx, tx FROM daily WHERE mac=? AND day>=? ORDER BY day',
      [mac, start],
    )) as { day: string; rx: number; tx: number }[];
    const got: Record<string, [number, number]> = {};
    for (const r of rows) got[r.day] = [Number(r.rx), Number(r.tx)];
    return fillDays(start, days, got);
  }

  async dailyAll(days = 14): Promise<[string, number, number][]> {
    const start = dateOffset(-(days - 1));
    const rows = (await this.q<{ day: string; rx: number; tx: number }[]>(
      'SELECT day, SUM(rx) rx, SUM(tx) tx FROM daily WHERE day>=? GROUP BY day ORDER BY day',
      [start],
    )) as { day: string; rx: number; tx: number }[];
    const got: Record<string, [number, number]> = {};
    for (const r of rows) got[r.day] = [Number(r.rx), Number(r.tx)];
    return fillDays(start, days, got);
  }

  async dailyByDevice(days = 30): Promise<{ day: string; mac: string; rx: number; tx: number }[]> {
    const start = dateOffset(-(days - 1));
    const rows = (await this.q<{ day: string; mac: string; rx: number; tx: number }[]>(
      'SELECT day, mac, rx, tx FROM daily WHERE day>=? ORDER BY day, (rx+tx) DESC',
      [start],
    )) as { day: string; mac: string; rx: number; tx: number }[];
    return rows.map((r) => ({ day: r.day, mac: r.mac, rx: Number(r.rx), tx: Number(r.tx) }));
  }

  async topDevices(limit = 8): Promise<{ mac: string; rx: number; tx: number }[]> {
    const rows = (await this.q<{ mac: string; rx: number; tx: number }[]>(
      'SELECT mac, rx, tx FROM totals ORDER BY (rx+tx) DESC LIMIT ?',
      [limit],
    )) as { mac: string; rx: number; tx: number }[];
    return rows.map((r) => ({ mac: r.mac, rx: Number(r.rx), tx: Number(r.tx) }));
  }

  async sessions(mac: string, limit = 10): Promise<Record<string, unknown>[]> {
    return (await this.q('SELECT started, ended, rx, tx FROM sessions WHERE mac=? ORDER BY started DESC LIMIT ?', [
      normalizeMac(mac),
      limit,
    ])) as Record<string, unknown>[];
  }

  async portalVisits(limit = 50): Promise<Record<string, unknown>[]> {
    return (await this.q(
      'SELECT ts, ip, mac, ua, accepted FROM portal_visits ORDER BY ts DESC LIMIT ?',
      [limit],
    )) as Record<string, unknown>[];
  }

  async recordDnsQuery(ip: string, mac: string, domain: string): Promise<void> {
    if (!domain) return;
    await this.q('INSERT INTO dns_queries(ts, ip, mac, domain) VALUES(?,?,?,?)', [
      Date.now() / 1000,
      ip || '',
      normalizeMac(mac),
      domain.slice(0, 120),
    ], undefined, 'run');
    // 简单滚动清理：只保留最近 5000 条
    await this.q(
      'DELETE FROM dns_queries WHERE id NOT IN (SELECT id FROM dns_queries ORDER BY id DESC LIMIT 5000)',
      undefined,
      undefined,
      'run',
    );
  }

  async dnsQueries(limit = 100): Promise<Record<string, unknown>[]> {
    return (await this.q(
      'SELECT ts, ip, mac, domain FROM dns_queries ORDER BY id DESC LIMIT ?',
      [limit],
    )) as Record<string, unknown>[];
  }

  async topDomains(limit = 10): Promise<{ domain: string; c: number }[]> {
    return (await this.q(
      'SELECT domain, COUNT(*) c FROM dns_queries GROUP BY domain ORDER BY c DESC LIMIT ?',
      [limit],
    )) as { domain: string; c: number }[];
  }

  async resetDevice(mac: string): Promise<void> {
    mac = normalizeMac(mac);
    for (const table of ['totals', 'daily', 'sessions', 'dns_queries']) {
      await this.q(`DELETE FROM ${table} WHERE mac=?`, [mac], undefined, 'run');
    }
  }

  async close(): Promise<void> {
    try {
      (await getConn()).close();
      conn = undefined;
    } catch {
      /* ignore */
    }
  }
}

function dayString(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function dateOffset(deltaDays: number): string {
  return dayString(Date.now() / 1000 + deltaDays * 86400);
}

function fillDays(start: string, days: number, got: Record<string, [number, number]>): [string, number, number][] {
  const out: [string, number, number][] = [];
  const startDate = new Date(start + 'T00:00:00');
  for (let i = 0; i < days; i++) {
    const d = dayString(startDate.getTime() / 1000 + i * 86400);
    const [rx, tx] = got[d] ?? [0, 0];
    out.push([d, rx, tx]);
  }
  return out;
}
