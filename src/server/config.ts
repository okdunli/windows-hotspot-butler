/**
 * 配置对象与持久化（对应原项目 hotspot/core/config.py）。
 * JSON 键名与原版完全一致（snake_case），%APPDATA% 下的 config.json 可直接互换。
 */
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { CONFIG_FILE, ensureDirs, getLogger } from './paths.ts';

const log = getLogger('config');

export const SECURITY_CHOICES: [string, string][] = [
  ['wpa2', 'WPA2-个人 (推荐)'],
  ['wpa2wpa3', 'WPA2/WPA3 混合'],
  ['wpa3', 'WPA3-个人 (SAE)'],
  ['open', '开放 (无密码)'],
];
export const BAND_CHOICES: [string, string][] = [
  ['auto', '自动'],
  ['2.4', '2.4 GHz'],
  ['5', '5 GHz'],
];
export const BACKEND_CHOICES: [string, string][] = [
  ['auto', '自动选择'],
  ['winrt', '移动热点 (WinRT)'],
  ['netsh', '承载网络 (netsh)'],
];
export const TRAFFIC_CHOICES: [string, string][] = [
  ['auto', '自动'],
  ['scapy', '抓包精确统计 (需 Npcap)'],
  ['adapter', '网卡总量统计'],
  ['off', '关闭'],
];

export interface HotspotConfig {
  ssid: string;
  passphrase: string;
  security: string; // open | wpa2 | wpa2wpa3 | wpa3
  band: string; // auto | 2.4 | 5
  max_clients: number;
  backend: string; // auto | winrt | netsh
  wps_enabled: boolean;
  auto_start: boolean;
  enforce_max_clients: boolean;
}

export interface PortalConfig {
  enabled: boolean;
  template: string; // aurora | ocean | corporate | minimal | custom
  custom_html: string;
  port: number;
  dns_redirect: boolean;
  title: string;
  subtitle: string;
  notice: string;
  button: string;
  footer: string;
  require_accept: boolean;
  auto_open_preview: boolean;
  access_password: string;
  schedule_start: number;
  schedule_end: number;
}

export interface MiniWindowState {
  show: boolean;
  x: number;
  y: number;
}

export interface AppConfig {
  theme: string; // dark | light
  language: string; // auto | zh_CN | en
  poll_interval: number;
  traffic_backend: string;
  demo_mode: boolean;
  sidebar_expanded: boolean;
  start_with_windows: boolean;
  close_to_tray: boolean;
  confirm_exit_hotspot: boolean;
  hotkey_enabled: boolean;
  temp_password: string;
  temp_password_until: number;
  window_geometry: string;
  mini_window: MiniWindowState;
  hotspot: HotspotConfig;
  portal: PortalConfig;
}

export function defaultHotspot(): HotspotConfig {
  return {
    ssid: 'MyHotspot',
    passphrase: '12345678',
    security: 'wpa2',
    band: 'auto',
    max_clients: 8,
    backend: 'auto',
    wps_enabled: false,
    auto_start: false,
    enforce_max_clients: true,
  };
}

export function defaultPortal(): PortalConfig {
  return {
    enabled: false,
    template: 'aurora',
    custom_html: '',
    port: 8080,
    dns_redirect: false,
    title: '欢迎接入 {{ssid}}',
    subtitle: '连接成功，请阅读下方须知后开始上网',
    notice:
      '本无线网络仅供访客临时使用，请勿从事任何违反法律法规的行为。\n如需帮助请联系网络管理员。',
    button: '同意并开始上网',
    footer: 'Powered by Windows Hotspot Butler',
    require_accept: true,
    auto_open_preview: true,
    access_password: '',
    schedule_start: 0,
    schedule_end: 24,
  };
}

export function defaultConfig(): AppConfig {
  return {
    theme: 'dark',
    language: 'auto',
    poll_interval: 4.0,
    traffic_backend: 'auto',
    demo_mode: false,
    sidebar_expanded: true,
    start_with_windows: false,
    close_to_tray: false,
    confirm_exit_hotspot: true,
    hotkey_enabled: true,
    temp_password: '',
    temp_password_until: 0,
    window_geometry: '',
    mini_window: { show: false, x: -1, y: -1 },
    hotspot: defaultHotspot(),
    portal: defaultPortal(),
  };
}

type Raw = Record<string, unknown>;

function str(v: unknown, dflt = ''): string {
  return v === undefined || v === null || typeof v === 'object' ? dflt : String(v);
}
function num(v: unknown, dflt = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : dflt;
}
function bool(v: unknown, dflt = false): boolean {
  return typeof v === 'boolean' ? v : v === undefined || v === null ? dflt : Boolean(v);
}

function coerceHotspot(data: Raw): HotspotConfig {
  const h = defaultHotspot();
  if ('ssid' in data) h.ssid = str(data.ssid, h.ssid);
  if ('passphrase' in data) h.passphrase = str(data.passphrase, h.passphrase);
  if ('security' in data) h.security = str(data.security, h.security);
  if ('band' in data) h.band = str(data.band, h.band);
  if ('backend' in data) h.backend = str(data.backend, h.backend);
  if ('max_clients' in data) h.max_clients = Math.trunc(num(data.max_clients, h.max_clients));
  if ('wps_enabled' in data) h.wps_enabled = bool(data.wps_enabled, h.wps_enabled);
  if ('auto_start' in data) h.auto_start = bool(data.auto_start, h.auto_start);
  if ('enforce_max_clients' in data) h.enforce_max_clients = bool(data.enforce_max_clients, h.enforce_max_clients);
  return h;
}

function coercePortal(data: Raw): PortalConfig {
  const p = defaultPortal();
  for (const k of ['template', 'custom_html', 'title', 'subtitle', 'notice', 'button', 'footer', 'access_password'] as const) {
    if (k in data) p[k] = str(data[k], p[k]);
  }
  for (const k of ['enabled', 'dns_redirect', 'require_accept', 'auto_open_preview'] as const) {
    if (k in data) p[k] = bool(data[k], p[k]);
  }
  for (const k of ['port', 'schedule_start', 'schedule_end'] as const) {
    if (k in data) p[k] = Math.trunc(num(data[k], p[k]));
  }
  return p;
}

export function normalizeConfig(cfg: AppConfig): AppConfig {
  const h = cfg.hotspot;
  h.ssid = (h.ssid || 'MyHotspot').slice(0, 32);
  if (h.security !== 'open' && h.passphrase.length < 8) {
    h.passphrase = (h.passphrase + '12345678').slice(0, 8);
  }
  h.max_clients = Math.max(1, Math.min(64, Math.trunc(h.max_clients || 8)));
  if (!SECURITY_CHOICES.some(([v]) => v === h.security)) h.security = 'wpa2';
  if (!BAND_CHOICES.some(([v]) => v === h.band)) h.band = 'auto';
  if (!BACKEND_CHOICES.some(([v]) => v === h.backend)) h.backend = 'auto';
  const p = cfg.portal;
  p.port = Math.max(1, Math.min(65535, p.port || 8080));
  if (cfg.theme !== 'dark' && cfg.theme !== 'light') cfg.theme = 'dark';
  if (cfg.language !== 'auto' && cfg.language !== 'zh_CN' && cfg.language !== 'en') cfg.language = 'auto';
  cfg.poll_interval = Math.max(1.0, Math.min(30.0, cfg.poll_interval || 4.0));
  if (!TRAFFIC_CHOICES.some(([v]) => v === cfg.traffic_backend)) cfg.traffic_backend = 'auto';
  const mw = cfg.mini_window;
  mw.x = Math.trunc(num(mw.x, -1));
  mw.y = Math.trunc(num(mw.y, -1));
  mw.show = Boolean(mw.show);
  return cfg;
}

export function configFromDict(data: Raw): AppConfig {
  const cfg = defaultConfig();
  for (const k of [
    'theme',
    'language',
    'traffic_backend',
    'window_geometry',
  ] as const) {
    if (k in data) cfg[k] = str(data[k], cfg[k]);
  }
  cfg.poll_interval = num(data.poll_interval, cfg.poll_interval);
  for (const k of [
    'demo_mode',
    'sidebar_expanded',
    'start_with_windows',
    'close_to_tray',
    'confirm_exit_hotspot',
    'hotkey_enabled',
  ] as const) {
    if (k in data) cfg[k] = bool(data[k], cfg[k]);
  }
  cfg.temp_password = str(data.temp_password, '');
  cfg.temp_password_until = num(data.temp_password_until, 0);
  if (typeof data.hotspot === 'object' && data.hotspot) cfg.hotspot = coerceHotspot(data.hotspot as Raw);
  if (typeof data.portal === 'object' && data.portal) cfg.portal = coercePortal(data.portal as Raw);
  // mini_window 手工合并（老配置缺字段 → 默认值）
  const mw = data.mini_window;
  if (typeof mw === 'object' && mw) {
    const m = mw as Raw;
    try {
      cfg.mini_window = { show: bool(m.show, false), x: Math.trunc(num(m.x, -1)), y: Math.trunc(num(m.y, -1)) };
    } catch {
      cfg.mini_window = { show: false, x: -1, y: -1 };
    }
  }
  return normalizeConfig(cfg);
}

export function configToDict(cfg: AppConfig): Raw {
  return JSON.parse(JSON.stringify(cfg)) as Raw;
}

export function loadConfig(): AppConfig {
  try {
    if (existsSync(CONFIG_FILE)) {
      const data = JSON.parse(readFileSync(CONFIG_FILE, 'utf-8')) as Raw;
      const cfg = configFromDict(data);
      log.info(`已读取配置：${CONFIG_FILE}`);
      return cfg;
    }
  } catch (exc) {
    log.warn(`配置读取失败(${String(exc)})，使用默认值`);
  }
  return normalizeConfig(defaultConfig());
}

export function saveConfig(cfg: AppConfig): boolean {
  ensureDirs();
  normalizeConfig(cfg);
  try {
    const tmp = CONFIG_FILE.replace(/\.json$/, '.tmp');
    writeFileSync(tmp, JSON.stringify(cfg, null, 2), 'utf-8');
    renameSync(tmp, CONFIG_FILE);
    return true;
  } catch (exc) {
    log.error(`配置保存失败：${String(exc)}`);
    return false;
  }
}
