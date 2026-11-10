/**
 * 端口转发（游戏模式）（对应原项目 hotspot/core/portforward.py）：
 * netsh interface portproxy 的封装。规则持久化在 netsh（系统级），本地也存一份用于列表。
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { run } from './pshell.ts';
import { DATA_DIR, ensureDirs, getLogger } from './paths.ts';

const log = getLogger('portforward');

const RULES_FILE = join(DATA_DIR, 'portproxy.json');

export interface PfRule {
  listen_port: number;
  connect_ip: string;
  connect_port: number;
  proto: string;
}

function ruleName(name: string): string {
  return `WHM-PORTPROXY-${name}`;
}

export class PortForwarder {
  rules: Record<string, PfRule> = {};

  constructor() {
    this.load();
  }

  load(): void {
    try {
      if (existsSync(RULES_FILE)) {
        const data = JSON.parse(readFileSync(RULES_FILE, 'utf-8')) as Record<string, PfRule>;
        if (data && typeof data === 'object') this.rules = data;
      }
    } catch {
      log.debug('端口转发规则读取失败');
    }
  }

  save(): void {
    try {
      ensureDirs();
      writeFileSync(RULES_FILE, JSON.stringify(this.rules, null, 2), 'utf-8');
    } catch {
      log.warn('端口转发规则保存失败');
    }
  }

  async add(
    name: string,
    listenPort: number,
    connectIp: string,
    connectPort: number,
    proto = 'tcp',
  ): Promise<[boolean, string]> {
    name = (name || '').trim() || `rule-${listenPort}`;
    proto = proto.toLowerCase() === 'udp' ? 'udp' : 'tcp';
    listenPort = Number(listenPort);
    connectPort = Number(connectPort);
    if (!Number.isFinite(listenPort) || !Number.isFinite(connectPort)) {
      return [false, '端口必须是数字'];
    }
    if (!(1 <= listenPort && listenPort <= 65535 && 1 <= connectPort && connectPort <= 65535)) {
      return [false, '端口范围 1-65535'];
    }
    if (proto === 'udp') {
      return [false, 'Windows portproxy 仅支持 TCP，UDP 请使用防火墙放行'];
    }
    const args = [
      'netsh', 'interface', 'portproxy', 'add', 'v4tov4',
      `listenport=${listenPort}`, 'listenaddress=0.0.0.0',
      `connectport=${connectPort}`, `connectaddress=${connectIp}`,
    ];
    const { code, stdout, stderr } = await run(args, 20);
    if (code !== 0) {
      return [false, (stdout || stderr).trim().slice(0, 200) || '添加失败（需要管理员权限）'];
    }
    // 顺手放行防火墙，避免规则加了却不通
    await run(
      [
        'netsh', 'advfirewall', 'firewall', 'add', 'rule',
        `name=${ruleName(name)}`, 'action=allow', 'enable=yes',
        `localport=${listenPort}`, 'protocol=tcp', 'dir=in',
      ],
      20,
    );
    this.rules[name] = { listen_port: listenPort, connect_ip: connectIp, connect_port: connectPort, proto };
    this.save();
    return [true, `已添加转发：${listenPort} → ${connectIp}:${connectPort}`];
  }

  async remove(name: string): Promise<[boolean, string]> {
    const rule = this.rules[name];
    if (!rule) return [false, '规则不存在'];
    delete this.rules[name];
    const { code, stdout, stderr } = await run(
      [
        'netsh', 'interface', 'portproxy', 'delete', 'v4tov4',
        `listenport=${rule.listen_port}`, 'listenaddress=0.0.0.0',
      ],
      20,
    );
    await run(['netsh', 'advfirewall', 'firewall', 'delete', 'rule', `name=${ruleName(name)}`], 20);
    this.save();
    if (code !== 0) {
      const msg = (stdout || stderr).trim().slice(0, 200);
      if (!msg.toLowerCase().includes('not found') && !msg.includes('找不到')) {
        return [false, msg || '删除失败（需要管理员权限）'];
      }
    }
    return [true, `已删除转发规则 ${name}`];
  }

  listRules(): (PfRule & { name: string })[] {
    return Object.entries(this.rules).map(([name, v]) => ({ ...v, name }));
  }

  async clearAll(): Promise<[boolean, string]> {
    const names = Object.keys(this.rules);
    for (const n of names) await this.remove(n);
    return [true, `已清空 ${names.length} 条规则`];
  }
}
