/** 共享工具（对应原项目 storage.py 顶部的工具函数）。 */

/** 统一成 aa:bb:cc:dd:ee:ff。 */
export function normalizeMac(mac: string): string {
  if (!mac) return '';
  const hexs = String(mac)
    .toLowerCase()
    .split('')
    .filter((c) => '0123456789abcdef'.includes(c))
    .join('');
  if (hexs.length < 12) return String(mac).toLowerCase().trim();
  const h = hexs.slice(0, 12);
  return (h.match(/../g) ?? []).join(':');
}

export function humanBytes(n: number): string {
  n = Number(n || 0);
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (n < 1024 || unit === 'TB') {
      if (unit === 'B') return `${Math.trunc(n)} ${unit}`;
      return `${n < 100 ? n.toFixed(2) : n.toFixed(1)} ${unit}`;
    }
    n /= 1024;
  }
  return `${n.toFixed(1)} TB`;
}

export function humanRate(bps: number): string {
  return humanBytes(bps) + '/s';
}

/** "x 秒前 / x 分钟前 / x 小时前 / x 天前" 的原始文案（i18n 在 backend 层做）。 */
export function agoParts(ts: number): { seconds: number; unit: 's' | 'm' | 'h' | 'd'; value: number } {
  const d = Math.max(0, Math.trunc(Date.now() / 1000 - ts));
  if (d < 60) return { seconds: d, unit: 's', value: d };
  if (d < 3600) return { seconds: d, unit: 'm', value: Math.floor(d / 60) };
  if (d < 86400) return { seconds: d, unit: 'h', value: Math.floor(d / 3600) };
  return { seconds: d, unit: 'd', value: Math.floor(d / 86400) };
}

export function uptimeText(ts: number): string {
  if (!ts) return '';
  const d = Math.max(0, Math.trunc(Date.now() / 1000 - ts));
  const h = Math.floor(d / 3600);
  const m = Math.floor((d % 3600) / 60);
  const s = d % 60;
  const p = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
}

export function timeHms(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function timeNow(): number {
  return Date.now() / 1000;
}

export function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

/** html.escape 等价。 */
export function esc(s: string): string {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/** python str.format 风格的 {key} 填充。 */
export function fmt(text: string, vars?: Record<string, unknown>): string {
  let out = text;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) out = out.split('{' + k + '}').join(String(v));
  }
  return out;
}

/** 随机小写字母数字串（临时密码用）。 */
export function randomPassword(len = 8): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let out = '';
  const bytes = new Uint8Array(len);
  globalThis.crypto.getRandomValues(bytes);
  for (let i = 0; i < len; i++) out += alphabet[bytes[i] % alphabet.length];
  return out;
}
