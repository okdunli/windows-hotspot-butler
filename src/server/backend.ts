/**
 * 后端桥（对应原项目 hotspot/webui/backend.py）。
 * 负责采集状态、执行操作，并把结果整理成前端能直接渲染的字典。
 *
 * 映射：pywebview js_api 的每个方法 = 一个 /api/* POST 端点；
 * get_state() / mini_state() = GET 端点。所有耗时操作仍在异步任务里跑，
 * 前端拉 get_state() 只读内存快照。
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { execFile } from 'node:child_process';
import * as netinfo from './netinfo.ts';
import * as pshell from './pshell.ts';
import { CaptivePortal, type AllowDetail } from './captive.ts';
import { loadConfig, saveConfig, normalizeConfig, type AppConfig } from './config.ts';
import { DeviceManager, type DeviceInfo } from './deviceman.ts';
import { HotspotController, hotspotStatus, stateText, type RawClient, type HotspotStatus } from './hotspot.ts';
import { DATA_DIR, SHARE_DIR, ensureDirs, setupLogging, getLogger } from './paths.ts';
import { makePortalContext, type PortalContext } from './portal.ts';
import { PortForwarder } from './portforward.ts';
import { FileShare } from './share.ts';
import { DeviceStore, TrafficDB } from './storage.ts';
import { TrafficMonitor } from './traffic.ts';
import { runSpeedTest } from './speedtest.ts';
import { humanBytes, humanRate, normalizeMac, agoParts, uptimeText, timeNow, randomPassword, fmt } from './util.ts';
import * as i18n from './i18n.ts';

const log = getLogger('backend');

export const TYPE_EMOJI: Record<string, string> = {
  phone: '📱', tablet: '📲', laptop: '💻', desktop: '🖥️',
  tv: '📺', console: '🎮', watch: '⌚', speaker: '🔊',
  camera: '📷', printer: '🖨️', router: '📡', nas: '🗄️',
  iot: '💡', car: '🚗', unknown: '📶',
};
export const TYPE_LABEL: Record<string, string> = {
  phone: '手机', tablet: '平板', laptop: '笔记本', desktop: '台式机',
  tv: '电视 / 盒子', console: '游戏机', watch: '手表 / 手环',
  speaker: '音箱', camera: '摄像头', printer: '打印机',
  router: '路由 / AP', nas: 'NAS / 服务器', iot: '智能家居',
  car: '车机', unknown: '未知设备',
};
export const ICON_CHOICES: { key: string; label: string }[] = Object.entries(TYPE_EMOJI).map(
  ([k, e]) => ({ key: k, label: `${e} ${TYPE_LABEL[k]}` }),
);

const BLOCK_FILE = join(DATA_DIR, 'blocked.json');
const GW_FILE = join(DATA_DIR, 'gateway.txt');
const EXPORT_DIR = DATA_DIR;
// 快采集：ARP + 流量（几十毫秒），负责设备列表实时更新
const POLL_FAST = 1.5;
// 慢采集：WinRT 状态 + 网关（PowerShell，8~15 秒），只定期跑，绝不阻塞快采集
const POLL_SLOW = 8.0;
const GW_TTL = 300.0;

function agoText(ts: number): string {
  if (!ts) return '';
  const a = agoParts(ts);
  return fmt(
    { s: '{d} 秒前', m: '{d} 分钟前', h: '{d} 小时前', d: '{d} 天前' }[a.unit]!,
    { d: a.value },
  );
}

export interface Toast {
  text: string;
  kind: string;
  ts: number;
}

type Json = Record<string, unknown>;

export class HotspotBackend {
  cfg: AppConfig;
  store: DeviceStore;
  db: TrafficDB;
  traffic: TrafficMonitor;
  devman: DeviceManager;
  controller: HotspotController;
  gateway = '192.168.137.1';
  ctx: PortalContext;
  captive: CaptivePortal;
  share: FileShare;
  portfwd: PortForwarder;

  private arp: Record<string, string> = {};
  private status: HotspotStatus | null = null;
  private raw: RawClient[] = [];
  private slowRunning = false;
  private lastSlow = 0;
  private gwTs = 0;

  private busy: Record<string, number> = {};
  private toasts: Toast[] = [];
  private snap: Json = {};
  private caps: Json = {};
  private diag: string[] = [];
  private ready = false;
  startedAt = 0;
  private stopped = false;

  private blocked: Record<string, string>;
  private prevOnline: Set<string> = new Set();
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private stopTimer: ReturnType<typeof setTimeout> | null = null;
  private stopDeadline = 0;
  private tempTimer: ReturnType<typeof setTimeout> | null = null;
  private tempDeadline = 0;
  private exitConfirmed = false;
  /** 标记门户是否由"热点随启随停"逻辑托管 */
  private portalAuto = false;
  private loopChain: Promise<void> = Promise.resolve();
  private trayTooltip: string = '';

  constructor(cfg?: AppConfig) {
    setupLogging();
    this.cfg = cfg ?? loadConfig();
    i18n.setLang(this.cfg.language);
    this.store = new DeviceStore();
    this.db = new TrafficDB();
    this.traffic = new TrafficMonitor(this.db, {
      trafficBackend: this.cfg.traffic_backend,
      demoMode: this.cfg.demo_mode,
      ifaceProvider: netinfo.findHotspotAdapter,
    });
    this.devman = new DeviceManager(this.store, this.db, this.traffic);
    this.controller = new HotspotController(this.cfg.hotspot);
    this.gateway = this.loadGateway() || '192.168.137.1';

    this.ctx = makePortalContext({
      getSsid: () => this.controller.lastStatus.ssid || this.cfg.hotspot.ssid,
      getGateway: () => this.gateway,
      getClients: () => this.devman.all().filter((d) => d.online).length,
      macOfIp: (ip) => this.arp[ip] ?? '',
    });
    this.captive = new CaptivePortal(this.ctx, (ip, mac, ua) => this.onPortalAccept(ip, mac, ua), (ip, d) =>
      this.onDnsQuery(ip, d),
    );
    this.share = new FileShare(SHARE_DIR, 8081, {
      titleProvider: () => this.cfg.hotspot.ssid,
      gatewayProvider: () => this.gateway,
    });
    this.portfwd = new PortForwarder();
    this.blocked = this.loadBlocked();

    void this.traffic.start().then(() => this.traffic.startFlushTimer());
    void this.detectCaps().finally(() => {
      this.ready = true;
    });
    this.pollTimer = setInterval(() => void this.tick(), POLL_FAST * 1000);

    if (this.cfg.hotspot.auto_start && !this.controller.lastStatus.active) {
      void this.controller.start().then(([ok, msg]) => {
        if (msg) this.toast(msg, ok ? 'success' : 'error');
      });
    }
  }

  async shutdown(): Promise<void> {
    this.stopped = true;
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    try {
      await this.captive.stop();
    } catch {
      /* ignore */
    }
    try {
      this.share.stop();
    } catch {
      /* ignore */
    }
    try {
      await this.traffic.stop();
      this.traffic.stopFlushTimer();
    } catch {
      /* ignore */
    }
    try {
      this.store.save(true);
    } catch {
      /* ignore */
    }
    try {
      await this.db.close();
    } catch {
      /* ignore */
    }
  }

  // ----------------------------- toast ----------------------------- //
  private toast(text: string, kind = 'info'): void {
    this.toasts.push({ text, kind, ts: timeNow() });
    log.info(`[${kind}] ${text}`);
  }

  private trayNotify(text: string): void {
    /** 桌面通知（浏览器 Notification 由前端做；这里记录到日志）。 */
    log.info(`[notify] ${text}`);
  }

  setDesktopNotifier(fn: (title: string, body: string) => void): void {
    this._desktopNotifier = fn;
  }
  private _desktopNotifier: ((title: string, body: string) => void) | null = null;

  private async onPortalAccept(ip: string, mac: string, ua: string): Promise<void> {
    const maxn = Math.trunc(this.cfg.hotspot.max_clients || 0);
    if (maxn) {
      const online = this.devman.all().filter((d) => d.online).length;
      if (online > maxn) {
        try {
          this.captive.revokeIp(ip, mac);
        } catch {
          /* ignore */
        }
        this.toast(fmt('已拒绝 {ip}：设备数超过上限 {n} 台', { ip, n: maxn }), 'error');
        return;
      }
    }
    this.toast(fmt('{ip} 已通过欢迎页并开始上网', { ip }), 'success');
  }

  private onDnsQuery(ip: string, domain: string): void {
    if (!domain || /\.(arpa|lan|local|localdomain)$/.test(domain)) return;
    void this.db.recordDnsQuery(ip, this.arp[ip] ?? '', domain).catch(() => {});
  }

  private static loadGatewayFile(): string {
    try {
      const txt = readFileSync(GW_FILE, 'utf-8').trim();
      if (txt.split('.').length === 4) return txt;
    } catch {
      /* ignore */
    }
    return '';
  }

  loadGateway(): string {
    return HotspotBackend.loadGatewayFile();
  }

  private saveGateway(ip: string): void {
    try {
      ensureDirs();
      writeFileSync(GW_FILE, ip, 'utf-8');
    } catch {
      log.debug('网关缓存写入失败');
    }
  }

  // ----------------------------- 采集循环 ----------------------------- //
  private async detectCaps(): Promise<void> {
    try {
      this.caps = await this.controller.capabilities(true);
    } catch (exc) {
      log.exception('能力探测失败', exc);
    }
  }

  private tick(): void {
    this.loopChain = this.loopChain
      .then(() => this.gatherFast())
      .catch((exc) => log.exception('快采集异常', exc));
    if (!this.slowRunning && timeNow() - this.lastSlow >= POLL_SLOW) {
      void this.gatherSlow();
    }
  }

  private async gatherSlow(force = false): Promise<void> {
    if (this.slowRunning) return;
    this.slowRunning = true;
    let status = this.status;
    try {
      const [st, raw] = await this.controller.snapshot();
      status = st;
      this.status = st;
      this.raw = [...(raw ?? [])];
      if (st.active && !this.startedAt) this.startedAt = timeNow();
      if (!st.active) this.startedAt = 0;
      if (force || !this.gwTs || timeNow() - this.gwTs > GW_TTL) {
        const gw = await netinfo.hotspotGatewayIp();
        if (gw) {
          if (gw !== this.gateway) {
            this.gateway = gw;
            this.saveGateway(gw);
          }
          this.gwTs = timeNow();
        }
      }
    } catch (exc) {
      log.exception('慢采集异常', exc);
    } finally {
      this.lastSlow = timeNow();
      this.slowRunning = false;
    }
    try {
      await this.syncPortal(Boolean(status?.active));
    } catch (exc) {
      log.exception('门户同步异常', exc);
    }
    this.updateTrayTooltip(status!);
    await this.gatherFast();
  }

  private updateTrayTooltip(status: HotspotStatus): void {
    try {
      if (status.active) {
        const online = this.devman.all().filter((d) => d.online).length;
        this.trayTooltip = fmt('{ssid} · 运行中 · 在线 {n} 台', { ssid: status.ssid || '热点', n: online });
      } else {
        this.trayTooltip = 'Windows Hotspot Butler · 热点未开启';
      }
    } catch {
      /* ignore */
    }
  }

  private async gatherFast(): Promise<void> {
    const prefix = netinfo.subnetPrefixOf(this.gateway);
    this.arp = Object.fromEntries((await netinfo.arpEntries(prefix)).map(([ip, mac]) => [ip, mac]));
    const status = this.status ?? this.controller.lastStatus;
    // 首次还没拿到系统状态时乐观处理，避免开局空列表
    const active = this.status !== null ? Boolean(status.active) : true;
    const clients = await this.devman.sync(this.raw, {
      active,
      gatewayIp: this.gateway,
      arpCache: this.arp,
      resolveNames: false,
    });
    await this.devman.attachTotals(clients);
    if (active && !this.startedAt) this.startedAt = timeNow();
    const [down, up] = this.traffic.totalRateNow();

    const devices: Json[] = [];
    for (const d of clients) {
      devices.push({
        mac: d.mac,
        ip: d.ip,
        name: d.name,
        hostname: d.hostname,
        vendor: d.vendor ? i18n.t(d.vendor) : '',
        type: d.devType,
        emoji: TYPE_EMOJI[d.devType] ?? '📶',
        type_label: TYPE_LABEL[d.devType] ?? '未知设备',
        online: d.online,
        blocked: d.mac in this.blocked,
        allowed: this.captive.allow.has(d.ip, d.mac),
        rate_down: d.rateDown,
        rate_up: d.rateUp,
        rate_down_text: humanRate(d.rateDown),
        rate_up_text: humanRate(d.rateUp),
        session_rx: d.sessionRx,
        session_tx: d.sessionTx,
        total_rx: d.totalRx,
        total_tx: d.totalTx,
        total_text: humanBytes(d.totalRx + d.totalTx),
        last_seen_text: d.online ? '在线' : agoText(d.lastSeen),
        portal_accepted: d.portalAccepted,
      });
    }

    const online = clients.filter((d) => d.online).length;
    const nowOnline = new Set(clients.filter((d) => d.online).map((d) => d.mac));
    const prev = this.prevOnline;
    this.prevOnline = nowOnline;
    for (const mac of nowOnline) {
      if (prev.has(mac)) continue;
      const d = clients.find((c) => c.mac === mac);
      if (d && !d.portalAccepted) {
        const text = fmt('新设备接入：{n}（{ip}）', { n: d.name, ip: d.ip || d.mac });
        this.toast(text, 'info');
        this.trayNotify(text);
        this._desktopNotifier?.('Windows Hotspot Butler', text);
      }
    }
    this.snap = {
      ts: timeNow(),
      devices,
      stats: {
        online,
        total: clients.length,
        down,
        up,
        down_text: humanRate(down),
        up_text: humanRate(up),
      },
    };
  }

  private async syncPortal(active: boolean): Promise<void> {
    const want = Boolean(this.cfg.portal.enabled) && Boolean(active);
    if (want && !this.captive.running) {
      try {
        this.captive.ctx = this.ctx;
        const [ok, msg] = await this.captive.start(this.cfg.portal, true);
        if (ok) {
          this.portalAuto = true;
          this.toast('强制门户已随热点启动（DNS 劫持生效）', 'info');
        } else {
          this.toast(fmt('门户启动失败：{m}', { m: msg }), 'error');
        }
      } catch (exc) {
        log.exception('门户自动启动失败', exc);
        this.toast(fmt('门户启动失败：{m}', { m: String(exc) }), 'error');
      }
    } else if (!want && this.captive.running && this.portalAuto) {
      try {
        await this.captive.stop();
        this.portalAuto = false;
        this.toast('热点已关闭，强制门户已停止', 'info');
      } catch (exc) {
        log.debug(`门户自动停止异常：${String(exc)}`);
      }
    }
  }

  // ----------------------------- 对外：读状态 ----------------------------- //
  getState(): Json {
    const st = this.controller.lastStatus;
    const snap = { ...this.snap };
    const toasts = this.toasts;
    this.toasts = [];
    const busyKeys = Object.keys(this.busy);
    const caps = { ...this.caps };

    const canHost = Boolean(caps['can_host_hotspot'] ?? true);
    const blockReason = canHost ? '' : String(caps['host_block_reason'] ?? '');

    return {
      ready: Boolean(this.ready && Object.keys(snap).length),
      admin: pshell.isAdmin(),
      busy: busyKeys.length > 0,
      toasts,
      mini_shown: Boolean(this.cfg.mini_window.show),
      hotspot: {
        active: Boolean(st.active),
        state: st.state,
        state_text: stateText(st),
        ssid: st.ssid || this.cfg.hotspot.ssid,
        passphrase: st.passphrase || this.cfg.hotspot.passphrase,
        band: st.band,
        backend: st.backend,
        backend_label: i18n.t(this.controller.backendLabelSync()),
        client_count: st.clientCount,
        max_clients: st.maxClients,
        message: st.message,
        started_at: this.startedAt,
        uptime: uptimeText(this.startedAt),
      },
      caps: {
        can_host: canHost,
        block_reason: blockReason,
        driver: caps['driver'] ?? '',
        radios: caps['radios'] ?? '',
        wifi_direct: caps['wifi_direct_supported'] ?? null,
        soft_ap: caps['soft_ap_supported'] ?? null,
        hosted: caps['hosted_supported'] ?? null,
        band_5: caps['band_5_supported'] ?? false,
        notes: caps['notes'] ?? [],
      },
      devices: snap['devices'] ?? [],
      stats: snap['stats'] ?? { online: 0, total: 0, down: 0, up: 0, down_text: '0 B/s', up_text: '0 B/s' },
      config: {
        ssid: this.cfg.hotspot.ssid,
        passphrase: this.cfg.hotspot.passphrase,
        security: this.cfg.hotspot.security,
        band: this.cfg.hotspot.band,
        backend: this.cfg.hotspot.backend,
        auto_start: this.cfg.hotspot.auto_start,
        max_clients: this.cfg.hotspot.max_clients,
        start_with_windows: this.cfg.start_with_windows,
        close_to_tray: this.cfg.close_to_tray,
        hotkey_enabled: this.cfg.hotkey_enabled,
        portal_enabled: this.cfg.portal.enabled,
        portal_dns: this.cfg.portal.dns_redirect,
        portal_template: this.cfg.portal.template,
        portal_title: this.cfg.portal.title,
        portal_notice: this.cfg.portal.notice,
        portal_button: this.cfg.portal.button,
        portal_password: this.cfg.portal.access_password,
        portal_schedule_start: this.cfg.portal.schedule_start,
        portal_schedule_end: this.cfg.portal.schedule_end,
        confirm_exit_hotspot: this.cfg.confirm_exit_hotspot,
      },
      portal: this.captive.status(),
      temp_password: this.tempPasswordStatus(),
      share: this.share.status(),
      port_fwd: this.portfwd.listRules(),
      gateway: this.gateway,
      auto_stop: this.stopStatus(),
      icons: ICON_CHOICES.map(({ key, label }) => ({ key, label: i18n.t(label) })),
      lang: i18n.currentLang(),
    };
  }

  // ----------------------------- 对外：操作 ----------------------------- //
  private runOp(key: string, fn: () => Promise<[boolean, string]>): Json {
    if (key in this.busy) {
      return { ok: false, started: false, msg: i18n.t('操作进行中，请稍候') };
    }
    this.busy[key] = timeNow();
    void (async () => {
      try {
        const [ok, msg] = await fn();
        if ((key === 'start' || key === 'stop') && ok) {
          // 开关成功立即反映到界面，不等下一次慢采集
          this.optimisticStatus(key === 'start');
        }
        this.toast(String(msg), ok ? 'success' : 'error');
      } catch (exc) {
        log.exception(`操作失败 ${key}`, exc);
        this.toast(fmt('操作失败：{e}', { e: String(exc) }), 'error');
      } finally {
        delete this.busy[key];
        void this.gatherSlow(true);
      }
    })();
    return { ok: true, started: true };
  }

  private optimisticStatus(active: boolean): void {
    const st = this.controller.lastStatus;
    this.controller.lastStatus = hotspotStatus({
      ...st,
      active,
      state: active ? 'on' : 'off',
      updated: timeNow(),
    });
    if (active) {
      this.controller.everActive = true;
      if (this.controller.wlanCaps !== null) {
        this.controller.wlanCaps.everActive = true;
        this.controller.wlanCaps.activeNow = true;
      }
    }
    this.status = this.controller.lastStatus;
    if (active && !this.startedAt) this.startedAt = timeNow();
    if (!active) {
      this.startedAt = 0;
      if (this.stopTimer) {
        clearTimeout(this.stopTimer);
        this.stopTimer = null;
      }
      this.stopDeadline = 0;
    }
    void this.syncPortal(active).catch(() => {});
    this.updateTrayTooltip(this.controller.lastStatus);
  }

  startHotspot(): Json {
    return this.runOp('start', () => this.controller.start());
  }

  stopHotspot(): Json {
    return this.runOp('stop', () => this.controller.stop());
  }

  toggle(): Json {
    return this.controller.lastStatus.active ? this.stopHotspot() : this.startHotspot();
  }

  refresh(): Json {
    void this.gatherSlow(true);
    void this.gatherFast();
    return { ok: true, started: true };
  }

  saveConfigPatch(patch: Json): Json {
    try {
      const h = this.cfg.hotspot;
      for (const key of ['ssid', 'passphrase', 'security', 'band', 'backend'] as const) {
        if (key in patch) (h as unknown as Record<string, string>)[key] = String(patch[key]);
      }
      if ('auto_start' in patch) h.auto_start = Boolean(patch['auto_start']);
      if ('max_clients' in patch) h.max_clients = Math.trunc(Number(patch['max_clients']) || 8);
      if ('start_with_windows' in patch) {
        this.cfg.start_with_windows = Boolean(patch['start_with_windows']);
        this.applyStartWithWindows(Boolean(patch['start_with_windows']));
      }
      if ('close_to_tray' in patch) this.cfg.close_to_tray = Boolean(patch['close_to_tray']);
      if ('confirm_exit_hotspot' in patch) this.cfg.confirm_exit_hotspot = Boolean(patch['confirm_exit_hotspot']);
      if ('hotkey_enabled' in patch) this.cfg.hotkey_enabled = Boolean(patch['hotkey_enabled']);
      // 旧版 Tk 设置页字段（本次一并支持，原 webui 未暴露）
      if ('enforce_max_clients' in patch) h.enforce_max_clients = Boolean(patch['enforce_max_clients']);
      if ('wps_enabled' in patch) h.wps_enabled = Boolean(patch['wps_enabled']);
      if ('poll_interval' in patch) this.cfg.poll_interval = Number(patch['poll_interval']) || 4;
      if ('traffic_backend' in patch) this.cfg.traffic_backend = String(patch['traffic_backend']);
      if ('demo_mode' in patch) this.cfg.demo_mode = Boolean(patch['demo_mode']);
      const p = this.cfg.portal;
      const portalMap: [string, string, 'bool' | 'str'][] = [
        ['portal_enabled', 'enabled', 'bool'],
        ['portal_dns', 'dns_redirect', 'bool'],
        ['portal_template', 'template', 'str'],
        ['portal_title', 'title', 'str'],
        ['portal_notice', 'notice', 'str'],
        ['portal_button', 'button', 'str'],
        ['portal_password', 'access_password', 'str'],
        ['portal_subtitle', 'subtitle', 'str'],
        ['portal_footer', 'footer', 'str'],
        ['portal_custom_html', 'custom_html', 'str'],
        ['portal_require_accept', 'require_accept', 'bool'],
        ['portal_auto_open_preview', 'auto_open_preview', 'bool'],
      ];
      for (const [key, attr, typ] of portalMap) {
        if (key in patch) {
          (p as unknown as Record<string, unknown>)[attr] =
            typ === 'bool' ? Boolean(patch[key]) : String(patch[key]);
        }
      }
      if ('portal_schedule_start' in patch) {
        p.schedule_start = Math.max(0, Math.min(23, Math.trunc(Number(patch['portal_schedule_start']) || 0)));
      }
      if ('portal_schedule_end' in patch) {
        p.schedule_end = Math.max(1, Math.min(24, Math.trunc(Number(patch['portal_schedule_end']) || 24)));
      }
      normalizeConfig(this.cfg);
      saveConfig(this.cfg);
      this.controller.cfg = this.cfg.hotspot;
      this.toast(i18n.t('设置已保存'), 'success');
      void this.controller.applyConfig(this.cfg.hotspot).catch(() => {});
      return { ok: true, msg: i18n.t('已保存') };
    } catch (exc) {
      log.exception('保存配置失败', exc);
      return { ok: false, msg: String(exc) };
    }
  }

  applyNow(): Json {
    return this.runOp('apply', () => this.controller.applyConfig(this.cfg.hotspot));
  }

  private applyStartWithWindows(enabled: boolean): void {
    // 写 HKCU\...\Run 注册表项，指向 Run.bat（等价原版指向 main.py）
    if (!pshell.IS_WIN) return;
    const value = `"${join(PROJ_ROOT, 'Run.bat')}"`;
    const args = enabled
      ? ['reg', 'add', 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', '/v', 'WifiHotspotManager', '/t', 'REG_SZ', '/d', value, '/f']
      : ['reg', 'delete', 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', '/v', 'WifiHotspotManager', '/f'];
    execFile(args[0], args.slice(1), { timeout: 15000, windowsHide: true }, () => {});
  }

  // ----------------------------- 设备 ----------------------------- //
  setDeviceName(mac: string, name: string): Json {
    try {
      this.devman.rename(mac, name);
      void this.gatherFast();
      return { ok: true, msg: '已重命名' };
    } catch (exc) {
      return { ok: false, msg: String(exc) };
    }
  }

  setDeviceType(mac: string, dtype: string): Json {
    try {
      this.devman.setType(mac, dtype, true);
      void this.gatherFast();
      return { ok: true, msg: '已更新设备类型' };
    } catch (exc) {
      return { ok: false, msg: String(exc) };
    }
  }

  forgetDevice(mac: string): Json {
    try {
      this.devman.forget(mac);
      void this.gatherFast();
      return { ok: true, msg: i18n.t('已移除设备') };
    } catch (exc) {
      return { ok: false, msg: String(exc) };
    }
  }

  private static ruleName(mac: string, direction: string): string {
    return `WHM-BLOCK-${normalizeMac(mac).replace(/:/g, '')}-${direction}`;
  }

  private loadBlocked(): Record<string, string> {
    try {
      if (existsSync(BLOCK_FILE)) return JSON.parse(readFileSync(BLOCK_FILE, 'utf-8'));
    } catch {
      log.debug('黑名单读取失败');
    }
    return {};
  }

  private saveBlocked(): void {
    try {
      ensureDirs();
      writeFileSync(BLOCK_FILE, JSON.stringify(this.blocked, null, 2), 'utf-8');
    } catch {
      log.warn('黑名单保存失败');
    }
  }

  async blockDevice(mac: string): Promise<Json> {
    mac = normalizeMac(mac);
    const dev = this.devman.get(mac);
    const ip = dev?.ip ?? '';
    if (!ip) return { ok: false, msg: '该设备当前不在线，拿不到 IP，无法禁用' };
    const base = ['netsh', 'advfirewall', 'firewall', 'add', 'rule', 'action=block', 'enable=yes', `remoteip=${ip}`];
    let okAll = true;
    let msg = '';
    for (const direction of ['out', 'in']) {
      const args = [...base, `name=${HotspotBackend.ruleName(mac, direction.toUpperCase())}`, `dir=${direction}`];
      const { code, stdout, stderr } = await pshell.run(args, 20);
      if (code !== 0) {
        okAll = false;
        msg = (stdout || stderr).trim().slice(0, 200);
        break;
      }
    }
    if (okAll) {
      this.blocked[mac] = ip;
      this.saveBlocked();
      void this.gatherFast();
      return { ok: true, msg: `已禁止 ${ip} 上网` };
    }
    return { ok: false, msg: msg || '添加防火墙规则失败（需要管理员权限）' };
  }

  async unblockDevice(mac: string): Promise<Json> {
    mac = normalizeMac(mac);
    for (const direction of ['OUT', 'IN']) {
      await pshell.run(
        ['netsh', 'advfirewall', 'firewall', 'delete', 'rule', `name=${HotspotBackend.ruleName(mac, direction)}`],
        20,
      );
    }
    delete this.blocked[mac];
    this.saveBlocked();
    void this.gatherFast();
    return { ok: true, msg: i18n.t('已恢复上网') };
  }

  // ----------------------------- 强制门户 ----------------------------- //
  portalStart(): Json {
    return this.runOp('portal', async () => {
      this.captive.ctx = this.ctx;
      this.portalAuto = false; // 用户手动控制
      return this.captive.start(this.cfg.portal, true);
    });
  }

  portalStop(): Json {
    return this.runOp('portal', async () => {
      this.portalAuto = false;
      return this.captive.stop();
    });
  }

  portalAllow(mac: string): Json {
    const dev = this.devman.get(normalizeMac(mac));
    if (!dev || !dev.ip) return { ok: false, msg: '设备不在线，无法放行' };
    this.captive.allowIp(dev.ip, dev.mac);
    void this.gatherFast();
    return { ok: true, msg: `已放行 ${dev.ip}` };
  }

  portalRevoke(mac: string): Json {
    const dev = this.devman.get(normalizeMac(mac));
    if (!dev) return { ok: false, msg: i18n.t('未找到设备') };
    this.captive.revokeIp(dev.ip, dev.mac);
    void this.gatherFast();
    return { ok: true, msg: fmt('已取消放行 {ip}', { ip: dev.ip || dev.mac }) };
  }

  // ----------------------------- 其它 ----------------------------- //
  diagnose(): Json {
    void (async () => {
      try {
        this.diag = await this.controller.diagnose();
      } catch (exc) {
        this.diag = [`诊断失败：${String(exc)}`];
      }
    })();
    return { ok: true, started: true };
  }

  getDiagnose(): Json {
    return { lines: [...this.diag] };
  }

  /** WiFi 二维码（WIFI: 标准格式；依赖可选包 qrcode）。 */
  async wifiQrcode(): Promise<Json> {
    try {
      const esc1 = (s: string): string => {
        for (const [a, b] of [
          ['\\', '\\\\'], [';', '\\;'], [',', '\\,'], [':', '\\:'], ['"', '\\"'],
        ] as [string, string][]) {
          s = s.split(a).join(b);
        }
        return s;
      };
      const hs = this.controller.lastStatus;
      const ssid = hs.ssid || this.cfg.hotspot.ssid;
      const passphrase =
        this.cfg.hotspot.security === 'open' ? '' : hs.passphrase || this.cfg.hotspot.passphrase;
      const security = this.cfg.hotspot.security === 'open' ? 'nopass' : 'WPA';
      const payload = `WIFI:T:${security};S:${esc1(ssid)};P:${esc1(passphrase)};;`;
      // 可选依赖：npm i qrcode（未安装时降级为错误提示，与原版一致）
      const QR = (await import(/* @vite-ignore */ 'qrcode' as string)) as {
        toDataURL(text: string, opts?: Record<string, unknown>): Promise<string>;
      };
      const dataUri = await QR.toDataURL(payload, { width: 256, margin: 2 });
      return { ok: true, data: dataUri, ssid };
    } catch (exc) {
      log.exception('二维码生成失败', exc);
      return { ok: false, msg: '缺少 qrcode 库：npm install qrcode' };
    }
  }

  // ----------------------------- 定时关闭 ----------------------------- //
  scheduleStop(minutes: number): Json {
    if (this.stopTimer) {
      clearTimeout(this.stopTimer);
      this.stopTimer = null;
    }
    if (minutes <= 0) return { ok: true, msg: i18n.t('已取消定时关闭') };
    this.stopDeadline = timeNow() + minutes * 60;
    const deadline = this.stopDeadline;
    this.stopTimer = setTimeout(() => {
      this.toast('定时时间到，正在关闭热点…', 'info');
      void Promise.resolve(this.stopHotspot());
      this.stopDeadline = 0;
      this.stopTimer = null;
    }, minutes * 60 * 1000);
    void deadline;
    return { ok: true, msg: fmt('将在 {m} 分钟后自动关闭热点', { m: minutes }) };
  }

  stopStatus(): { remaining: number } {
    const dl = this.stopDeadline;
    if (!dl || dl < timeNow()) return { remaining: 0 };
    return { remaining: Math.trunc(dl - timeNow()) };
  }

  // ----------------------------- 临时密码 ----------------------------- //
  tempPasswordStart(hours: number): Json {
    const tempPw = randomPassword(8);
    const savedPw = this.controller.lastStatus.passphrase || this.cfg.hotspot.passphrase;
    if (this.tempTimer) {
      clearTimeout(this.tempTimer);
      this.tempTimer = null;
    }
    this.cfg.hotspot.passphrase = tempPw;
    saveConfig(this.cfg);
    this.controller.cfg = this.cfg.hotspot;
    void this.controller.applyConfig(this.cfg.hotspot).catch(() => {});
    if (this.controller.lastStatus.active) {
      this.toast(`临时密码已生效：${tempPw}`, 'success');
    } else {
      this.toast(`临时密码已设置：${tempPw}（热点开启后生效）`, 'info');
    }
    this.trayNotify(`临时密码：${tempPw}`);
    this._desktopNotifier?.('Windows Hotspot Butler', `临时密码：${tempPw}`);

    const restore = (): void => {
      this.cfg.hotspot.passphrase = savedPw;
      this.cfg.temp_password = '';
      this.cfg.temp_password_until = 0;
      saveConfig(this.cfg);
      this.controller.cfg = this.cfg.hotspot;
      void this.controller.applyConfig(this.cfg.hotspot).catch(() => {});
      this.toast(i18n.t('临时密码已到期，已恢复原密码'), 'info');
      this.trayNotify(i18n.t('临时密码已到期，热点密码已恢复'));
      this.tempTimer = null;
      this.tempDeadline = 0;
    };
    this.tempDeadline = timeNow() + hours * 3600;
    this.tempTimer = setTimeout(restore, hours * 3600 * 1000);
    this.cfg.temp_password = tempPw;
    this.cfg.temp_password_until = this.tempDeadline;
    saveConfig(this.cfg);
    return {
      ok: true,
      msg: fmt('临时密码 {pw}，{h} 小时后自动恢复', { pw: tempPw, h: hours }),
      password: tempPw,
    };
  }

  tempPasswordStop(): Json {
    if (this.tempTimer) {
      clearTimeout(this.tempTimer);
      this.tempTimer = null;
    }
    this.tempDeadline = 0;
    if (this.cfg.temp_password) {
      this.cfg.temp_password = '';
      this.cfg.temp_password_until = 0;
      saveConfig(this.cfg);
    }
    return { ok: true, msg: i18n.t('已取消临时密码（密码保持当前值不变）') };
  }

  tempPasswordStatus(): Json {
    const dl = this.tempDeadline;
    const active = Boolean(this.cfg.temp_password) && dl > timeNow();
    return {
      active,
      password: active ? this.cfg.temp_password : '',
      remaining: active ? Math.trunc(dl - timeNow()) : 0,
    };
  }

  // ----------------------------- 配置导入导出 ----------------------------- //
  exportConfig(): Json {
    try {
      ensureDirs();
      const p = (n: number) => String(n).padStart(2, '0');
      const d = new Date();
      const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
      const exportData = {
        app: 'WifiHotspotManager',
        version: 1,
        exported_at: d.toLocaleString('sv-SE'),
        config: this.cfg,
        devices: this.store.all(),
      };
      const exportFile = join(EXPORT_DIR, `export-${stamp}.json`);
      writeFileSync(exportFile, JSON.stringify(exportData, null, 2), 'utf-8');
      openFolder(DATA_DIR);
      return { ok: true, msg: `已导出到 ${exportFile}` };
    } catch (exc) {
      log.exception('配置导出失败', exc);
      return { ok: false, msg: String(exc) };
    }
  }

  async exportCsv(): Promise<Json> {
    try {
      const rows = await this.db.dailyByDevice(30);
      const names: Record<string, string> = {};
      for (const rec of this.store.all()) {
        names[String(rec['mac'])] = this.store.displayName(rec) || String(rec['mac']);
      }
      const p = (n: number) => String(n).padStart(2, '0');
      const d = new Date();
      const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
      const exportFile = join(EXPORT_DIR, `traffic-${stamp}.csv`);
      // UTF-8 BOM，Excel 直接打开不乱码
      let csv = '\uFEFF日期,设备,MAC,下载(bytes),上传(bytes),合计(bytes)\n';
      for (const r of rows) {
        csv += `${r.day},${names[r.mac] ?? r.mac},${r.mac},${r.rx},${r.tx},${r.rx + r.tx}\n`;
      }
      writeFileSync(exportFile, csv, 'utf-8');
      openFolder(DATA_DIR);
      return { ok: true, msg: fmt('已导出到 {p}', { p: exportFile }) };
    } catch (exc) {
      log.exception('CSV 导出失败', exc);
      return { ok: false, msg: String(exc) };
    }
  }

  async importConfig(jsonText: string): Promise<Json> {
    // 内容由前端 <input type=file> 读入后 POST 上来（等价原版文件选择框）
    try {
      const data = JSON.parse(jsonText) as Json;
      if (data['app'] !== 'WifiHotspotManager' || typeof data['config'] !== 'object') {
        return { ok: false, msg: '文件格式不正确（不是本程序导出的配置）' };
      }
      const { configFromDict } = await import('./config.ts');
      this.cfg = configFromDict(data['config'] as Json);
      saveConfig(this.cfg);
      i18n.setLang(this.cfg.language);
      this.controller.cfg = this.cfg.hotspot;
      void this.controller.applyConfig(this.cfg.hotspot).catch(() => {});
      this.toast(i18n.t('配置已导入并下发'), 'success');
      return { ok: true, msg: i18n.t('配置已导入') };
    } catch (exc) {
      log.exception('配置导入失败', exc);
      return { ok: false, msg: String(exc) };
    }
  }

  // ----------------------------- 主题 / 语言 ----------------------------- //
  setTheme(theme: string): Json {
    if (theme === 'dark' || theme === 'light') {
      this.cfg.theme = theme;
      saveConfig(this.cfg);
    }
    return { ok: true };
  }

  getTheme(): string {
    return this.cfg.theme;
  }

  getLanguage(): string {
    return i18n.currentLang();
  }

  setLanguage(lang: string): Json {
    this.cfg.language = lang === 'zh_CN' || lang === 'en' ? lang : 'auto';
    saveConfig(this.cfg);
    i18n.setLang(this.cfg.language);
    return { ok: true, lang: i18n.currentLang() };
  }

  getI18n(): Record<string, string> {
    // en 模式给整张翻译表，zh 模式给空表（原文即中文）
    return i18n.currentLang() === 'en' ? { ...i18n.ZH2EN } : {};
  }

  miniState(): Json {
    const snap = { ...this.snap };
    const st = this.controller.lastStatus;
    const stats = (snap['stats'] as Json) ?? {};
    return {
      active: Boolean(st.active),
      online: Number(stats['online'] ?? 0),
      down: Number(stats['down'] ?? 0),
      up: Number(stats['up'] ?? 0),
      theme: this.cfg.theme,
      lang: i18n.currentLang(),
    };
  }

  miniToggle(): Json {
    return this.toggle();
  }

  toggleMini(): Json {
    // Web 版：浮窗即 /mini 路由，由前端 localStorage 记忆；此处返回 shown 供按钮高亮
    return { ok: true, shown: true };
  }

  confirmExit(): Json {
    this.exitConfirmed = true;
    return { ok: true };
  }

  cancelExit(): Json {
    this.exitConfirmed = false;
    return { ok: true };
  }

  // ----------------------------- 窗口兼容 API（浏览器里 no-op，保留原版调用契约） ----------------------------- //
  minimize(): Json {
    // Web 版：浏览器窗口最小化由宿主接管，这里保持返回 ok 不报错
    return { ok: true };
  }

  hideMain(): Json {
    return { ok: true };
  }

  close(): Json {
    // 原版 close：close_to_tray 时隐藏到托盘，否则销毁窗口。
    // Web 版没有原生窗，close_to_tray 语义映射为"保留页面但清空退出确认"。
    if (this.cfg.close_to_tray) return { ok: true, hidden: true };
    return { ok: true };
  }

  bootReady(): Json {
    // 原版：主窗前端上报就绪 → 恢复悬浮窗。Web 版浮窗由用户点击按钮打开。
    return { ok: true };
  }

  miniDragStart(): Json {
    // Web 版浮窗由浏览器窗口边框拖动
    return { ok: false };
  }

  miniRestoreMain(): Json {
    return { ok: true };
  }

  openMini(): Json {
    return { ok: true };
  }

  closeMini(): Json {
    return { ok: true };
  }

  miniClosePinned(): Json {
    this.cfg.mini_window.show = false;
    saveConfig(this.cfg);
    return { ok: true };
  }

  // ----------------------------- 统计报表 ----------------------------- //
  async getStatsReport(): Promise<Json> {
    try {
      const daily = await this.db.dailyAll(14);
      const names: Record<string, string> = {};
      for (const rec of this.store.all()) {
        names[String(rec['mac'])] = this.store.displayName(rec) || String(rec['mac']);
      }
      const top = (await this.db.topDevices(8)).map((t) => ({
        name: names[t.mac] ?? t.mac,
        mac: t.mac,
        total_text: humanBytes(t.rx + t.tx),
      }));
      const queries = await this.db.dnsQueries(80);
      for (const q of queries) {
        const ts = Number(q['ts'] ?? 0);
        q['time_text'] = hms(ts);
        q['name'] = names[String(q['mac'] ?? '')] || String(q['ip'] ?? '');
      }
      const domains = await this.db.topDomains(10);
      const grand = await this.db.grandTotal();
      return {
        ok: true,
        daily: daily.map(([day, rx, tx]) => ({ day, rx, tx, total_text: humanBytes(rx + tx) })),
        top,
        queries,
        top_domains: domains,
        grand: {
          rx: humanBytes(grand[0]),
          tx: humanBytes(grand[1]),
          total: humanBytes(grand[0] + grand[1]),
        },
      };
    } catch (exc) {
      log.exception('统计报表失败', exc);
      return { ok: false, msg: String(exc) };
    }
  }

  // ----------------------------- 网速测试 ----------------------------- //
  speedTest(): Json {
    return this.runOp('speedtest', async () => {
      const res = await runSpeedTest();
      if (res.ok) this.toast(fmt('测速结果：{m}', { m: res.msg }), 'success');
      return [res.ok, res.msg];
    });
  }

  // ----------------------------- 文件共享 ----------------------------- //
  shareStart(): Json {
    return this.runOp('share', async () => this.share.start());
  }

  shareStop(): Json {
    return this.runOp('share', async () => this.share.stop());
  }

  shareOpenFolder(): Json {
    try {
      mkdirSync(SHARE_DIR, { recursive: true });
      openFolder(SHARE_DIR);
      return { ok: true };
    } catch (exc) {
      return { ok: false, msg: String(exc) };
    }
  }

  // ----------------------------- 端口转发 ----------------------------- //
  async pfAdd(name: string, listenPort: number, connectIp: string, connectPort: number, proto = 'tcp'): Promise<Json> {
    const [ok, msg] = await this.portfwd.add(name, listenPort, connectIp, connectPort, proto);
    return { ok, msg };
  }

  async pfRemove(name: string): Promise<Json> {
    const [ok, msg] = await this.portfwd.remove(name);
    return { ok, msg };
  }

  // ----------------------------- 门户辅助 ----------------------------- //
  allowedList(): (AllowDetail & { ip: string })[] {
    return this.captive.allowedList();
  }

  async portalVisits(limit = 40): Promise<Record<string, unknown>[]> {
    return this.db.portalVisits(limit);
  }

  async resetDeviceUsage(mac: string): Promise<Json> {
    await this.db.resetDevice(mac);
    void this.gatherFast();
    return { ok: true, msg: '已清除该设备用量' };
  }

  async resolveHostnameFor(mac: string): Promise<Json> {
    const name = await this.devman.resolveHostname(mac, true);
    if (!name) return { ok: false, msg: '未能解析主机名' };
    void this.gatherFast();
    return { ok: true, name, msg: name };
  }

  // ----------------------------- 旧版 Tk 界面专属功能（本次 1:1 补齐到 Web API） ----------------------------- //
  setDeviceNote(mac: string, note: string): Json {
    this.devman.setNote(mac, note);
    void this.gatherFast();
    return { ok: true, msg: '备注已保存' };
  }

  setDeviceIcon(mac: string, icon: string, customPath = ''): Json {
    this.devman.setIcon(mac, icon, customPath);
    void this.gatherFast();
    return { ok: true, msg: '图标已更新' };
  }

  setDeviceLimited(mac: string, limited: boolean): Json {
    this.devman.setLimited(mac, limited);
    void this.gatherFast();
    return { ok: true, msg: '标记已更新' };
  }

  async deviceDaily(mac: string, days = 14): Promise<Json> {
    const rows = await this.db.daily(mac, days);
    return {
      ok: true,
      daily: rows.map(([day, rx, tx]) => ({ day, rx, tx, total_text: humanBytes(rx + tx) })),
    };
  }

  async clearAllStats(): Promise<Json> {
    // 原版：清空全部统计（每台设备 + __total__）
    const totals = await this.db.totalsMap();
    for (const mac of Object.keys(totals)) await this.db.resetDevice(mac);
    void this.gatherFast();
    return { ok: true, msg: '已清空全部统计' };
  }

  async portalPreview(): Promise<Json> {
    const { previewHtml } = await import('./portal.ts');
    const html = await previewHtml(this.cfg.portal, this.ctx);
    return { ok: true, html };
  }

  async readSystemValues(): Promise<Json> {
    // 原版"读取系统当前值"：把 WinRT 上报的 SSID/密码/频段/上限写回配置
    const [st] = await this.controller.snapshot();
    if (st.ssid) this.cfg.hotspot.ssid = st.ssid;
    if (st.passphrase) this.cfg.hotspot.passphrase = st.passphrase;
    if (st.band && ['auto', '2.4', '5'].includes(st.band)) this.cfg.hotspot.band = st.band;
    if (st.maxClients) this.cfg.hotspot.max_clients = st.maxClients;
    normalizeConfig(this.cfg);
    saveConfig(this.cfg);
    return { ok: true, msg: '已读取系统当前值', config: this.cfg.hotspot };
  }

  async setTrafficMode(trafficBackend: string, demoMode?: boolean): Promise<Json> {
    // 原版设置页：运行时切换统计后端 / 演示模式
    this.cfg.traffic_backend = trafficBackend;
    if (demoMode !== undefined) this.cfg.demo_mode = Boolean(demoMode);
    saveConfig(this.cfg);
    await this.traffic.restart(trafficBackend, demoMode);
    return { ok: true, msg: `统计后端：${this.traffic.statusText}` };
  }

  async relaunchAsAdmin(): Promise<Json> {
    const ok = await pshell.relaunchAsAdmin();
    return { ok, msg: ok ? '已请求以管理员身份重新启动' : '提权失败或当前非 Windows' };
  }

  openLogsFolder(): Json {
    openFolder(join(DATA_DIR, 'logs'));
    return { ok: true };
  }

  openDataFolder(): Json {
    openFolder(DATA_DIR);
    return { ok: true };
  }

  openNetworkSettings(): Json {
    // 原版快捷操作：ms-settings:network-mobilehotspot / ncpa.cpl
    if (pshell.IS_WIN) {
      execFile('cmd', ['/c', 'start', 'ms-settings:network-mobilehotspot'], { timeout: 5000 }, () => {});
      execFile('cmd', ['/c', 'start', 'ncpa.cpl'], { timeout: 5000 }, () => {});
    }
    return { ok: true };
  }

  async clearDemoData(): Promise<Json> {
    const { DEMO_CLIENTS } = await import('./traffic.ts');
    for (const [mac] of DEMO_CLIENTS) await this.db.resetDevice(mac);
    void this.gatherFast();
    return { ok: true, msg: '已清除演示数据' };
  }
}

function hms(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 用系统文件管理器打开目录（os.startfile 等价）。 */
function openFolder(path: string): void {
  try {
    if (process.platform === 'win32') execFile('explorer', [path], { timeout: 5000 }, () => {});
    else if (process.platform === 'darwin') execFile('open', [path], { timeout: 5000 }, () => {});
    else execFile('xdg-open', [path], { timeout: 5000 }, () => {});
  } catch {
    /* ignore */
  }
}

// 工程根（供开机自启指向 Run.bat）
import { PROJ_DIR as PROJ_ROOT } from './paths.ts';

export { setupLogging, readdirSync };
