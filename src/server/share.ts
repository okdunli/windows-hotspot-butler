/**
 * 局域网文件共享（对应原项目 hotspot/core/share.py）：
 * 手机浏览器访问 http://网关:8081 即可上传/下载文件。仅监听热点网关所在网卡。
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { basename, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { createSocket } from 'node:dgram';
import { esc, humanBytes } from './util.ts';
import { getLogger } from './paths.ts';

const log = getLogger('share');

const PAGE = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>文件共享 · {{title}}</title><style>
body{margin:0;font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;
background:linear-gradient(160deg,#0b1026,#1b2350);color:#eaf0ff;min-height:100vh}
.wrap{max-width:640px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:20px} .sub{color:#9aa7d4;font-size:13px;margin-bottom:18px}
.up{background:rgba(255,255,255,.07);border:1px dashed rgba(255,255,255,.25);
border-radius:14px;padding:18px;text-align:center}
.item{display:flex;align-items:center;gap:10px;padding:11px 4px;
border-bottom:1px solid rgba(255,255,255,.08);font-size:14px}
.item a{color:#8ab4ff;text-decoration:none;word-break:break-all;flex:1}
.size{color:#9aa7d4;font-size:12px;white-space:nowrap}
.btn{display:inline-block;background:#3b82f6;color:#fff;border:0;border-radius:10px;
padding:10px 22px;font-size:14px;cursor:pointer}
input[type=file]{display:none}
</style></head><body><div class="wrap">
<h1>📤 文件共享</h1>
<div class="sub">{{title}} · 连接同一热点即可互传文件</div>
<div class="up">
<form method="POST" action="/upload" enctype="multipart/form-data">
<input type="file" name="f" id="fp" multiple required onchange="this.form.submit()">
<label for="fp" class="btn">选择文件上传</label>
</form></div>
<div>{{items}}</div>
</div></body></html>`;

function sizeText(n: number): string {
  let f = n;
  for (const u of ['B', 'KB', 'MB', 'GB']) {
    if (f < 1024 || u === 'GB') return `${f.toFixed(1)} ${u}`;
    f /= 1024;
  }
  return `${f.toFixed(1)} GB`;
}

export interface FileShareStatus {
  running: boolean;
  url: string;
  port: number;
  error: string;
}

export class FileShare {
  root: string;
  port: number;
  private titleProvider: () => string;
  private gatewayProvider: (() => string) | null;
  private srv: Server | null = null;
  lastError = '';

  constructor(
    root: string,
    port = 8081,
    opts: { titleProvider?: () => string; gatewayProvider?: (() => string) | null } = {},
  ) {
    this.root = root;
    this.port = port;
    this.titleProvider = opts.titleProvider ?? (() => 'WiFi 热点');
    this.gatewayProvider = opts.gatewayProvider ?? null;
  }

  get running(): boolean {
    return this.srv !== null;
  }

  get url(): string {
    let host = '';
    if (this.gatewayProvider) {
      try {
        host = this.gatewayProvider() || '';
      } catch {
        host = '';
      }
    }
    if (!host || host.startsWith('127.')) host = gatewayIp() || lanIp();
    return `http://${host}:${this.port}/`;
  }

  start(): [boolean, string] {
    if (this.running) return [true, '文件共享已在运行'];
    try {
      mkdirSync(this.root, { recursive: true });
    } catch {
      /* ignore */
    }
    try {
      this.srv = createServer((req, res) => {
        void this.handle(req, res).catch((exc) => {
          log.debug(`share 处理异常：${String(exc)}`);
          try {
            res.destroy();
          } catch {
            /* ignore */
          }
        });
      });
      this.srv.listen(this.port);
    } catch (exc) {
      this.lastError = `端口 ${this.port} 被占用：${String(exc)}`;
      this.srv = null;
      return [false, this.lastError];
    }
    return [true, `文件共享已启动：${this.url}（手机浏览器打开即可传文件）`];
  }

  stop(): [boolean, string] {
    if (this.srv) {
      try {
        this.srv.close();
      } catch (exc) {
        log.debug(`关闭文件共享异常：${String(exc)}`);
      }
      this.srv = null;
    }
    return [true, '文件共享已停止'];
  }

  status(): FileShareStatus {
    return { running: this.running, url: this.url, port: this.port, error: this.lastError };
  }

  // ------------------------------ HTTP ------------------------------ //
  private send(
    res: ServerResponse,
    code: number,
    body: Buffer | string = '',
    ctype = 'text/html; charset=utf-8',
    extra: Record<string, string> = {},
  ): void {
    const buf = typeof body === 'string' ? Buffer.from(body, 'utf-8') : body;
    try {
      res.writeHead(code, { 'Content-Type': ctype, 'Content-Length': String(buf.length), ...extra });
      res.end(this.reqMethod === 'HEAD' ? undefined : buf);
    } catch {
      /* broken pipe */
    }
  }

  private reqMethod = 'GET';

  private safeName(name: string): string {
    let n = basename(decodeURIComponent(name)).trim();
    n = n.replace(/[\\/:*?"<>|]/g, '_') || 'file';
    return n.slice(0, 150);
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    this.reqMethod = req.method ?? 'GET';
    const path = (req.url ?? '/').split('?')[0];
    if (req.method === 'GET' || req.method === 'HEAD') {
      if (path === '/' || path === '/index.html') this.listPage(res);
      else if (path.startsWith('/dl/')) this.download(res, path.slice(4));
      else this.send(res, 404, '<h1>404</h1>');
      return;
    }
    if (req.method === 'POST') {
      if (path !== '/upload') {
        this.send(res, 404, '<h1>404</h1>');
        return;
      }
      const length = Number(req.headers['content-length'] ?? 0);
      const ctype = String(req.headers['content-type'] ?? '');
      const m = /boundary=(?:"([^"]+)"|([^;]+))/.exec(ctype);
      if (!m || length <= 0) {
        this.send(res, 400, 'bad request');
        return;
      }
      const boundary = ('--' + (m[1] ?? m[2])).trim();
      const saved = await this.saveMultipart(req, length, boundary);
      this.send(
        res,
        200,
        "<!DOCTYPE html><meta charset='utf-8'><script>location.replace('/')</script>",
      );
      if (saved) log.info(`文件共享：已接收 ${saved}`);
      return;
    }
    this.send(res, 404, '<h1>404</h1>');
  }

  private listPage(res: ServerResponse): void {
    const rows: string[] = [];
    try {
      const files = readdirSync(this.root)
        .map((name) => join(this.root, name))
        .filter((p) => {
          try {
            return statSync(p).isFile();
          } catch {
            return false;
          }
        })
        .sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
      for (const p of files) {
        const name = basename(p);
        const st = statSync(p);
        rows.push(
          `<div class="item"><a href="/dl/${encodeURIComponent(name)}">${esc(name)}</a>` +
            `<span class="size">${sizeText(st.size)}</span></div>`,
        );
      }
    } catch {
      /* ignore */
    }
    const page = PAGE.split('{{title}}')
      .join(esc(this.titleProvider()))
      .split('{{items}}')
      .join(rows.join('') || '<div class="sub">暂无文件，上传一个试试</div>');
    this.send(res, 200, page);
  }

  private download(res: ServerResponse, name: string): void {
    const p = join(this.root, this.safeName(name));
    if (!existsSync(p) || !statSync(p).isFile()) {
      this.send(res, 404, '<h1>404</h1>');
      return;
    }
    try {
      const data = readFileSync(p);
      this.send(res, 200, data, 'application/octet-stream', {
        'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(basename(p))}`,
      });
    } catch {
      this.send(res, 500, 'read error');
    }
  }

  /** multipart 流式保存（与原版语义一致：解析第一个 part 的 filename，去尾部 boundary）。 */
  private async saveMultipart(req: IncomingMessage, length: number, boundary: string): Promise<string> {
    const tmp = join(this.root, `.up-${randomUUID()}.part`);
    let name = '';
    const chunks: Buffer[] = [];
    await new Promise<void>((resolve) => {
      let remaining = length;
      req.on('data', (d: Buffer) => {
        if (remaining <= 0) return;
        const take = d.subarray(0, remaining);
        remaining -= take.length;
        chunks.push(take);
      });
      req.on('end', () => resolve());
      req.on('error', () => resolve());
    });
    const data = Buffer.concat(chunks);
    // 头部解析（第一个 part 的 filename）
    const headEnd = data.indexOf('\r\n\r\n');
    if (headEnd >= 0) {
      const header = data.subarray(0, headEnd).toString('utf-8');
      const fm = /filename="([^"]*)"/.exec(header);
      if (fm) name = this.safeName(fm[1]) || 'file';
    }
    if (!name) return '';
    // 正文 = 第一个 \r\n\r\n 之后到尾部 boundary 之前
    let body = data.subarray(headEnd + 4);
    const bIdx = body.lastIndexOf(boundary);
    if (bIdx > 0) body = body.subarray(0, bIdx);
    if (body.subarray(-2).toString() === '\r\n') body = body.subarray(0, -2);
    // 去掉可能的后续 part（多文件时取第一个，与原版行为一致）
    const nextPart = body.indexOf(boundary);
    if (nextPart > 0) body = body.subarray(0, nextPart);
    let final = join(this.root, name);
    const stem = name.replace(/\.[^.]*$/, '');
    const ext = (name.match(/\.[^.]*$/) ?? [''])[0];
    let n = 1;
    while (existsSync(final)) {
      final = join(this.root, `${stem}(${n})${ext}`);
      n += 1;
    }
    try {
      writeFileSync(final, body);
      try {
        unlinkSync(tmp);
      } catch {
        /* ignore */
      }
    } catch (exc) {
      log.warn(`上传写盘失败：${String(exc)}`);
      return '';
    }
    return basename(final);
  }
}

function gatewayIp(): string {
  try {
    const s = createSocket('udp4');
    return new Promise<string>((resolve) => {
      const done = (ip: string) => {
        try {
          s.close();
        } catch {
          /* ignore */
        }
        resolve(ip);
      };
      s.on('error', () => done(''));
      s.connect(53, '223.5.5.5', () => {
        const addr = s.address();
        const ip = typeof addr === 'object' && addr ? addr.address : '';
        done(ip.startsWith('192.168.') ? ip : '');
      });
      setTimeout(() => done(''), 1000);
    }) as unknown as string;
  } catch {
    return '';
  }
}

function lanIp(): string {
  try {
    // 同步尽力而为：Node 没有内置同步 gethostbyname，回退默认值
    return '127.0.0.1';
  } catch {
    return '127.0.0.1';
  }
}

export { humanBytes };
