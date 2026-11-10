/**
 * 自检（对应原版 `python main.py --selftest`）：验证各核心模块能否正常工作，不弹窗。
 *
 * 走 Rosezm 自己的管线：直接调用构建产物 dist/server.js 的 handleApi()
 * （与 `rosezm serve` 的 api 路由完全同一份代码），不 import 未打包的 TS 源码
 * ——Node 26 不允许对 node_modules 下的 .ts 剥类型（见框架 BUG.md BN-5）。
 */
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(fileURLToPath(new URL('.', import.meta.url)), '..');
const dist = join(root, 'dist', 'server.js');
if (!existsSync(dist)) {
  console.error('缺少构建产物 dist/server.js —— 请先运行 npm run build');
  process.exit(2);
}

let ok = true;
const check = async (name, fn) => {
  try {
    const r = await fn();
    console.log(`  [OK]   ${name}: ${typeof r === 'object' ? JSON.stringify(r) : r}`);
    return r;
  } catch (exc) {
    ok = false;
    console.log(`  [FAIL] ${name}: ${String(exc)}`);
    return null;
  }
};

console.log('='.repeat(62));
console.log('Windows Hotspot Butler — 自检');
console.log('='.repeat(62));

process.env.WHM_RUNTIME = '1';
const ssr = await import(dist);
const base = 'http://localhost';
const api = async (name, args = []) => {
  const res = await ssr.handleApi('POST', '/api/op/' + name, new Request(base + '/api/op/' + name, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ args }),
  }));
  return res.json();
};
const state = async () => {
  const res = await ssr.handleApi('GET', '/api/state', new Request(base + '/api/state'));
  return res.json();
};

const st = await state();
await check('配置读写', () => ({ ssid: st.config.ssid, theme: st.config.theme }));
await check('翻译表条数', async () => Object.keys(await api('getI18n')).length);
await check('设备档案数量', () => st.devices.length);
await check('MAC 归一化 + 厂商识别', async () => {
  const r = await api('getState'); // 状态接口本身就是内核全链路冒烟
  if (!r || !r.hotspot) throw new Error('getState 未返回 hotspot');
  return { state: r.hotspot.state_text, backend: r.hotspot.backend_label, ssid: r.hotspot.ssid };
});
await check('存储初始化（统计报表）', async () => {
  const r = await api('getStatsReport');
  return { ok: r.ok, daily: r.daily?.length, top: r.top?.length };
});
await check('热点状态读取', () => ({
  state: st.hotspot.state_text,
  ssid: st.hotspot.ssid,
  clients: st.hotspot.client_count,
}));
await check('实际能力矩阵', () => ({
  driver: st.caps.driver || '(未探测到)',
  radios: st.caps.radios || '(未知)',
  wifi_direct: st.caps.wifi_direct,
  soft_ap: st.caps.soft_ap,
  hosted: st.caps.hosted,
}));
await check('诊断', async () => {
  await api('diagnose');
  const r = await api('getDiagnose');
  return { lines: r.lines?.length ?? 0 };
});
await check('热点网关', () => st.gateway);
await check('门户模板数', () => {
  const { readdirSync } = process.getBuiltinModule('node:fs');
  return readdirSync(join(root, 'portal-templates')).filter((f) => f.endsWith('.html')).length;
});
await check('门户状态', () => st.portal);
await check('文件共享状态', () => st.share);
await check('端口转发规则', () => st.port_fwd.length);
await check('迷你浮窗状态', async () => {
  const res = await ssr.handleApi('GET', '/api/mini', new Request(base + '/api/mini'));
  return res.json();
});
await check('保存配置回环', async () => api('saveConfigPatch', [{ ssid: st.config.ssid }]));

console.log('-'.repeat(62));
console.log('自检结果：' + (ok ? '全部通过' : '存在失败项（见上）'));
console.log('='.repeat(62));
try {
  const hook = globalThis.__whmShutdown;
  if (typeof hook === 'function') await hook();
} catch {
  /* ignore */
}
process.exit(ok ? 0 : 1);
