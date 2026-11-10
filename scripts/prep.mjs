/**
 * Project bootstrap (runs before every build/dev/serve/check/selftest).
 *
 * Vendors @types/node into node_modules/@types/node so `tsc --noEmit` and
 * editors resolve Node built-ins without a full `npm install` (the project
 * has only optional native deps: qrcode / cap).
 *
 * NOTE: the old `node_modules/whm-core` facade was removed once the Rosezm
 * compiler learned to resolve local relative TypeScript imports (BN-6);
 * the api routes now import `../../server/index.ts` directly.
 */
import { mkdirSync, cpSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const typesDir = join(root, 'vendor', 'types-node');
const typesDst = join(root, 'node_modules', '@types', 'node');
if (existsSync(typesDir) && !existsSync(typesDst)) {
  mkdirSync(join(root, 'node_modules', '@types'), { recursive: true });
  cpSync(typesDir, typesDst, { recursive: true });
}
