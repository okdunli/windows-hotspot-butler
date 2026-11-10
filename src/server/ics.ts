/**
 * 互联网连接共享(ICS)控制（对应原项目 hotspot/core/ics.py）。
 * netsh hostednetwork 只是建了一个 AP，并不会把上网连接共享给连进来的设备——
 * 需要用 HNetCfg.HNetShare COM 对象启用 ICS。需管理员权限，best-effort。
 */
import { findHotspotAdapter } from './netinfo.ts';
import { psJson, psScript } from './pshell.ts';
import { ICS_PS1, getLogger } from './paths.ts';

const log = getLogger('ics');

/** 返回当前用于上网的网卡名称（Get-NetConnectionProfile IPv4Connectivity=Internet）。 */
export async function internetAdapterName(): Promise<string | null> {
  const data = (await psJson(
    "$p = Get-NetConnectionProfile -ErrorAction SilentlyContinue | " +
      "Where-Object {$_.IPv4Connectivity -eq 'Internet' -or $_.IPv6Connectivity -eq 'Internet'};" +
      'if ($p) { ($p | Select-Object -First 1).InterfaceAlias } else { $null }',
    20,
  )) as unknown;
  if (typeof data === 'string' && data.trim()) return data.trim();
  return null;
}

/** 把上网连接(public)共享给热点网卡(private)。返回 [ok, 消息]。 */
export async function enableIcs(
  privateName?: string | null,
  publicName?: string | null,
): Promise<[boolean, string]> {
  const pub = publicName || (await internetAdapterName());
  if (!pub) return [false, '找不到可共享的上网连接（请先连上有线/无线网）'];
  if (!privateName) {
    try {
      const ap = await findHotspotAdapter();
      privateName = ap ? ap.name : null;
    } catch {
      privateName = null;
    }
  }
  if (!privateName) return [false, '未找到热点网卡（请先开启热点再启用共享）'];
  const data = await psScript(ICS_PS1, { Action: 'Enable', Public: pub, Private: privateName }, 45);
  if (data && data.ok) {
    return [true, String(data.message || `已共享 ${pub} -> ${privateName}`)];
  }
  const err = (data && data.error) || 'ICS 启用失败';
  return [false, String(err)];
}

/** 关闭热点网卡的共享。返回 [ok, 消息]。 */
export async function disableIcs(privateName?: string | null): Promise<[boolean, string]> {
  if (!privateName) {
    try {
      const ap = await findHotspotAdapter();
      privateName = ap ? ap.name : null;
    } catch {
      privateName = null;
    }
  }
  if (!privateName) return [false, '未找到热点网卡'];
  const data = await psScript(ICS_PS1, { Action: 'Disable', Private: privateName }, 45);
  if (data && data.ok) {
    return [true, String(data.message || '已关闭共享')];
  }
  const err = (data && data.error) || 'ICS 关闭失败';
  return [false, String(err)];
}

// 保留原始命名别名（与 Python 原版函数名对齐）
export const enable = enableIcs;
export const disable = disableIcs;
