/**
 * 欢迎页（对应原项目 hotspot/core/portal.py）：
 * 4 套预制模板渲染 + 自定义 HTML + DNS 重定向 + 统一管理入口。
 */
import { copyFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs';
import { basename, join } from 'node:path';
import { esc, normalizeMac, timeNow } from './util.ts';
import type { TrafficDB } from './storage.ts';
import { CUSTOM_PORTAL_DIR, TEMPLATE_DIR, getLogger } from './paths.ts';
import type { PortalConfig } from './config.ts';

const log = getLogger('portal');

export const TEMPLATES: [string, string, string][] = [
  ['aurora', '极光', '深蓝紫渐变 · 玻璃拟态'],
  ['ocean', '海风', '青绿渐变 · 清爽卡片'],
  ['corporate', '商务', '浅色专业 · 信息表格'],
  ['minimal', '极简', '黑白等宽 · 终端风格'],
  ['custom', '自定义', '使用本地 HTML 文件'],
];

/** 各类系统检测网络连通性的探测路径 */
export const PROBE_PATHS = [
  '/generate_204',
  '/gen_204',
  '/hotspot-detect.html',
  '/library/test/success.html',
  '/ncsi.txt',
  '/connecttest.txt',
  '/redirect',
  '/success.txt',
];

export function listTemplates(): [string, string, string][] {
  return [...TEMPLATES];
}

export function templatePath(name: string): string | null {
  if (name === 'custom') return null;
  const p = join(TEMPLATE_DIR, `${name}.html`);
  return existsSync(p) ? p : null;
}

export interface PortalContext {
  getSsid: () => string | Promise<string>;
  getGateway: () => string | Promise<string>;
  getClients: () => number | Promise<number>;
  macOfIp: (ip: string) => string | Promise<string>;
}

export function makePortalContext(
  partial: Partial<PortalContext> = {},
): PortalContext {
  return {
    getSsid: partial.getSsid ?? (() => ''),
    getGateway: partial.getGateway ?? (() => ''),
    getClients: partial.getClients ?? (() => 0),
    macOfIp: partial.macOfIp ?? (() => ''),
  };
}

/** 占位符替换：{{key}}。不用模板引擎，避免与 CSS 花括号冲突。 */
export function renderTemplate(tpl: string, values: Record<string, string>): string {
  let out = tpl;
  for (const [key, val] of Object.entries(values)) {
    out = out.split('{{' + key + '}}').join(val);
  }
  return out;
}

function noticeLi(text: string): string {
  const lines = (text || '')
    .split('\n')
    .map((ln) => ln.trim())
    .filter(Boolean);
  return lines.map((ln) => `<li>${esc(ln)}</li>`).join('') || '<li>请合理使用网络资源。</li>';
}

export async function buildValues(cfg: PortalConfig, ctx: PortalContext): Promise<Record<string, string>> {
  const ssid = (await ctx.getSsid()) || cfg.title;
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return {
    ssid: esc(ssid),
    title: esc(cfg.title.split('{{ssid}}').join(ssid)),
    subtitle: esc(cfg.subtitle),
    notice: esc(cfg.notice || ''),
    notice_li: noticeLi(cfg.notice),
    button: esc(cfg.button || '同意并开始上网'),
    footer: esc(cfg.footer || ''),
    gateway: esc((await ctx.getGateway()) || ''),
    clients: String(await ctx.getClients()),
    time: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`,
  };
}

// --------------------------------------------------------------------------- //
//                              DNS 重定向器                                    //
// --------------------------------------------------------------------------- //
import { createSocket, type Socket } from 'node:dgram';

/** 极简 DNS 服务器：所有 A 查询都回答本机地址，诱导系统弹出门户页。 */
export class DnsRedirector {
  answerIp: string;
  port: number;
  private sock: Socket | null = null;
  error = '';
  private stopped = false;

  constructor(answerIp: string, port = 53) {
    this.answerIp = answerIp;
    this.port = port;
  }

  start(): void {
    const s = createSocket('udp4');
    this.sock = s;
    s.on('message', (data, rinfo) => {
      try {
        const resp = this.buildResponse(data);
        if (resp) s.send(resp, rinfo.port, rinfo.address);
      } catch {
        /* ignore */
      }
    });
    s.on('error', (err) => {
      this.error = `UDP ${this.port} 绑定失败：${err.message}（可能被系统 DNS 服务占用）`;
      log.warn(this.error);
      try {
        s.close();
      } catch {
        /* ignore */
      }
    });
    s.bind(this.port, () => {
      log.info(`DNS 重定向已启动，应答地址 ${this.answerIp}`);
    });
  }

  private parseQname(data: Buffer): [string, number] {
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

  private buildResponse(data: Buffer): Buffer | null {
    if (data.length < 12) return null;
    try {
      const tid = data.subarray(0, 2);
      const flags = data.readUInt16BE(2);
      if (flags & 0x8000) return null;
      const [qname, end] = this.parseQname(data);
      if (!qname) return null;
      if (end + 4 > data.length) return null;
      const qtype = data.readUInt16BE(end);
      if (qtype !== 1) return null; // 只处理 A 记录
      const header = Buffer.alloc(12);
      tid.copy(header, 0);
      header.writeUInt16BE(0x8180, 2);
      header.writeUInt16BE(1, 4); // QDCOUNT
      header.writeUInt16BE(1, 6); // ANCOUNT
      header.writeUInt16BE(0, 8);
      header.writeUInt16BE(0, 10);
      const question = data.subarray(12, end + 4);
      const answer = Buffer.alloc(16);
      answer.writeUInt16BE(0xc00c, 0); // name pointer
      answer.writeUInt16BE(1, 2); // type A
      answer.writeUInt16BE(1, 4); // class IN
      answer.writeUInt32BE(30, 6); // TTL
      answer.writeUInt16BE(4, 10); // RDLENGTH
      const octets = this.answerIp.split('.').map((x) => Number(x) & 0xff);
      for (let i = 0; i < 4; i++) answer.writeUInt8(octets[i] ?? 0, 12 + i);
      return Buffer.concat([header, question, answer]);
    } catch {
      return null;
    }
  }

  stop(): void {
    this.stopped = true;
    try {
      this.sock?.close();
    } catch {
      /* ignore */
    }
    this.sock = null;
  }
}

// --------------------------------------------------------------------------- //
//                              统一管理入口                                     //
// --------------------------------------------------------------------------- //
import * as captive from './captive.ts';

/** 欢迎页 HTTP 门户（构建在 captive.ts 的 CaptiveServer 之上，模板渲染 + /accept）。 */
export class PortalManager {
  db: TrafficDB;
  ctx: PortalContext;
  private server: captive.CaptiveServer | null = null;
  private dns: DnsRedirector | null = null;
  cfg: PortalConfig;
  lastError = '';

  constructor(db: TrafficDB, ctx: PortalContext) {
    this.db = db;
    this.ctx = ctx;
    this.cfg = captive.defaultPortalConfig();
  }

  get running(): boolean {
    return this.server !== null;
  }

  get port(): number {
    return this.cfg.port || 8080;
  }

  async start(cfg?: PortalConfig): Promise<[boolean, string]> {
    if (this.running) return [true, '欢迎页已在运行'];
    this.cfg = cfg ?? this.cfg;
    let customIndex: string | null = null;
    let assetsRoot: string | null = null;
    if (this.cfg.template === 'custom') {
      const p = this.cfg.custom_html || '';
      if (!p || !existsSync(p)) return [false, '未选择有效的自定义 HTML 文件'];
      customIndex = p;
      assetsRoot = join(p, '..');
    }
    const portalPort = this.port;
    const values = async () => buildValues(this.cfg, this.ctx);
    const accept = async (ip: string, mac: string, ua: string) => {
      try {
        await this.db.recordPortalVisit(ip, mac, ua, true);
      } catch (exc) {
        log.exception('记录欢迎页同意失败', exc);
      }
      log.info(`欢迎页：${ip}(${mac || '未知MAC'}) 已同意并接入`);
    };
    try {
      this.server = captive.startCaptiveServer({
        port: portalPort,
        allow: new captive.AllowList(join(CUSTOM_PORTAL_DIR, '..', 'portal_accepted_welcome.json')),
        cfg: this.cfg,
        ctx: this.ctx,
        values,
        accept,
        templateName: this.cfg.template || 'aurora',
        customIndex,
        assetsRoot,
        requireAccept: true,
        successPageKind: 'welcome',
      });
    } catch (exc) {
      this.lastError = String(exc);
      return [false, `端口 ${portalPort} 无法监听：${String(exc)}`];
    }

    if (this.cfg.dns_redirect) {
      const gw = (await this.ctx.getGateway()) || '192.168.137.1';
      this.dns = new DnsRedirector(gw);
      this.dns.start();
      await sleep(300);
    }
    const dnsNote = this.dns && this.dns.error ? `（DNS 重定向未生效：${this.dns.error}）` : '';
    const url = `http://${(await this.ctx.getGateway()) || '192.168.137.1'}:${this.port}/`;
    return [true, `欢迎页已启动：${url} ${dnsNote}`.trim()];
  }

  async stop(): Promise<[boolean, string]> {
    if (!this.running) return [true, '欢迎页未运行'];
    try {
      this.server?.close();
    } catch (exc) {
      log.debug(`关闭门户服务异常：${String(exc)}`);
    }
    this.server = null;
    if (this.dns) {
      this.dns.stop();
      this.dns = null;
    }
    return [true, '欢迎页已停止'];
  }

  async restart(cfg: PortalConfig): Promise<[boolean, string]> {
    await this.stop();
    await sleep(250);
    return this.start(cfg);
  }

  url(): string {
    return `http://127.0.0.1:${this.port}/`;
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** 把用户选择的 HTML 复制到数据目录，避免原文件被移动后失效。 */
export function copyCustomHtml(src: string): string {
  const dst = join(CUSTOM_PORTAL_DIR, basename(src));
  try {
    mkdirSync(CUSTOM_PORTAL_DIR, { recursive: true });
    if (src !== dst) copyFileSync(src, dst);
    return dst;
  } catch (exc) {
    log.warn(`复制自定义 HTML 失败：${String(exc)}`);
    return src;
  }
}

/** 给「预览」按钮使用：直接渲染出 HTML 字符串。 */
export async function previewHtml(cfg: PortalConfig, ctx: PortalContext): Promise<string> {
  let raw = '<h1>模板缺失</h1>';
  if (cfg.template === 'custom') {
    const p = cfg.custom_html || '';
    raw = p && existsSync(p) ? readFileSync(p, 'utf-8') : '<h1>未选择文件</h1>';
  } else {
    const tpl = templatePath(cfg.template) ?? templatePath('aurora');
    raw = tpl ? readFileSync(tpl, 'utf-8') : '<h1>模板缺失</h1>';
  }
  return renderTemplate(raw, await buildValues(cfg, ctx));
}

export { normalizeMac, timeNow };
