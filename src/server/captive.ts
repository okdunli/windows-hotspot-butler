/**
 * 强制门户（对应原项目 hotspot/core/captive.py）：
 * DNS 劫持 + 80 端口门户 + 白名单放行。
 *
 * 1. DNS 劫持：未同意设备的 A 查询全部指向热点网关；已同意设备转发上游。
 * 2. 探测路径应答：未同意 → 302 到门户页；已同意 → 按各家规定内容返回。
 *
 * Windows 的 ICS(SharedAccess) 自带 DNS 代理会占用 UDP 53，抢占需要管理员权限，
 * 且短暂中断共享上网，只在用户主动开启时执行。
 */
import { createSocket, type Socket } from 'node:dgram';
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { lookup as dnsLookup } from 'node:dns';
import { hostname } from 'node:os';
import { esc, normalizeMac, timeNow } from './util.ts';
import { DATA_DIR, ensureDirs, getLogger } from './paths.ts';
import { isAdmin, run } from './pshell.ts';
import { PROBE_PATHS, templatePath } from './portal.ts';
import type { PortalContext } from './portal.ts';
import type { PortalConfig } from './config.ts';

const log = getLogger('captive');

export const UPSTREAM_DNS = ['223.5.5.5', '119.29.29.29'];
export const ACCEPT_FILE: string = join(DATA_DIR, 'portal_accepted.json');

/** 已放行设备的探测应答（各系统约定的"已联网"内容） */
const PROBE_OK: Record<string, [number, Buffer]> = {
  '/generate_204': [204, Buffer.alloc(0)],
  '/gen_204': [204, Buffer.alloc(0)],
  '/hotspot-detect.html': [200, Buffer.from('<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>')],
  '/library/test/success.html': [200, Buffer.from('<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>')],
  '/ncsi.txt': [200, Buffer.from('Microsoft NCSI')],
  '/connecttest.txt': [200, Buffer.from('Microsoft Connect Test')],
  '/success.txt': [200, Buffer.from('success')],
  '/redirect': [200, Buffer.from('<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>')],
};

// --------------------------------------------------------------------------- //
//                                白名单存储                                    //
// --------------------------------------------------------------------------- //
export interface AllowDetail {
  mac: string;
  ua: string;
  at: string;
}

export class AllowList {
  path: string;
  ips: Set<string> = new Set();
  macs: Set<string> = new Set();
  detail: Record<string, AllowDetail> = {};

  constructor(path: string = ACCEPT_FILE) {
    this.path = path;
    this.load();
  }

  load(): void {
    try {
      if (existsSync(this.path)) {
        const data = JSON.parse(readFileSync(this.path, 'utf-8')) as {
          ips?: unknown;
          macs?: unknown;
          detail?: Record<string, AllowDetail>;
        };
        this.ips = new Set(((data.ips as unknown[]) ?? []).map((i) => String(i)));
        this.macs = new Set(((data.macs as unknown[]) ?? []).map((m) => String(m).toUpperCase()));
        this.detail = { ...(data.detail ?? {}) };
      }
    } catch {
      log.debug('放行名单读取失败，按空名单处理');
    }
  }

  save(): void {
    try {
      ensureDirs();
      const data = {
        ips: [...this.ips].sort(),
        macs: [...this.macs].sort(),
        detail: this.detail,
      };
      const tmp = this.path.replace(/\.json$/, '.tmp');
      writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf-8');
      renameSync(tmp, this.path);
    } catch (exc) {
      log.warn(`放行名单保存失败：${String(exc)}`);
    }
  }

  has(ip = '', mac = ''): boolean {
    if (mac && this.macs.has(mac.toUpperCase())) return true;
    return Boolean(ip) && this.ips.has(ip);
  }

  allow(ip: string, mac = '', ua = ''): void {
    if (ip) {
      this.ips.add(ip);
      this.detail[ip] = {
        mac: (mac || '').toUpperCase(),
        ua: (ua || '').slice(0, 200),
        at: new Date().toLocaleString('sv-SE').replace('T', ' '),
      };
    }
    if (mac) this.macs.add(mac.toUpperCase());
    this.save();
  }

  revoke(ip = '', mac = ''): void {
    if (ip) {
      this.ips.delete(ip);
      delete this.detail[ip];
    }
    if (mac) this.macs.delete(mac.toUpperCase());
    this.save();
  }

  clear(): void {
    this.ips.clear();
    this.macs.clear();
    this.detail = {};
    this.save();
  }

  snapshot(): (AllowDetail & { ip: string })[] {
    return Object.entries(this.detail).map(([ip, v]) => ({ ...v, ip }));
  }
}

// --------------------------------------------------------------------------- //
//                                 DNS 代理                                     //
// --------------------------------------------------------------------------- //
export function parseQname(data: Buffer): [string, number] {
  const parts: string[] = [];
  let i = 12;
  while (i < data.length) {
    const ln = data[i];
    if (ln === 0) {
      i += 1;
      break;
    }
    parts.push(data.subarray(i + 1, i + 1 + ln).toString('latin1'));
    i += 1 + ln;
  }
  return [parts.join('.'), i];
}

function aResponse(data: Buffer, end: number, ip: string): Buffer {
  const tid = data.subarray(0, 2);
  const head = Buffer.alloc(12);
  tid.copy(head, 0);
  head.writeUInt16BE(0x8180, 2);
  head.writeUInt16BE(1, 4);
  head.writeUInt16BE(1, 6);
  const answer = Buffer.alloc(16);
  answer.writeUInt16BE(0xc00c, 0);
  answer.writeUInt16BE(1, 2);
  answer.writeUInt16BE(1, 4);
  answer.writeUInt32BE(30, 6);
  answer.writeUInt16BE(4, 10);
  const octets = ip.split('.').map((x) => Number(x) & 0xff);
  for (let i = 0; i < 4; i++) answer.writeUInt8(octets[i] ?? 0, 12 + i);
  return Buffer.concat([head, data.subarray(12, end + 4), answer]);
}

function emptyResponse(data: Buffer, end: number): Buffer {
  // NOERROR 但无记录（用于 AAAA / HTTPS 等，避免设备走 IPv6 绕过门户）
  const tid = data.subarray(0, 2);
  const head = Buffer.alloc(12);
  tid.copy(head, 0);
  head.writeUInt16BE(0x8180, 2);
  head.writeUInt16BE(1, 4);
  head.writeUInt16BE(0, 6);
  return Buffer.concat([head, data.subarray(12, end + 4)]);
}

export class DnsProxy {
  gateway: string;
  allow: AllowList;
  upstream: string[];
  error = '';
  queryCount = 0;
  hijackCount = 0;
  onQuery: ((ip: string, domain: string) => void) | null = null;

  private sock: Socket | null = null;
  private stopFlag = false;
  private localCache: Set<string> | null = null;

  constructor(gateway: string, allow: AllowList, upstream: string[] = UPSTREAM_DNS) {
    this.gateway = gateway;
    this.allow = allow;
    this.upstream = upstream;
  }

  bind(): boolean {
    try {
      const s = createSocket('udp4');
      s.on('message', (data, rinfo) => void this.handle(data, rinfo, s));
      s.on('error', (err) => {
        this.error = `UDP 53 绑定失败：${err.message}`;
      });
      s.bind(53);
      this.sock = s;
      return true;
    } catch (err) {
      this.error = `UDP 53 绑定失败：${String(err)}`;
      return false;
    }
  }

  private async handle(data: Buffer, rinfo: { address: string; port: number }, sock: Socket): Promise<void> {
    const src = rinfo?.address ?? '';
    try {
      const resp = await this.resolve(data, src);
      if (resp) sock.send(resp, rinfo.port, rinfo.address);
    } catch {
      /* ignore */
    }
  }

  /** 本机自身的所有 IP（含网关、回环）——来自这些地址的查询绝不劫持。 */
  private localIps(): Set<string> {
    if (this.localCache !== null) return this.localCache;
    const ips = new Set<string>(['127.0.0.1', '::1', 'localhost']);
    const names = new Set<string>([hostname()]);
    let pending = names.size;
    let done = 0;
    const finish = () => {
      if (this.gateway) ips.add(this.gateway);
      this.localCache = ips;
    };
    const cb = (err: unknown, addr: string) => {
      if (!err && addr && addr !== '127.0.0.1' && addr !== '::1') ips.add(addr);
      done++;
      if (done >= pending) finish();
    };
    for (const n of names) dnsLookup(n, { all: false }, cb);
    if (pending === 0) finish();
    return ips; // 首次调用可能还没收全，多轮后收敛
  }

  private async resolve(data: Buffer, src: string): Promise<Buffer | null> {
    if (data.length < 12) return null;
    const flags = data.readUInt16BE(2);
    if (flags & 0x8000) return null; // 不是查询
    let qname = '';
    let end = 0;
    let qtype = 0;
    try {
      [qname, end] = parseQname(data);
      qtype = data.readUInt16BE(end);
    } catch {
      return null;
    }
    this.queryCount++;
    if (this.onQuery && qname) {
      try {
        this.onQuery(src, qname);
      } catch {
        /* ignore */
      }
    }
    // 本机自身 / 已放行设备正常转发；其余未放行客户端的 A 记录劫持到网关
    if (this.localIps().has(src) || this.allow.has(src)) return this.forward(data);
    if (qtype === 1) {
      this.hijackCount++;
      return aResponse(data, end, this.gateway);
    }
    return emptyResponse(data, end);
  }

  private forward(data: Buffer): Promise<Buffer | null> {
    return new Promise((resolve) => {
      let idx = 0;
      const tryNext = (): void => {
        if (idx >= this.upstream.length) {
          resolve(null);
          return;
        }
        const up = this.upstream[idx++];
        const s = createSocket('udp4');
        const timer = setTimeout(() => {
          try {
            s.close();
          } catch {
            /* ignore */
          }
          tryNext();
        }, 1500);
        s.once('message', (resp) => {
          clearTimeout(timer);
          try {
            s.close();
          } catch {
            /* ignore */
          }
          resolve(resp);
        });
        s.once('error', () => {
          clearTimeout(timer);
          try {
            s.close();
          } catch {
            /* ignore */
          }
          tryNext();
        });
        s.send(data, 53, up);
      };
      tryNext();
    });
  }

  stop(): void {
    this.stopFlag = true;
    try {
      this.sock?.close();
    } catch {
      /* ignore */
    }
    this.sock = null;
  }
}

// --------------------------------------------------------------------------- //
//                                 HTTP 门户                                    //
// --------------------------------------------------------------------------- //
export interface CaptiveServerOpts {
  port: number;
  cfg: PortalConfig;
  ctx: PortalContext;
  allow: AllowList;
  values: () => Promise<Record<string, string>>;
  accept: (ip: string, mac: string, ua: string) => Promise<void> | void;
  templateName?: string;
  customIndex?: string | null;
  assetsRoot?: string | null;
  verified?: AllowList;
  requireAccept?: boolean;
  successPageKind?: 'captive' | 'welcome';
  onScheduleDenied?: (ip: string) => Promise<void> | void;
}

export interface CaptiveServer {
  close(): void;
  scheduleOpen(): boolean;
  readonly port: number;
}

export function defaultPortalConfig(): PortalConfig {
  return {
    enabled: false,
    template: 'aurora',
    custom_html: '',
    port: 8080,
    dns_redirect: false,
    title: '欢迎接入 {{ssid}}',
    subtitle: '连接成功，请阅读下方须知后开始上网',
    notice: '本无线网络仅供访客临时使用。\n如需帮助请联系网络管理员。',
    button: '同意并开始上网',
    footer: '',
    require_accept: true,
    auto_open_preview: true,
    access_password: '',
    schedule_start: 0,
    schedule_end: 24,
  };
}

const BASE_CSS =
  "body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:" +
  "-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;" +
  'background:linear-gradient(150deg,#0b1026,#1b2350);color:#eaf0ff}';

function passwordPage(error = ''): Buffer {
  const err = error ? `<p class="err">${esc(error)}</p>` : '';
  return Buffer.from(
    "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>" +
      "<meta name='viewport' content='width=device-width,initial-scale=1'>" +
      "<title>访问验证</title><style>" +
      BASE_CSS +
      ".card{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.16);" +
      'border-radius:20px;padding:32px 28px;width:min(90vw,360px);text-align:center;' +
      'backdrop-filter:blur(14px)}' +
      "h1{font-size:19px;margin-bottom:8px}p{color:#9aa7d4;font-size:13px;margin-bottom:18px}" +
      "input{width:100%;padding:12px 14px;border-radius:11px;border:1px solid rgba(255,255,255,.2);" +
      'background:rgba(0,0,0,.25);color:#fff;font-size:15px;outline:none;margin-bottom:14px}' +
      "button{width:100%;padding:13px;border:0;border-radius:12px;cursor:pointer;font-size:15px;" +
      "font-weight:600;color:#fff;background:linear-gradient(135deg,#5b8cff,#7c5cff)}" +
      ".err{color:#ff8a80;font-size:12.5px;margin-top:10px}" +
      "</style></head><body><div class='card'>" +
      '<h1>🔒 访问验证</h1><p>本网络需要访问密码，请向网络所有者获取</p>' +
      "<form method='post' action='/verify'>" +
      "<input type='password' name='pw' placeholder='访问密码' autofocus required>" +
      '<button type="submit">验证并继续</button></form>' +
      err +
      '</div></body></html>',
    'utf-8',
  );
}

function scheduleDeniedPage(s: number, e: number): Buffer {
  return Buffer.from(
    "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>" +
      "<meta name='viewport' content='width=device-width,initial-scale=1'>" +
      "<title>不在上网时段</title><style>" +
      BASE_CSS +
      'text-align:center}' +
      '.box{padding:40px}.emo{font-size:52px;margin-bottom:14px}' +
      'h1{font-size:20px;margin-bottom:10px}p{color:#9aa7d4;font-size:14px;line-height:1.8}' +
      "</style></head><body><div class='box'><div class='emo'>🌙</div>" +
      `<h1>当前不在上网时段</h1><p>本网络开放时间：${String(s).padStart(2, '0')}:00 - ${String(e % 24).padStart(2, '0')}:00<br>` +
      '请在开放时段内再连接。</p></div></body></html>',
    'utf-8',
  );
}

function successPage(kind: 'captive' | 'welcome', gateway: string): Buffer {
  const back = kind === 'welcome' ? `<a href='/'>返回欢迎页</a>` : '';
  return Buffer.from(
    "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>" +
      "<meta name='viewport' content='width=device-width,initial-scale=1'>" +
      "<title>已接入</title><style>" +
      BASE_CSS +
      '.box{text-align:center;padding:40px}' +
      '.tick{width:74px;height:74px;border-radius:50%;margin:0 auto 22px;display:grid;' +
      'place-items:center;background:linear-gradient(135deg,#22c55e,#16a34a);' +
      'box-shadow:0 12px 34px rgba(34,197,94,.4);font-size:36px;color:#fff;' +
      'animation:pop .45s cubic-bezier(.2,1.4,.4,1)}' +
      '@keyframes pop{from{transform:scale(.4);opacity:0}to{transform:none;opacity:1}}' +
      'h1{font-size:22px;margin-bottom:10px}p{color:#9aa7d4;font-size:14px;line-height:1.8}' +
      "a{display:inline-block;margin-top:22px;color:#79c0ff;text-decoration:none;font-size:13px}" +
      "</style></head><body><div class='box'>" +
      '<div class="tick">&#10003;</div>' +
      '<h1>已成功接入网络</h1>' +
      '<p>你现在可以自由上网了。<br>Powered by Windows Hotspot Butler.</p>' +
      back +
      '</div></body></html>',
    'utf-8',
  );
}

/** 启动强制门户 HTTP 服务（含密码验证 / 时段限制 / 探测应答 / 放行）。 */
export function startCaptiveServer(opts: CaptiveServerOpts): CaptiveServer {
  const cfg = opts.cfg;
  const ctx = opts.ctx;
  const allow = opts.allow;
  const verified = opts.verified ?? new AllowList(join(dirname(ACCEPT_FILE), 'portal_verified.json'));
  const successKind = opts.successPageKind ?? 'captive';

  const scheduleOpen = (): boolean => {
    const s = Math.trunc(cfg.schedule_start);
    const e = Math.trunc(cfg.schedule_end);
    if (s === e) return true;
    const h = new Date().getHours();
    if (s < e) return s <= h && h < e;
    return h >= s || h < e; // 跨午夜（如 22:00-7:00）
  };

  const server: Server = createServer((req, res) => {
    void handle(req, res).catch((exc) => {
      log.debug(`captive 处理异常：${String(exc)}`);
      try {
        res.destroy();
      } catch {
        /* ignore */
      }
    });
  });

  const send = (
    res: ServerResponse,
    code: number,
    body: Buffer = Buffer.alloc(0),
    ctype = 'text/html; charset=utf-8',
    extra: Record<string, string> = {},
    req?: IncomingMessage,
  ): void => {
    try {
      res.writeHead(code, {
        'Content-Type': ctype,
        'Content-Length': String(body.length),
        'Cache-Control': 'no-store, no-cache, must-revalidate',
        ...extra,
      });
      res.end(req && req.method === 'HEAD' ? undefined : body);
    } catch {
      /* broken pipe */
    }
  };

  const redirect = (res: ServerResponse, location: string): void => {
    send(res, 302, Buffer.alloc(0), 'text/html; charset=utf-8', { Location: location });
  };

  const portalPage = async (): Promise<Buffer> => {
    let tplName = opts.templateName ?? cfg.template ?? 'aurora';
    if (tplName === 'custom' && opts.customIndex && existsSync(opts.customIndex)) {
      const raw = readFileSync(opts.customIndex, 'utf-8');
      return Buffer.from(renderCustom(raw, await opts.values()), 'utf-8');
    }
    let tplPath = templatePath(tplName);
    if (tplPath === null) tplPath = templatePath('aurora');
    if (tplPath === null) return Buffer.from('<h1>Portal template missing</h1>', 'utf-8');
    const raw = readFileSync(tplPath, 'utf-8');
    return Buffer.from(renderTemplatePublic(raw, await opts.values()), 'utf-8');
  };

  const readBody = (req: IncomingMessage): Promise<Buffer> =>
    new Promise((resolve) => {
      const chunks: Buffer[] = [];
      req.on('data', (d) => chunks.push(d));
      req.on('end', () => resolve(Buffer.concat(chunks)));
      req.on('error', () => resolve(Buffer.alloc(0)));
    });

  const clientIp = (req: IncomingMessage): string =>
    (req.socket.remoteAddress ?? '').replace(/^::ffff:/, '');

  const handle = async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    const ip = clientIp(req);
    const path = (req.url ?? '/').split('?')[0];
    const ua = String(req.headers['user-agent'] ?? '');

    if (path === '/favicon.ico') {
      send(res, 204);
      return;
    }

    const macAllowed = async (): Promise<boolean> => {
      try {
        const mac = ip ? await ctx.macOfIp(ip) : '';
        return Boolean(mac) && allow.has('', mac);
      } catch {
        return false;
      }
    };
    const allowed = allow.has(ip) || (await macAllowed());

    if (PROBE_PATHS.includes(path)) {
      if (allowed) {
        const [code, body] = PROBE_OK[path] ?? [204, Buffer.alloc(0)];
        send(res, code, body);
      } else {
        // 关键：未同意时必须重定向，系统才会弹出门户页
        redirect(res, `http://${(await ctx.getGateway()) || '192.168.137.1'}/?from=probe`);
      }
      return;
    }

    if (allowed) {
      if (path === '/' || path === '/index.html' || path === '/portal') {
        send(res, 200, successPage(successKind, (await ctx.getGateway()) || '192.168.137.1'));
      } else {
        send(res, 404, Buffer.from('<h1>404</h1>', 'utf-8'));
      }
      return;
    }

    if (req.method === 'POST') {
      if (path === '/verify') {
        const body = await readBody(req);
        const pw = /(?:^|&)pw=([^&]*)/.exec(body.toString('utf-8'))?.[1] ?? '';
        const decoded = decodeURIComponent(pw.replaceAll('+', ' '));
        if (decoded && decoded === cfg.access_password) {
          verified.allow(ip);
          send(res, 200, await portalPage());
        } else {
          send(res, 200, passwordPage('密码不正确，请重试'));
        }
        return;
      }
      if (path === '/accept' || path === '/__portal_accept') {
        await readBody(req);
        let mac = '';
        try {
          mac = ip ? await ctx.macOfIp(ip) : '';
        } catch {
          mac = '';
        }
        if (!scheduleOpen()) {
          send(res, 200, scheduleDeniedPage(cfg.schedule_start, cfg.schedule_end));
          return;
        }
        allow.allow(ip, mac, ua);
        await opts.accept(ip, mac, ua);
        log.info(`强制门户：${ip}(${mac || '未知MAC'}) 已同意并开始上网`);
        send(res, 200, successPage(successKind, (await ctx.getGateway()) || '192.168.137.1'));
        return;
      }
      send(res, 404, Buffer.from('<h1>404</h1>', 'utf-8'));
      return;
    }

    // 时段限制：不在开放时间内直接拒绝
    if (!scheduleOpen()) {
      send(res, 200, scheduleDeniedPage(cfg.schedule_start, cfg.schedule_end));
      return;
    }
    // 门户访问密码：先验密码再看欢迎页
    if (cfg.access_password && !verified.has(ip)) {
      send(res, 200, passwordPage());
      return;
    }
    send(res, 200, await portalPage());
  };

  return {
    close: () => {
      try {
        server.close();
      } catch {
        /* ignore */
      }
    },
    scheduleOpen,
    get port() {
      return opts.port;
    },
  };
}

import { renderTemplate } from './portal.ts';
const renderTemplatePublic = renderTemplate;
const renderCustom = renderTemplate;

// --------------------------------------------------------------------------- //
//                                  门户外壳                                    //
// --------------------------------------------------------------------------- //
function portBusy(port: number): boolean {
  // 同步探测（占用判断）——用 net 模块简版
  try {
    const net = require('node:net') as typeof import('node:net');
    const s = net.createConnection({ host: '127.0.0.1', port, timeout: 400 });
    let result = false;
    s.on('connect', () => {
      result = true;
      s.destroy();
    });
    s.on('timeout', () => s.destroy());
    s.on('error', () => s.destroy());
    // busy 等待 400ms
    const start = Date.now();
    while (Date.now() - start < 450 && !result) {
      /* spin */
    }
    return result;
  } catch {
    return false;
  }
}

async function service(action: string): Promise<[boolean, string]> {
  /** 启停 SharedAccess(ICS) 服务，用于让出/恢复 UDP 53。 */
  const { code, stdout, stderr } = await run(['sc', action, 'SharedAccess'], 25);
  const ok = code === 0 || (stdout + stderr).toLowerCase().includes('already');
  return [ok, (stdout || stderr).trim().slice(0, 200)];
}

export interface CaptivePortalStatus {
  running: boolean;
  dns: boolean;
  dns_error: string;
  url: string;
  allowed: number;
  queries: number;
  hijacked: number;
  error: string;
  need_password: boolean;
  schedule: string;
  schedule_open: boolean;
}

export class CaptivePortal {
  ctx: PortalContext;
  onAccept: ((ip: string, mac: string, ua: string) => Promise<void> | void) | null;
  onDnsQuery: ((ip: string, domain: string) => void) | null;

  allow = new AllowList();
  cfg: PortalConfig = defaultPortalConfig();
  private http: CaptiveServer | null = null;
  private dns: DnsProxy | null = null;
  lastError = '';
  dnsNote = '';
  port = 80;

  constructor(
    ctx: PortalContext,
    onAccept?: (ip: string, mac: string, ua: string) => Promise<void> | void,
    onDnsQuery?: (ip: string, domain: string) => void,
  ) {
    this.ctx = ctx;
    this.onAccept = onAccept ?? null;
    this.onDnsQuery = onDnsQuery ?? null;
  }

  get running(): boolean {
    return this.http !== null;
  }

  get dnsRunning(): boolean {
    return this.dns !== null && this.dns.error === '';
  }

  get url(): string {
    const gw = this.ctx.getGateway() || '192.168.137.1';
    return this.port === 80 ? `http://${gw}/` : `http://${gw}:${this.port}/`;
  }

  status(): CaptivePortalStatus {
    const s = Math.trunc(this.cfg.schedule_start);
    const e = Math.trunc(this.cfg.schedule_end);
    const p = (n: number) => String(n).padStart(2, '0');
    return {
      running: this.running,
      dns: this.dnsRunning,
      dns_error: (this.dns?.error ?? '') || this.dnsNote,
      url: this.url,
      allowed: this.allow.ips.size,
      queries: this.dns?.queryCount ?? 0,
      hijacked: this.dns?.hijackCount ?? 0,
      error: this.lastError,
      need_password: Boolean(this.cfg.access_password),
      schedule: `${p(s)}:00-${p(e % 24)}:00`,
      schedule_open: this.http ? this.http.scheduleOpen() : true,
    };
  }

  async start(cfg?: PortalConfig, forceDns = false): Promise<[boolean, string]> {
    if (this.running) return [true, '强制门户已在运行'];
    this.cfg = cfg ?? this.cfg;
    this.lastError = '';
    this.dnsNote = '';
    this.port = 80;

    const gw = (await this.ctx.getGateway()) || '192.168.137.1';

    // 1) DNS 劫持（可选，但只有它才能真正弹出门户）
    if (this.cfg.dns_redirect || forceDns) await this.startDns(gw);

    // 2) HTTP 门户
    try {
      this.http = startCaptiveServer({
        port: this.port,
        cfg: this.cfg,
        ctx: this.ctx,
        allow: this.allow,
        values: () => portalValues(this.cfg, this.ctx),
        accept: async (ip, mac, ua) => {
          if (this.onAccept) await this.onAccept(ip, mac, ua);
        },
        templateName: this.cfg.template || 'aurora',
      });
    } catch (exc) {
      this.lastError = `端口 ${this.port} 无法监听：${String(exc)}`;
      return [false, this.lastError];
    }

    let msg = `强制门户已启动：${this.url}`;
    if (this.cfg.dns_redirect || forceDns) {
      msg += this.dnsRunning
        ? '（DNS 劫持生效中）'
        : `（DNS 劫持未生效：${this.dnsNote || '未知原因'}，需手动访问门户页）`;
    }
    return [true, msg];
  }

  private async startDns(gateway: string): Promise<void> {
    let dns = new DnsProxy(gateway, this.allow);
    dns.onQuery = (ip, domain) => this.onDnsQuery?.(ip, domain);
    if (dns.bind()) {
      this.dns = dns;
      return;
    }
    // 53 通常被 ICS 的 DNS 代理(SharedAccess)占用：停服务 → 抢占 → 恢复
    if (!isAdmin()) {
      this.dnsNote = 'UDP 53 被占用，且当前非管理员，无法抢占';
      return;
    }
    log.info('UDP 53 被占用，尝试暂停 SharedAccess 以抢占 DNS');
    await service('stop');
    await new Promise((r) => setTimeout(r, 1200));
    dns = new DnsProxy(gateway, this.allow);
    dns.onQuery = (ip, domain) => this.onDnsQuery?.(ip, domain);
    if (dns.bind()) {
      this.dns = dns;
      await service('start');
      await new Promise((r) => setTimeout(r, 800));
      try {
        const { enableIcs } = await import('./ics.ts');
        await enableIcs();
      } catch {
        log.debug('ICS 重新启用失败（可稍后在界面上重试）');
      }
      return;
    }
    await service('start');
    this.dnsNote = 'UDP 53 仍被其它程序占用，门户需手动访问';
  }

  async stop(): Promise<[boolean, string]> {
    if (this.dns) {
      this.dns.stop();
      this.dns = null;
    }
    if (this.http) {
      try {
        this.http.close();
      } catch (exc) {
        log.debug(`关闭门户服务异常：${String(exc)}`);
      }
      this.http = null;
    }
    return [true, '强制门户已停止'];
  }

  async restart(cfg: PortalConfig, forceDns = false): Promise<[boolean, string]> {
    await this.stop();
    await new Promise((r) => setTimeout(r, 300));
    return this.start(cfg, forceDns);
  }

  allowIp(ip: string, mac = ''): void {
    this.allow.allow(ip, mac);
  }

  revokeIp(ip: string, mac = ''): void {
    this.allow.revoke(ip, mac);
  }

  clearAllowed(): void {
    this.allow.clear();
  }

  allowedList(): (AllowDetail & { ip: string })[] {
    return this.allow.snapshot();
  }
}

import { buildValues } from './portal.ts';
const portalValues = buildValues;

export { portBusy, timeNow, esc, normalizeMac, DATA_DIR, ensureDirs };
