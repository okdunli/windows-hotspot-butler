/**
 * MAC 厂商识别 + 设备类型智能推断（对应原项目 hotspot/core/oui.py）。
 * 内置常用 OUI，可外挂扩展文件 %APPDATA%\WifiHotspotManager\oui_extra.csv：
 *   OUI前缀(6位hex),厂商名,设备类型
 * 例如：A4C138,Telink,iot
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { DATA_DIR, getLogger } from './paths.ts';

const log = getLogger('oui');

/** 设备类型 -> 中文名（同时也是预设图标的 key） */
export const DEVICE_TYPES: Record<string, string> = {
  phone: '手机',
  tablet: '平板',
  laptop: '笔记本',
  desktop: '台式机',
  tv: '电视 / 盒子',
  console: '游戏机',
  watch: '手表 / 手环',
  speaker: '音箱',
  camera: '摄像头',
  printer: '打印机',
  router: '路由 / AP',
  nas: 'NAS / 服务器',
  iot: '智能家居',
  car: '车机',
  unknown: '未知设备',
};

/** 内置 OUI 表：前缀(6 hex, 大写) -> [厂商, 设备类型] */
export const OUI_DB: Record<string, [string, string]> = {
  "0021E9": ["Apple", "phone"],
  "3C0754": ["Apple", "phone"],
  "6C4008": ["Apple", "phone"],
  "A85C2C": ["Apple", "phone"],
  "F0DBF8": ["Apple", "phone"],
  "D02598": ["Apple", "phone"],
  "AC3C0B": ["Apple", "laptop"],
  "F81EDF": ["Apple", "phone"],
  "8863DF": ["Apple", "phone"],
  "9803D8": ["Apple", "phone"],
  "DC2B2A": ["Apple", "phone"],
  "E0B52D": ["Apple", "phone"],
  "6CE85C": ["Apple", "phone"],
  "C4B301": ["Apple", "laptop"],
  "000A27": ["Apple", "phone"],
  "001124": ["Apple", "phone"],
  "001451": ["Apple", "laptop"],
  "0016CB": ["Apple", "phone"],
  "0017F2": ["Apple", "phone"],
  "0019E3": ["Apple", "phone"],
  "001CB3": ["Apple", "laptop"],
  "002312": ["Apple", "phone"],
  "0025BC": ["Apple", "phone"],
  "002608": ["Apple", "phone"],
  "0026BB": ["Apple", "phone"],
  "041552": ["Apple", "phone"],
  "041E64": ["Apple", "phone"],
  "042665": ["Apple", "phone"],
  "0453ED": ["Apple", "phone"],
  "045453": ["Apple", "phone"],
  "04C5A1": ["Apple", "phone"],
  "04D9F5": ["Apple", "phone"],
  "086D41": ["Apple", "phone"],
  "0C531A": ["Apple", "phone"],
  "109ADD": ["Apple", "laptop"],
  "10DDB1": ["Apple", "laptop"],
  "14109F": ["Apple", "phone"],
  "1499E2": ["Apple", "phone"],
  "183451": ["Apple", "phone"],
  "1C0B29": ["Apple", "phone"],
  "20EE28": ["Apple", "phone"],
  "24AB81": ["Apple", "phone"],
  "28CFE9": ["Apple", "phone"],
  "3010B3": ["Apple", "phone"],
  "34363B": ["Apple", "phone"],
  "3830F9": ["Apple", "phone"],
  "3C22FB": ["Apple", "phone"],
  "40CBC0": ["Apple", "phone"],
  "440010": ["Apple", "phone"],
  "4860BC": ["Apple", "phone"],
  "4C3275": ["Apple", "phone"],
  "50ED3C": ["Apple", "phone"],
  "54724F": ["Apple", "phone"],
  "5855CA": ["Apple", "phone"],
  "68DBCA": ["Apple", "phone"],
  "7073CB": ["Apple", "phone"],
  "745BC4": ["Apple", "phone"],
  "787F70": ["Apple", "phone"],
  "7C04D0": ["Apple", "phone"],
  "7C6193": ["Apple", "phone"],
  "7CC3A1": ["Apple", "phone"],
  "843838": ["Apple", "phone"],
  "881FA1": ["Apple", "phone"],
  "8C8590": ["Apple", "phone"],
  "90840D": ["Apple", "phone"],
  "989E63": ["Apple", "phone"],
  "9C3E53": ["Apple", "phone"],
  "9CAD97": ["Apple", "phone"],
  "A0999B": ["Apple", "phone"],
  "A45E60": ["Apple", "phone"],
  "A483E7": ["Apple", "phone"],
  "A82066": ["Apple", "phone"],
  "A8BC32": ["Apple", "laptop"],
  "B019C6": ["Apple", "phone"],
  "B80997": ["Apple", "phone"],
  "B8F6B1": ["Apple", "phone"],
  "BC3BAF": ["Apple", "phone"],
  "BCE195": ["Apple", "phone"],
  "C09F42": ["Apple", "phone"],
  "C869CD": ["Apple", "phone"],
  "CCB088": ["Apple", "phone"],
  "D0034B": ["Apple", "phone"],
  "D023DB": ["Apple", "phone"],
  "D4619D": ["Apple", "phone"],
  "D89695": ["Apple", "phone"],
  "E0ACCB": ["Apple", "phone"],
  "E4C767": ["Apple", "phone"],
  "EC2C4F": ["Apple", "phone"],
  "EC55F9": ["Apple", "phone"],
  "F01898": ["Apple", "phone"],
  "F44EFD": ["Apple", "phone"],
  "F4F15A": ["Apple", "phone"],
  "286C07": ["Xiaomi", "phone"],
  "3480B3": ["Xiaomi", "phone"],
  "64B473": ["Xiaomi", "phone"],
  "78022E": ["Xiaomi", "phone"],
  "8CBEBE": ["Xiaomi", "phone"],
  "9C99A0": ["Xiaomi", "phone"],
  "F8A45F": ["Xiaomi", "phone"],
  "F0B429": ["Xiaomi", "phone"],
  "04CF8C": ["Xiaomi", "phone"],
  "7C49EB": ["Xiaomi", "phone"],
  "AC0BFB": ["Espressif/Xiaomi", "iot"],
  "00E0FC": ["Huawei", "phone"],
  "1C1D67": ["Huawei", "phone"],
  "24DBAC": ["Huawei", "phone"],
  "48DB50": ["Huawei", "router"],
  "5CF96A": ["Huawei", "phone"],
  "70723C": ["Huawei", "phone"],
  "88E3AB": ["Huawei", "phone"],
  "AC853D": ["Huawei", "phone"],
  "D0FF98": ["Huawei", "phone"],
  "E0247F": ["Huawei", "router"],
  "F4CB52": ["Honor", "phone"],
  "0C96BF": ["Honor", "phone"],
  "0021D1": ["Samsung", "phone"],
  "1C5A3E": ["Samsung", "phone"],
  "2C0E3D": ["Samsung", "phone"],
  "34BE00": ["Samsung", "phone"],
  "5001BB": ["Samsung", "phone"],
  "8425DB": ["Samsung", "phone"],
  "B47443": ["Samsung", "tv"],
  "C81EE7": ["Samsung", "phone"],
  "F409D8": ["Samsung", "phone"],
  "E8508B": ["Samsung", "phone"],
  "1CA770": ["OPPO", "phone"],
  "3CCD36": ["OPPO", "phone"],
  "94D9B3": ["OPPO", "phone"],
  "D0C5D3": ["MediaTek", "phone"],
  "0C1DAF": ["vivo", "phone"],
  "48A9D2": ["vivo", "phone"],
  "8C1ABF": ["vivo", "phone"],
  "94E1AC": ["OnePlus", "phone"],
  "C0EEFB": ["OnePlus", "phone"],
  "64A2F9": ["OnePlus", "phone"],
  "5C51A9": ["realme", "phone"],
  "00218C": ["Lenovo", "laptop"],
  "6045BD": ["Microsoft", "laptop"],
  "50EC26": ["Lenovo", "laptop"],
  "C85B76": ["Lenovo", "laptop"],
  "0022B0": ["Dell", "desktop"],
  "18DBF2": ["Dell", "desktop"],
  "F8BC12": ["Dell", "desktop"],
  "B499BA": ["HP", "laptop"],
  "3822D6": ["HP", "printer"],
  "9C8E99": ["HP", "printer"],
  "3897D6": ["ASUS", "laptop"],
  "AC220B": ["Broadcom", "laptop"],
  "1C872C": ["ASUS", "laptop"],
  "7C10C9": ["ASUS", "router"],
  "5CBA37": ["Microsoft", "laptop"],
  "281878": ["Microsoft", "console"],
  "001A73": ["Intel", "laptop"],
  "3C970E": ["Intel", "laptop"],
  "8C554A": ["Intel", "laptop"],
  "A0A8CD": ["Intel", "laptop"],
  "D0577B": ["Intel", "laptop"],
  "F8B95A": ["Intel", "laptop"],
  "0016EA": ["Intel", "laptop"],
  "001B21": ["Intel", "laptop"],
  "001CBF": ["Intel", "laptop"],
  "001D92": ["Intel", "laptop"],
  "00215C": ["Intel", "laptop"],
  "00248C": ["Intel", "laptop"],
  "0026C6": ["Intel", "laptop"],
  "0026C7": ["Intel", "laptop"],
  "00A0C6": ["Intel", "laptop"],
  "0C8BFD": ["Intel", "laptop"],
  "181DEA": ["Intel", "laptop"],
  "247703": ["Intel", "laptop"],
  "28C2DD": ["Intel", "laptop"],
  "302086": ["Intel", "laptop"],
  "3C46D8": ["Intel", "laptop"],
  "3C5282": ["Intel", "laptop"],
  "3C5AB4": ["Intel", "laptop"],
  "6837E9": ["Intel", "laptop"],
  "C8CBB8": ["Intel", "laptop"],
  "0019A0": ["Broadcom", "phone"],
  "0019E0": ["Broadcom", "phone"],
  "002196": ["Broadcom", "phone"],
  "00300F": ["Broadcom", "laptop"],
  "08EDB9": ["Broadcom", "phone"],
  "18AF61": ["Broadcom", "phone"],
  "24701C": ["Broadcom", "phone"],
  "3C15C2": ["Broadcom", "phone"],
  "448771": ["Broadcom", "phone"],
  "68A0F6": ["Broadcom", "phone"],
  "78A0DF": ["Broadcom", "phone"],
  "001374": ["Qualcomm/Atheros", "laptop"],
  "001CF0": ["Qualcomm/Atheros", "laptop"],
  "002216": ["Qualcomm/Atheros", "laptop"],
  "00904C": ["Qualcomm/Atheros", "laptop"],
  "24695E": ["Qualcomm/Atheros", "phone"],
  "44AD8E": ["Qualcomm/Atheros", "phone"],
  "5CF661": ["Qualcomm/Atheros", "phone"],
  "8C7B9D": ["Qualcomm/Atheros", "phone"],
  "A4E4B8": ["Qualcomm/Atheros", "phone"],
  "CC6E25": ["Qualcomm/Atheros", "phone"],
  "000E35": ["MediaTek", "phone"],
  "00E04C": ["Realtek", "desktop"],
  "08001F": ["MediaTek", "phone"],
  "18A6F7": ["MediaTek", "phone"],
  "24C939": ["MediaTek", "phone"],
  "34C375": ["MediaTek", "phone"],
  "44F7AD": ["MediaTek", "phone"],
  "4CDBEE": ["MediaTek", "phone"],
  "68C900": ["MediaTek", "tv"],
  "74B511": ["MediaTek", "phone"],
  "7C2EBD": ["MediaTek", "phone"],
  "8CAE4C": ["MediaTek", "phone"],
  "A0F3C1": ["MediaTek", "phone"],
  "ECA5B5": ["MediaTek", "phone"],
  "F4876E": ["MediaTek", "phone"],
  "0023CD": ["Realtek", "desktop"],
  "525400": ["QEMU 虚拟机", "desktop"],
  "000C29": ["VMware", "desktop"],
  "0050F2": ["Microsoft 虚拟", "desktop"],
  "0003FF": ["Hyper-V", "desktop"],
  "080027": ["VirtualBox", "desktop"],
  "8CDE52": ["Hisense", "tv"],
  "9CB6D0": ["TCL", "tv"],
  "24FD5B": ["小米电视", "tv"],
  "F8AB05": ["LG", "tv"],
  "1CC1DE": ["Sony", "tv"],
  "54271E": ["Sony", "tv"],
  "F4F5D8": ["Google Chromecast", "tv"],
  "6CAD3F": ["Google", "tv"],
  "F09FC2": ["Amazon Fire", "tv"],
  "F0272D": ["Amazon Echo", "speaker"],
  "44650D": ["Amazon", "speaker"],
  "68DBF5": ["Amazon", "speaker"],
  "0019C5": ["Sony PlayStation", "console"],
  "0CFE45": ["Sony PlayStation", "console"],
  "98B6E9": ["Nintendo", "console"],
  "7CBB8A": ["Nintendo", "console"],
  "0017AB": ["Nintendo", "console"],
  "60455E": ["Xbox", "console"],
  "0000AA": ["Xerox", "printer"],
  "3C2AF4": ["Brother", "printer"],
  "002673": ["Brother", "printer"],
  "001BA9": ["Brother", "printer"],
  "00265E": ["Canon", "printer"],
  "1831BF": ["Canon", "printer"],
  "0026AB": ["Seiko Epson", "printer"],
  "A4CB84": ["Epson", "printer"],
  "8CCE4E": ["Ai-Thinker", "iot"],
  "D4A651": ["Tuya 涂鸦", "iot"],
  "68572D": ["Tuya 涂鸦", "iot"],
  "38010F": ["Broadlink", "iot"],
  "B4430D": ["Broadlink", "iot"],
  "7C874C": ["Tuya 涂鸦", "iot"],
  "ECFABC": ["Tuya 涂鸦", "iot"],
  "B0F893": ["Aqara / 绿米", "iot"],
  "04CF4B": ["Aqara / 绿米", "iot"],
  "001D0F": ["TP-Link", "router"],
  "14CC20": ["TP-Link", "router"],
  "50C7BF": ["TP-Link", "router"],
  "A42BB0": ["TP-Link", "router"],
  "B0BE76": ["TP-Link", "router"],
  "F4F26D": ["TP-Link", "router"],
  "002401": ["D-Link", "router"],
  "1CBDB9": ["D-Link", "router"],
  "0018E7": ["Netgear", "router"],
  "A040A0": ["Netgear", "router"],
  "802AA8": ["Ubiquiti", "router"],
  "FCECDA": ["Ubiquiti", "router"],
  "6C5AB0": ["TCL / 移动光猫", "router"],
  "F8E71E": ["Ruijie 锐捷", "router"],
  "5869F0": ["H3C 新华三", "router"],
  "70F96D": ["ZTE 中兴", "router"],
  "B827EB": ["Raspberry Pi", "nas"],
  "DCA632": ["Raspberry Pi", "nas"],
  "E45F01": ["Raspberry Pi", "nas"],
  "D83ADD": ["Raspberry Pi", "nas"],
  "001132": ["Synology", "nas"],
  "0011D8": ["ASUSTOR", "nas"],
  "245EBE": ["QNAP", "nas"],
  "00089B": ["ICP / NAS", "nas"],
  "4491DB": ["Hikvision 海康", "camera"],
  "BCAD28": ["Hikvision 海康", "camera"],
  "C0561D": ["Hikvision 海康", "camera"],
  "3C1E04": ["D-Link Camera", "camera"],
  "E0508B": ["Dahua 大华", "camera"],
  "9C14C3": ["Dahua 大华", "camera"],
  "6C5C14": ["EZVIZ 萤石", "camera"],
  "F8E43B": ["Tesla", "car"],
  "4C7425": ["Tesla", "car"],
};

/** 主机名关键字 -> 设备类型 */
export const HOSTNAME_RULES: [string, string][] = [
  [String.raw`iphone|redmi|mi\-?phone|honor|huawei\-?p|oppo|vivo|oneplus|pixel|galaxy|nova|mate`, 'phone'],
  [String.raw`ipad|tab(let)?\b|mipad`, 'tablet'],
  [String.raw`macbook|laptop|thinkpad|ideapad|matebook|notebook|xps|vivobook|zenbook|magicbook`, 'laptop'],
  [String.raw`desktop|imac|pc\b|win\-?pc|workstation|optiplex`, 'desktop'],
  [String.raw`\btv\b|smarttv|bravia|aquos|chromecast|firetv|appletv|mibox|box\b|roku`, 'tv'],
  [String.raw`switch|playstation|ps[45]|xbox|nintendo`, 'console'],
  [String.raw`watch|band|amazfit|gt[0-9]`, 'watch'],
  [String.raw`echo|homepod|speaker|sonos|soundbar`, 'speaker'],
  [String.raw`cam(era)?|ipc\-|hikvision|dahua|ezviz`, 'camera'],
  [String.raw`printer|hp[0-9a-f]{6}|epson|canon|brother`, 'printer'],
  [String.raw`nas|synology|qnap|raspberrypi|rpi|server|ubuntu|debian`, 'nas'],
  [String.raw`esp\-|esp32|esp8266|tasmota|shelly|sonoff|lumi|plug|bulb|light|sensor`, 'iot'],
  [String.raw`tesla|model[3sxy]|byd|nio`, 'car'],
  [String.raw`router|ap\-|repeater|mesh`, 'router'],
];

let extraLoaded = false;

function loadExtra(): void {
  if (extraLoaded) return;
  extraLoaded = true;
  const path = join(DATA_DIR, 'oui_extra.csv');
  let text = '';
  try {
    text = readFileSync(path, 'utf-8');
  } catch {
    return;
  }
  try {
    let count = 0;
    for (const line of text.split(/\r?\n/)) {
      const row = line.split(',');
      if (row.length < 2 || row[0].trim().startsWith('#')) continue;
      const prefix = row[0].replace(/[^0-9A-Fa-f]/g, '').slice(0, 6).toUpperCase();
      if (prefix.length !== 6) continue;
      const vendor = row[1].trim();
      const dtype = row.length > 2 ? row[2].trim() : 'unknown';
      OUI_DB[prefix] = [vendor, dtype in DEVICE_TYPES ? dtype : 'unknown'];
      count++;
    }
    if (count) log.info(`已加载自定义 OUI 扩展：${path}（${count} 条）`);
  } catch (exc) {
    log.warn(`OUI 扩展读取失败：${String(exc)}`);
  }
}

function prefixOf(mac: string): string {
  return (mac || '').replace(/[^0-9A-Fa-f]/g, '').slice(0, 6).toUpperCase();
}

/** 判断是否为随机化 MAC（手机隐私地址，第二个 hex 为 2/6/A/E）。 */
export function isRandomMac(mac: string): boolean {
  const hexs = (mac || '').replace(/[^0-9A-Fa-f]/g, '');
  if (hexs.length < 2) return false;
  const v = parseInt(hexs[1], 16);
  return Number.isNaN(v) ? false : (v & 0x02) !== 0;
}

export function lookupVendor(mac: string): string {
  loadExtra();
  const hit = OUI_DB[prefixOf(mac)];
  if (hit) return hit[0];
  return isRandomMac(mac) ? '随机化 MAC 设备' : '';
}

/** 综合 OUI + 主机名推断设备类型。 */
export function guessType(mac: string, hostname = '', vendor = ''): string {
  loadExtra();
  const name = (hostname || '').toLowerCase();
  if (name) {
    for (const [pattern, dtype] of HOSTNAME_RULES) {
      try {
        if (new RegExp(pattern, 'i').test(name)) return dtype;
      } catch {
        /* 忽略坏正则（与原版一致：原版 search 抛错会向上冒，此处保守跳过） */
      }
    }
  }
  const hit = OUI_DB[prefixOf(mac)];
  if (hit && hit[1] in DEVICE_TYPES) return hit[1];
  const ven = (vendor || (hit ? hit[0] : '')).toLowerCase();
  if (ven) {
    for (const [key, dtype] of [
      ['apple', 'phone'], ['xiaomi', 'phone'], ['huawei', 'phone'],
      ['samsung', 'phone'], ['espressif', 'iot'], ['tuya', 'iot'],
      ['raspberry', 'nas'], ['tp-link', 'router'], ['intel', 'laptop'],
    ] as [string, string][]) {
      if (ven.includes(key)) return dtype;
    }
  }
  // 随机化 MAC 基本都是现代手机
  if (isRandomMac(mac)) return 'phone';
  return 'unknown';
}

/** 返回 [厂商, 设备类型]。 */
export function identify(mac: string, hostname = ''): [string, string] {
  const vendor = lookupVendor(mac);
  return [vendor, guessType(mac, hostname, vendor)];
}

export function typeLabel(dtype: string | null | undefined): string {
  return DEVICE_TYPES[dtype || 'unknown'] ?? '未知设备';
}
