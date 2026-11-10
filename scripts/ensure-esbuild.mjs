/**
 * Cross-platform bootstrap for the Rosezm CLI's native esbuild dependency.
 *
 * The framework checkout's node_modules/esbuild may contain a binary for a
 * different OS (e.g. a macOS checkout copied to Windows: @esbuild/darwin-arm64
 * present but @esbuild/win32-x64 needed). The framework CLI statically imports
 * "esbuild" from its own tree, so the fix must happen THERE, not in this
 * project. We reinstall esbuild inside the framework tree for the current
 * platform (npm writes the matching @esbuild/<platform> package), leaving the
 * framework's source and dist-cli untouched.
 *
 * No-op on the machine that built the framework (already matches).
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const frameworkCandidates = [
  resolve(root, '../../work/Rosezm/Rosezm'),
  resolve(root, '../Rosezm/Rosezm'),
  resolve(root, 'vendor/rosezm'),
];
const framework = frameworkCandidates.find((p) => existsSync(join(p, 'dist-cli', 'rosezm.mjs')));

if (!framework) {
  console.error('[ensure-esbuild] Rosezm framework not found; looked in:');
  for (const p of frameworkCandidates) console.error('  ' + p);
  process.exit(1);
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
