/**
 * 业务内核入口：Rosezm 中间件 / api 路由通过本地相对路径 import（框架 BN-6 修复）。
 * - 单例 HotspotBackend（一个 worker 进程一个）。
 * - `apiCall(method, name, body)` 把后端方法统一暴露给 /api/state、/api/op/... 路由。
 */
import { HotspotBackend } from './backend.ts';
import { APP_TITLE, APP_VERSION, getLogger, setupLogging } from './paths.ts';

const log = getLogger('core');

let backend: HotspotBackend | null = null;
let booting: Promise<HotspotBackend> | null = null;

export function ensureBackend(): Promise<HotspotBackend> {
  if (backend) return Promise.resolve(backend);
  if (booting) return booting;
  // 构建期（rosezm build 的 prerender 会 import server bundle 并跑中间件/api
  // 烘焙）绝不启动后端：定时器会挂住构建进程退出，PowerShell 探测会拖慢构建。
  // 运行时标记由 rosezm-cli.mjs 注入：dev/preview/serve → WHM_RUNTIME=1。
  if (process.env.WHM_RUNTIME !== '1' && process.env.rosezm_DEV !== '1') {
    return Promise.reject(new Error('WHM_BACKEND_DISABLED_AT_BUILD'));
  }
  booting = (async () => {
    setupLogging();
    const b = new HotspotBackend();
    backend = b;
    log.info(`${APP_TITLE} v${APP_VERSION} 后端已启动`);
    return b;
  })();
  return booting;
}

export function getBackend(): HotspotBackend | null {
  return backend;
}

/** 关机钩子（rosezm.config.js 的 onShutdown 调用）。 */
export async function shutdownBackend(): Promise<void> {
  if (backend) {
    await backend.shutdown();
    backend = null;
  }
}
(globalThis as Record<string, unknown>).__whmShutdown = shutdownBackend;

/** 白名单：允许前端调用的后端方法（等价 pywebview 暴露的全部公共方法）。 */
const ASYNC_METHODS = [
  'blockDevice',
  'unblockDevice',
  'wifiQrcode',
  'getStatsReport',
  'exportCsv',
  'importConfig',
  'pfAdd',
  'pfRemove',
  'resetDeviceUsage',
  'resolveHostnameFor',
  'portalVisits',
  'deviceDaily',
  'clearAllStats',
  'portalPreview',
  'readSystemValues',
  'setTrafficMode',
  'relaunchAsAdmin',
  'clearDemoData',
] as const;

const SYNC_METHODS = [
  'startHotspot',
  'stopHotspot',
  'toggle',
  'refresh',
  'saveConfigPatch',
  'applyNow',
  'setDeviceName',
  'setDeviceType',
  'forgetDevice',
  'portalStart',
  'portalStop',
  'portalAllow',
  'portalRevoke',
  'diagnose',
  'getDiagnose',
  'scheduleStop',
  'stopStatus',
  'tempPasswordStart',
  'tempPasswordStop',
  'tempPasswordStatus',
  'exportConfig',
  'setTheme',
  'getTheme',
  'getLanguage',
  'setLanguage',
  'getI18n',
  'miniState',
  'miniToggle',
  'toggleMini',
  'confirmExit',
  'cancelExit',
  'getState',
  'shareStart',
  'shareStop',
  'shareOpenFolder',
  'speedTest',
  'allowedList',
  'minimize',
  'hideMain',
  'close',
  'bootReady',
  'miniDragStart',
  'miniRestoreMain',
  'openMini',
  'closeMini',
  'miniClosePinned',
  'setDeviceNote',
  'setDeviceIcon',
  'setDeviceLimited',
  'openLogsFolder',
  'openDataFolder',
  'openNetworkSettings',
] as const;

type MethodName = (typeof ASYNC_METHODS)[number] | (typeof SYNC_METHODS)[number];

/**
 * 统一 API 调度。
 * @param name  后端方法名（camelCase，与原 pywebview js_api 方法一一对应）
 * @param args  位置参数数组（来自前端 JSON body 的 args 字段）
 */
export async function apiCall(name: string, args: unknown[] = []): Promise<unknown> {
  const b = await ensureBackend();
  const isAsync = (ASYNC_METHODS as readonly string[]).includes(name);
  const isSync = (SYNC_METHODS as readonly string[]).includes(name);
  if (!isAsync && !isSync) {
    return { ok: false, msg: `未知操作：${name}` };
  }
  const fn = (b as unknown as Record<string, (...a: unknown[]) => unknown>)[name];
  if (typeof fn !== 'function') return { ok: false, msg: `未知操作：${name}` };
  try {
    return await Promise.resolve(fn.apply(b, args));
  } catch (exc) {
    log.exception(`API ${name} 失败`, exc);
    return { ok: false, msg: String(exc) };
  }
}

/** 桌面通知注册（Web 版由前端 Notification API 承担）。 */
export function setNotifier(fn: (title: string, body: string) => void): void {
  void ensureBackend().then((b) => b.setDesktopNotifier(fn));
}

export {
  HotspotBackend,
  TYPE_EMOJI,
  TYPE_LABEL,
  ICON_CHOICES,
} from './backend.ts';
export { APP_TITLE, APP_VERSION };
export { stateText, hotspotStatus } from './hotspot.ts';
export { humanBytes, humanRate, normalizeMac } from './util.ts';
export * as i18n from './i18n.ts';
export { setLang, currentLang, t as translate, ZH2EN } from './i18n.ts';

export default {
  ensureBackend,
  getBackend,
  apiCall,
  shutdownBackend,
  setNotifier,
};
