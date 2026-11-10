/**
 * PowerShell / 命令行调用封装（对应原项目 hotspot/core/pshell.py）。
 * 隐藏黑窗（windowsHide）、统一编码、JSON 解析、管理员检测与提权。
 */
import { spawn, execFile, exec, execSync } from 'node:child_process';
import { getLogger } from './paths.ts';

const log = getLogger('pshell');

export const IS_WIN = process.platform === 'win32';
export const POWERSHELL = IS_WIN ? 'powershell.exe' : 'powershell';

export interface RunResult {
  code: number;
  stdout: string;
  stderr: string;
}

function parseExit(code: number | null): number {
  return code === null ? -1 : code;
}

/** 执行外部命令，返回 { code, stdout, stderr }。 */
export function run(cmd: readonly string[], timeout = 25.0): Promise<RunResult> {
  return new Promise((resolve) => {
    try {
      const child = spawn(cmd[0], cmd.slice(1), {
        windowsHide: true,
        timeout: timeout * 1000,
        env: { ...process.env },
      });
      let stdout = '';
      let stderr = '';
      let settled = false;
      const done = (code: number | null, err?: string) => {
        if (settled) return;
        settled = true;
        resolve({ code: err ? -3 : parseExit(code), stdout, stderr: stderr || err || '' });
      };
      child.stdout?.on('data', (d) => (stdout += d.toString('utf8')));
      child.stderr?.on('data', (d) => (stderr += d.toString('utf8')));
      child.on('error', (e: NodeJS.ErrnoException) => {
        if (e.code === 'ENOENT') done(-2, 'NOT_FOUND');
        else done(-3, String(e));
      });
      child.on('close', (code, signal) => {
        if (signal === 'SIGTERM') {
          log.warn(`命令超时：${cmd.slice(0, 3).join(' ')}`);
          done(-1, 'TIMEOUT');
        } else done(code);
      });
    } catch (exc) {
      resolve({ code: -3, stdout: '', stderr: String(exc) });
    }
  });
}

/** 执行 netsh/arp 等本地化输出命令，自动尝试 GBK 解码。 */
export function runConsole(cmd: readonly string[], timeout = 20.0): Promise<string> {
  return new Promise((resolve) => {
    try {
      const child = spawn(cmd[0], cmd.slice(1), {
        windowsHide: true,
        timeout: timeout * 1000,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      const chunks: Buffer[] = [];
      let settled = false;
      const done = (buf: Buffer) => {
        if (settled) return;
        settled = true;
        const raw = buf;
        for (const enc of ['utf8', 'gbk', 'cp936', 'latin1'] as BufferEncoding[]) {
          try {
            resolve(raw.toString(enc));
            return;
          } catch {
            continue;
          }
        }
        resolve(raw.toString('utf8'));
      };
      child.stdout?.on('data', (d) => chunks.push(d));
      child.stderr?.on('data', (d) => chunks.push(d));
      child.on('error', () => {
        log.debug(`命令失败 ${cmd.join(' ')}`);
        done(Buffer.alloc(0));
      });
      child.on('close', () => done(Buffer.concat(chunks)));
    } catch (exc) {
      log.debug(`命令失败 ${cmd.join(' ')}: ${String(exc)}`);
      resolve('');
    }
  });
}

function extractJson(text: string): unknown {
  const t = (text || '').trim().replace(/^\uFEFF/, '');
  if (!t) return null;
  try {
    return JSON.parse(t);
  } catch {
    /* fall through */
  }
  // 输出里可能混有警告行，尝试截取首个 JSON 片段
  for (const [opener, closer] of [['{', '}'], ['[', ']']] as const) {
    const start = t.indexOf(opener);
    const end = t.lastIndexOf(closer);
    if (start >= 0 && start < end) {
      try {
        return JSON.parse(t.slice(start, end + 1));
      } catch {
        continue;
      }
    }
  }
  log.debug(`JSON 解析失败：${t.slice(0, 200)}`);
  return null;
}

/** 执行一段 PowerShell 并把输出当 JSON 解析。 */
export async function psJson(command: string, timeout = 20.0): Promise<unknown> {
  const full =
    "$ErrorActionPreference='Stop';" +
    'try{[Console]::OutputEncoding=[System.Text.Encoding]::UTF8}catch{};' +
    command;
  const { code, stdout, stderr } = await run(
    [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', full],
    timeout,
  );
  if (code !== 0 && !stdout.trim()) {
    log.debug(`PowerShell 失败(${code})：${(stderr || '').slice(0, 200)}`);
    return null;
  }
  return extractJson(stdout);
}

export interface PsScriptResult extends Record<string, unknown> {
  ok?: boolean;
  error?: string;
  errorType?: string;
  returncode?: number;
}

/** 执行 .ps1 脚本文件并解析 JSON 输出。 */
export async function psScript(
  scriptPath: string,
  params: Record<string, string>,
  timeout = 45.0,
): Promise<PsScriptResult> {
  const args: string[] = [
    POWERSHELL,
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    scriptPath,
  ];
  for (const [key, value] of Object.entries(params)) args.push(`-${key}`, String(value));
  const { code, stdout, stderr } = await run(args, timeout);
  const data = extractJson(stdout);
  if (data && typeof data === 'object' && !Array.isArray(data)) return data as PsScriptResult;
  return {
    ok: false,
    error: (stderr || stdout || 'NO_OUTPUT').trim().slice(0, 300),
    errorType: stderr === 'TIMEOUT' ? 'TIMEOUT' : 'PS_FAILED',
    returncode: code,
  };
}

export function b64(text: string): string {
  return Buffer.from(text || '', 'utf-8').toString('base64');
}

/** 是否管理员（Windows）。 */
export function isAdmin(): boolean {
  if (!IS_WIN) return false;
    try {
      // `net session` 仅管理员可成功；比加载 ctypes 更稳的纯 CLI 做法
      execSync('net session', { stdio: 'ignore', windowsHide: true, timeout: 5000 });
      return true;
    } catch {
      return false;
    }
}

/**
 * 以管理员身份重新启动当前程序（对应原版 ShellExecuteW runas）。
 * 重启的是服务进程（node rosezm-cli serve/dev）。
 * 成功返回 true，调用方应自行退出。
 */
export async function relaunchAsAdmin(): Promise<boolean> {
  if (!IS_WIN) return false;
  try {
    const psi = process.execPath;
    const args = process.argv.slice(1).map((a) => `"${a}"`).join(' ');
    const script =
      `Start-Process -FilePath '${psi.replace(/'/g, "''")}' -ArgumentList '${args.replace(/'/g, "''")}' -Verb RunAs`;
    const { code } = await run(
      [POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
      20,
    );
    return code === 0;
  } catch (exc) {
    log.error(`提权失败：${String(exc)}`);
    return false;
  }
}

export { execFile, exec };
