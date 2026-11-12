/**
 * Cross-platform bootstrap for the Rosezm CLI's native esbuild dependency.
 *
 * 开源用户走 npm 包（rosezm@0.2.2）：esbuild 由 npm 按当前平台安装好，
 * 这里不需要做任何事 —— 直接跳过。
 *
 * 本地开发者用 ROSEZM_FRAMEWORK 指向自己的框架 checkout 时，checkout 里的
 * node_modules/esbuild 可能是别的平台的二进制（macOS checkout 拷到 Windows
 * 之类）。CLI 从框架自己的目录静态 import "esbuild"，所以修复必须发生在
 * 框架树里：按当前平台重装 esbuild，不动框架的源码与 dist-cli。
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const frameworkCandidates = [
  process.env.ROSEZM_FRAMEWORK,
  resolve(root, '../../work/Rosezm/Rosezm'),
  resolve(root, '../Rosezm/Rosezm'),
].filter(Boolean);
const framework = frameworkCandidates.find(
  (p) => existsSync(join(p, 'dist-cli', 'rosezm.mjs')));

if (!framework) {
  // 没有本地 checkout = 用的是 npm 包，esbuild 已由 npm 按平台装好。
  console.log('[ensure-esbuild] using the npm rosezm package; nothing to do.');
  process.exit(0);
}

const pkgDir = join(framework, 'node_modules', 'esbuild');
if (!existsSync(pkgDir)) {
  console.error(`[ensure-esbuild] esbuild is missing at ${pkgDir}`);
  process.exit(1);
}

/** esbuild's native package for this Node process. */
function expectedPackage() {
  const map = {
    'win32-x64': '@esbuild/win32-x64',
    'win32-ia32': '@esbuild/win32-ia32',
    'win32-arm64': '@esbuild/win32-arm64',
    'darwin-arm64': '@esbuild/darwin-arm64',
    'darwin-x64': '@esbuild/darwin-x64',
    'linux-x64': '@esbuild/linux-x64',
    'linux-arm64': '@esbuild/linux-arm64',
  };
  return map[`${process.platform}-${process.arch}`] ?? null;
}

function installedPlatformPackages() {
  const atDir = join(framework, 'node_modules', '@esbuild');
  if (!existsSync(atDir)) return [];
  return readdirSync(atDir).filter((n) => n.startsWith('win32') || n.startsWith('darwin') || n.startsWith('linux'));
}

function esbuildVersion() {
  try {
    return JSON.parse(readFileSync(join(pkgDir, 'package.json'), 'utf-8')).version;
  } catch {
    return null;
  }
}

const need = expectedPackage();
const have = installedPlatformPackages().map((n) => '@esbuild/' + n);
if (need && have.includes(need)) {
  // Also verify the binary itself is present (a stale directory can survive a copy).
  const bin = join(framework, 'node_modules', need, process.platform === 'win32' ? 'esbuild.exe' : 'bin/esbuild');
  if (existsSync(bin)) {
    console.log(`[ensure-esbuild] ${need} already installed (${esbuildVersion() ?? '?'}) - no action.`);
    process.exit(0);
  }
}

console.log(`[ensure-esbuild] platform ${process.platform}-${process.arch} needs ${need}; found: ${have.join(', ') || '(none)'}.`);
console.log(`[ensure-esbuild] reinstalling esbuild inside ${framework} for this platform...`);

const version = esbuildVersion();
const spec = version ? `esbuild@${version}` : 'esbuild';
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const r = spawnSync(npm, ['install', spec, '--no-save', '--no-audit', '--no-fund'], {
  cwd: framework,
  stdio: 'inherit',
  shell: process.platform === 'win32',
  env: { ...process.env },
});
if (r.status !== 0) {
  console.error('[ensure-esbuild] automatic reinstall failed.');
  console.error(`  Run manually:  cd /d "${framework}"  &&  npm install ${spec} --no-save`);
  console.error('  (needs network access the first time; afterwards the binary is cached by npm.)');
  process.exit(1);
}
const after = installedPlatformPackages().map((n) => '@esbuild/' + n);
if (!need || !after.includes(need)) {
  console.error(`[ensure-esbuild] ${need} still missing after install: ${after.join(', ') || '(none)'}`);
  process.exit(1);
}
console.log(`[ensure-esbuild] OK: ${need} installed (${esbuildVersion() ?? '?'}).`);
