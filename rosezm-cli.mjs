// Rosezm CLI shim: resolves the framework's self-contained dist-cli bundle
// next to this project (the framework checkout this port is built against),
// so the project needs no npm install of the framework itself. esbuild resolves
// from the framework's own node_modules.
//
// 兼容原版 main.py 的命令行：
//   (none)          → rosezm build + serve（默认网页界面）
//   --selftest      → 构建 + 走 dist/server.js 做自检，不开窗口
//   --verbose       → 详细日志（setupLogging(verbose)，控制台 DEBUG）
//   --debug         → WebView 调试的等价物：Rosezm dev 模式（热重载 + 错误浮层）
//   --no-elevate    → 跳过提权提示（本版不主动提权，标记环境变量）
//   --tk / --legacy → 旧 Tk 界面：Rosezm 是 Web 框架无 Tk 容器；功能已并入
//                     Web API（见 README），这里打开同一网页界面并提示。
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
// ROSEZM_CLI 可显式指向框架 dist-cli/rosezm.mjs（换机器/换盘符时不用改代码）。
const override = process.env.ROSEZM_CLI;
const candidates = override ? [resolve(override)] : [
  resolve(here, '../../work/Rosezm/Rosezm/dist-cli/rosezm.mjs'),
  resolve(here, '../Rosezm/Rosezm/dist-cli/rosezm.mjs'),
  resolve(here, 'vendor/rosezm/dist-cli/rosezm.mjs'),
];
const cli = candidates.find((p) => existsSync(p));
if (!cli) {
  // 桌面 exe 从源码 public/ 直接携带界面，Web 预览版 dist/ 是可选产物。
  // 打包流程设 WHM_OPTIONAL_BUILD=1：缺框架/缺 Node 时跳过而不阻断编译。
  if (process.env.WHM_OPTIONAL_BUILD === '1') {
    console.log('  frontend preview skipped (CLI unavailable); desktop UI ships from public/');
    process.exit(0);
  }
  console.error('rosezm CLI not found. Looked in:\n  ' + candidates.join('\n  '));
  console.error('Set ROSEZM_CLI=<path to dist-cli/rosezm.mjs> to override.');
  process.exit(1);
}

const args = process.argv.slice(2);
const flags = args.filter((a) => a.startsWith('--'));
// 允许 `rosezm-cli.mjs build .` / `serve .` 这种带命令名的调用（npm scripts 会用）；
// 命令名本身不重复传给框架 CLI。
const COMMAND_WORDS = new Set(['build', 'dev', 'preview', 'serve', 'check', 'export', 'new']);
const passthrough = args.filter((a) => !a.startsWith('--') && !COMMAND_WORDS.has(a));

// `export` 的子命令与全部 flag（--out/--api/--target 等）必须原样透传给框架 CLI，
// 不能像 build/serve 那样把 flag 剥掉——否则 `export python . --out ... --api ...` 会退化成 build。
const exportIdx = args.indexOf('export');
const exportArgs = exportIdx >= 0 ? args.slice(exportIdx + 1) : [];

const run = (cmd, cmdArgs = []) =>
  spawnSync(process.execPath, [cli, cmd, ...cmdArgs], {
    stdio: 'inherit',
    cwd: process.cwd(),
    env: {
      ...process.env,
      WHM_RUNTIME: /^(dev|preview|serve|export|new)$/.test(cmd) ? '1' : process.env.WHM_RUNTIME ?? '',
      // 桌面工具是单例：一个 worker 一份后端容器（流量引擎、门户、SQLite 连接）。
      rosezm_WORKERS: process.env.rosezm_WORKERS ?? '1',
      WHM_VERBOSE: flags.includes('--verbose') ? '1' : process.env.WHM_VERBOSE ?? '',
      WHM_NO_ELEVATE: flags.includes('--no-elevate') ? '1' : '',
      WHM_TK: flags.includes('--tk') || flags.includes('--legacy') ? '1' : '',
      rosezm_DEV: flags.includes('--debug') ? '1' : process.env.rosezm_DEV ?? '',
    },
  });

const wantsCheck = args.includes('check');
const wantsPreview = args.includes('preview');
const wantsDev = args.includes('dev') || flags.includes('--debug');
const wantsServeOnly = args.includes('serve');
const wantsBuildOnly = args.includes('build');
const wantsExport = exportIdx >= 0;
const noCommand = !wantsCheck && !wantsPreview && !wantsDev && !wantsServeOnly && !wantsBuildOnly && !wantsExport && !flags.includes('--selftest');
let r;
if (wantsCheck) {
  r = run('check', passthrough);
} else if (wantsExport) {
  r = run('export', exportArgs);
} else if (wantsPreview) {
  r = run('build', passthrough);
  if (r.status === 0) r = run('preview', passthrough);
} else if (wantsServeOnly) {
  r = run('serve', passthrough);
} else if (flags.includes('--selftest')) {
  r = run('build', passthrough);
  if (r.status === 0) {
    r = spawnSync(process.execPath, [resolve(here, 'scripts', 'selftest.mjs')], {
      stdio: 'inherit',
      cwd: process.cwd(),
      env: { ...process.env, WHM_RUNTIME: '1', WHM_VERBOSE: flags.includes('--verbose') ? '1' : '' },
    });
  }
} else if (wantsDev) {
  if (flags.includes('--tk') || flags.includes('--legacy')) {
  }
  r = run('dev', passthrough);
} else if (wantsBuildOnly) {
  r = run('build', passthrough);
} else if (noCommand) {
  // 原版 `python main.py`（无参数）：构建 + 启动网页界面
  if (flags.includes('--tk') || flags.includes('--legacy')) {
  }
  r = run('build', passthrough);
  if (r.status === 0) r = run('serve', passthrough);
} else {
  r = run('build', passthrough);
  if (r.status === 0) r = run('serve', passthrough);
}
process.exit(r?.status ?? 1);
