/**
 * 路径与日志（对应原项目 hotspot/core/paths.py）。
 * 所有用户数据统一存放在 %APPDATA%\WifiHotspotManager（非 Windows 回退到 ~/.WifiHotspotManager）。
 *
 * 注意：本文件会被打包进 dist/server.js，模块级路径必须在运行时计算，
 * 且工程根目录通过"向上找 rosezm.config.js"定位（构建期 import 时模块
 * 可能位于 .rosezm-build/ 临时目录）。
 */
import { existsSync, mkdirSync, appendFileSync, renameSync, statSync, unlinkSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const APP_NAME = 'WifiHotspotManager';
export const APP_TITLE = 'Windows Hotspot Butler';
export const APP_VERSION = '0.0.3';

function baseDir(): string {
  const appdata = process.env.APPDATA;
  if (appdata) return join(appdata, APP_NAME);
  return join(homedir(), '.' + APP_NAME);
}

export const DATA_DIR = baseDir();
export const CONFIG_FILE = join(DATA_DIR, 'config.json');
export const DEVICES_FILE = join(DATA_DIR, 'devices.json');
export const TRAFFIC_DB = join(DATA_DIR, 'traffic.sqlite3');
export const LOG_DIR = join(DATA_DIR, 'logs');
export const CUSTOM_ICON_DIR = join(DATA_DIR, 'icons');
export const CUSTOM_PORTAL_DIR = join(DATA_DIR, 'portal');
export const SHARE_DIR = join(DATA_DIR, 'share');

/** 工程根目录：从本模块（或打包后的 dist/server.js）向上找 rosezm.config.js。
 *  不能用 package.json 作标志：dist/ 里框架也会生成一个 dist/package.json，
 *  会把根目录误判为 dist/（导致模板/脚本路径全部失效）。 */
function projectRoot(): string {
  try {
    let dir = dirname(fileURLToPath(import.meta.url));
    for (let i = 0; i < 8; i++) {
      if (existsSync(join(dir, 'rosezm.config.js'))) return dir;
      const up = dirname(dir);
      if (up === dir) break;
      dir = up;
    }
  } catch {
    /* import.meta.url 不可用时回退 cwd */
  }
  return process.cwd();
}

export const PROJ_DIR = projectRoot();
export const TEMPLATE_DIR = join(PROJ_DIR, 'portal-templates');
export const SCRIPT_DIR = join(PROJ_DIR, 'scripts', 'ps1');
export const TETHERING_PS1 = join(SCRIPT_DIR, 'tethering.ps1');
export const ICS_PS1 = join(SCRIPT_DIR, 'ics.ps1');

const ALL_DIRS = [DATA_DIR, LOG_DIR, CUSTOM_ICON_DIR, CUSTOM_PORTAL_DIR, SHARE_DIR];

export function ensureDirs(): void {
  for (const d of ALL_DIRS) {
    try {
      mkdirSync(d, { recursive: true });
    } catch {
      /* ignore */
    }
  }
}

// --------------------------------------------------------------------------- //
//                                日志                                          //
// --------------------------------------------------------------------------- //
type Level = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR';
const LEVEL_ORDER: Record<Level, number> = { DEBUG: 10, INFO: 20, WARN: 30, ERROR: 40 };

const MAX_BYTES = 1024 * 512;
const BACKUP_COUNT = 3;

let logReady = false;
let consoleLevel: Level = 'INFO';

function rotate(file: string): void {
  try {
    if (!existsSync(file)) return;
    if (statSync(file).size < MAX_BYTES) return;
    for (let i = BACKUP_COUNT - 1; i >= 1; i--) {
      const from = `${file}.${i}`;
      const to = `${file}.${i + 1}`;
      if (existsSync(from)) {
        try {
          unlinkSync(to);
        } catch {
          /* ignore */
        }
        try {
          renameSync(from, to);
        } catch {
          /* ignore */
        }
      }
    }
    if (existsSync(`${file}.1`)) {
      try {
        unlinkSync(`${file}.1`);
      } catch {
        /* ignore */
      }
    }
    renameSync(file, `${file}.1`);
  } catch {
    /* ignore */
  }
}

function stamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

let logFile = '';

/** 初始化日志：文件（DEBUG 全量）+ 控制台。重复调用安全。 */
export function setupLogging(verbose = false): void {
  if (logReady) return;
  ensureDirs();
  consoleLevel = verbose || process.env.WHM_VERBOSE === '1' ? 'DEBUG' : 'INFO';
  logFile = join(LOG_DIR, 'app.log');
  logReady = true;
}

function writeLog(level: Level, name: string, msg: string): void {
  const line = `${stamp()} [${level.padEnd(7)}] ${name}: ${msg}`;
  if (logReady && logFile) {
    try {
      rotate(logFile);
      appendFileSync(logFile, line + '\n');
    } catch {
      /* ignore */
    }
  }
  if (LEVEL_ORDER[level] >= LEVEL_ORDER[consoleLevel]) {
    const out = level === 'ERROR' || level === 'WARN' ? process.stderr : process.stdout;
    out.write(line + '\n');
  }
}

export interface Logger {
  debug(msg: string): void;
  info(msg: string): void;
  warn(msg: string): void;
  error(msg: string): void;
  exception(msg: string, exc?: unknown): void;
}

export function getLogger(name: string): Logger {
  const fmt = (e: unknown): string =>
    e instanceof Error ? `${e.message}${e.stack ? '\n' + e.stack : ''}` : String(e);
  return {
    debug: (m) => writeLog('DEBUG', name, m),
    info: (m) => writeLog('INFO', name, m),
    warn: (m) => writeLog('WARN', name, m),
    error: (m) => writeLog('ERROR', name, m),
    exception: (m, exc) => writeLog('ERROR', name, `${m}: ${fmt(exc ?? new Error('exception'))}`),
  };
}
