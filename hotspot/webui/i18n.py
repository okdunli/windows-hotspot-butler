"""多语言支持（中文 / 英文）。

设计（ponytail）：
  * 只有 zh→en 一张字典，键 = 中文原文，值 = 英文译文；zh 模式直接返回原文。
  * t() 精确匹配 + {占位符} 填充；未命中的原文原样返回——新增文案忘补翻译也不会崩。
  * 后端 _toast/_tray_notify 出口统一过 t()，核心层透传的静态消息自动获得翻译。
  * 前端启动时经 get_i18n() 拉整张字典，静态文案用 data-i18n 标记，动态文案用 t()。
"""
from __future__ import annotations

from typing import Dict

ZH2EN: Dict[str, str] = {
    # ---- 通用 ----
    "WiFi 热点管理器": "WiFi Hotspot Manager",
    "管理员": "Admin",
    "非管理员": "Not admin",
    "点击开启": "Tap to start",
    "点击关闭": "Tap to stop",
    "处理中…": "Working…",
    "未运行": "Not running",
    "已开启": "On",
    "运行中": "Running",
    "切换中…": "Switching…",
    "未开启": "Off",
    "上限 {n} 台": "{n} device limit",
    "⏱ {t} 后关闭": "Stops in {t}",
    "名称": "Name",
    "密码": "Password",
    "复制": "Copy",
    "扫码连接": "Scan to connect",
    "显示/隐藏": "Show/Hide",
    "在线": "Online",
    "下行": "Down",
    "上行": "Up",
    "设备": "Devices",
    "更多": "More",
    "已复制：{t}": "Copied: {t}",
    "复制失败：{e}": "Copy failed: {e}",
    "本机未检测到可用的热点能力": "No usable hotspot capability detected",
    "不可用": "Unavailable",

    # ---- 设备面板 ----
    "门户 未启动": "Portal off",
    "门户 · 劫持中": "Portal · DNS",
    "门户 · 仅手动": "Portal · manual",
    "诊断": "Diagnostics",
    "刷新": "Refresh",
    "开启热点后，已连接的设备会显示在这里": "Connected devices will appear here once the hotspot starts",
    "✏️ 重命名": "✏️ Rename",
    "设备类型": "Device type",
    "🚫 禁止上网": "🚫 Block internet",
    "🌐 放行门户": "🌐 Allow via portal",
    "🗑️ 移除设备": "🗑️ Remove device",
    "✅ 恢复上网": "✅ Restore internet",
    "⛔ 取消放行": "⛔ Revoke portal",
    "已禁止": "Blocked",
    "已放行": "Allowed",
    "重命名设备": "Rename device",
    "已重命名": "Renamed",
    "已更新设备类型": "Device type updated",
    "已移除设备": "Device removed",
    "未找到设备": "Device not found",
    "该设备当前不在线，拿不到 IP，无法禁用": "Device offline — no IP, cannot block",
    "已禁止 {ip} 上网": "Blocked {ip}",
    "已恢复上网": "Internet restored",
    "添加防火墙规则失败（需要管理员权限）": "Firewall rule failed (admin required)",
    "设备不在线，无法放行": "Device offline, cannot allow",
    "已放行 {ip}": "Allowed {ip}",
    "已取消放行 {ip}": "Portal access revoked for {ip}",
    "新设备接入：{n}（{ip}）": "New device: {n} ({ip})",

    # ---- 设置弹窗 ----
    "⚙️ 设置": "⚙️ Settings",
    "热点名称": "Hotspot name",
    "密码（至少 8 位）": "Password (min 8 chars)",
    "频段": "Band",
    "自动": "Auto",
    "控制后端": "Backend",
    "自动选择": "Auto select",
    "移动热点 (WinRT)": "Mobile hotspot (WinRT)",
    "承载网络 (netsh)": "Hosted network (netsh)",
    "启动时自动开启热点": "Start hotspot on launch",
    "开机自启": "Run at startup",
    "关闭时最小化到托盘": "Minimize to tray on close",
    "退出时热点开启中 → 提醒确认": "Confirm before exit while hotspot is on",

    # ---- 托盘菜单 ----
    "显示主界面": "Show main window",
    "退出": "Exit",
    "全局热键 Ctrl+Alt+H 开关热点": "Global hotkey Ctrl+Alt+H toggles hotspot",
    "临时密码（到期自动恢复原密码）": "Temp password (auto-restored on expiry)",
    "1 小时": "1 hour",
    "2 小时": "2 hours",
    "4 小时": "4 hours",
    "8 小时": "8 hours",
    "24 小时": "24 hours",
    "生成": "Generate",
    "取消": "Cancel",
    "配置备份": "Config backup",
    "导出配置": "Export",
    "导入配置": "Import",
    "定时关闭（分钟，0=取消）": "Auto stop (minutes, 0=cancel)",
    "不定时": "Off",
    "15 分钟": "15 min",
    "30 分钟": "30 min",
    "应用定时": "Apply",
    "立即下发配置": "Apply now",
    "正在下发配置…": "Applying configuration…",
    "保存": "Save",
    "语言": "Language",
    "跟随系统": "System",
    "设置已保存": "Settings saved",
    "已保存": "Saved",
    "已导出到 {p}": "Exported to {p}",
    "窗口未就绪": "Window not ready",
    "未选择文件": "No file selected",
    "文件格式不正确（不是本程序导出的配置）": "Invalid file (not exported by this app)",
    "配置已导入并下发": "Config imported and applied",
    "配置已导入": "Config imported",
    "JSON 文件 (*.json)": "JSON files (*.json)",

    # ---- 强制门户弹窗 ----
    "🌐 强制门户": "🌐 Captive portal",
    "开启强制门户（连上先看欢迎页，同意后放行）": "Enable captive portal (welcome page before internet)",
    "DNS 劫持（设备自动弹出门户页）": "DNS hijack (devices auto-open the portal)",
    "页面模板": "Template",
    "极光": "Aurora",
    "海风": "Ocean",
    "商务": "Corporate",
    "极简": "Minimal",
    "按钮文字": "Button text",
    "标题": "Title",
    "须知": "Notice",
    "访问密码（空=不需要）": "Access password (empty = none)",
    "放行时段开始": "Allowed from",
    "放行时段结束": "Allowed until",
    "24:00（全天）": "24:00 (all day)",
    "停止": "Stop",
    "启动": "Start",
    "正在启动门户…": "Starting portal…",
    "门户已停止": "Portal stopped",
    "状态：{s}": "Status: {s}",
    "需访问密码": "· password required",
    "DNS 劫持：{s}": "DNS hijack: {s}",
    "生效中": "active",
    "未生效 — {r}": "inactive — {r}",
    "未知原因": "unknown reason",
    "已放行设备：{n} 台　劫持查询：{q} 次": "Allowed devices: {n} · hijacked queries: {q}",
    "不在放行时段（{s}），新设备无法通过门户": "Outside allowed window ({s}) — new devices blocked",
    "门户地址：{u}": "Portal URL: {u}",
    "强制门户已随热点启动（DNS 劫持生效）": "Portal started with hotspot (DNS hijack on)",
    "门户启动失败：{m}": "Portal failed to start: {m}",
    "热点已关闭，强制门户已停止": "Hotspot off, portal stopped",
    "{ip} 已通过欢迎页并开始上网": "{ip} passed the portal and is online",
    "已拒绝 {ip}：设备数超过上限 {n} 台": "Rejected {ip}: device limit {n} reached",

    # ---- 诊断 / 提示 / 二维码 ----
    "🩺 诊断": "🩺 Diagnostics",
    "正在收集…": "Collecting…",
    "无输出": "No output",
    "诊断失败：{e}": "Diagnostics failed: {e}",
    "输入": "Input",
    "确定": "OK",
    "🔗 扫码连接": "🔗 Scan to connect",
    "手机相机扫码即可连接，无需输入密码": "Scan with your phone camera to connect — no typing",

    # ---- 流量统计 ----
    "📊 流量统计": "📊 Traffic stats",
    "累计": "Total",
    "接收": "Received",
    "发送": "Sent",
    "每日流量": "Daily traffic",
    "流量 TOP 设备": "Top devices",
    "域名排行": "Top domains",
    "最近 DNS 访问": "Recent DNS queries",
    "暂无数据": "No data",
    "暂无记录": "No records",
    "暂无记录（需开启强制门户的 DNS 劫持）": "No records (requires portal DNS hijack)",
    "{n} 次": "{n} hits",
    "统计读取失败": "Failed to read stats",
    "导出 CSV（30 天明细）": "Export CSV (30-day detail)",

    # ---- 工具箱 ----
    "🧰 工具箱": "🧰 Toolbox",
    "文件共享（手机浏览器直接互传文件）": "File sharing (transfer via phone browser)",
    "未启动": "Not running",
    "打开共享目录": "Open folder",
    "端口转发（TCP）": "Port forwarding (TCP)",
    "本机端口": "Local port",
    "设备 IP": "Device IP",
    "设备端口": "Device port",
    "添加": "Add",
    "删除": "Delete",
    "暂无转发规则": "No forwarding rules",
    "请填写完整端口转发信息": "Fill in all port forwarding fields",
    "网速测试（本机下载速率）": "Speed test (local download rate)",
    "开始测速": "Start",
    "正在测速…": "Testing…",
    "测速结果：{m}": "Speed test: {m}",

    # ---- 退出确认 ----
    "⚠️ 热点仍在运行": "⚠️ Hotspot is still running",
    "热点开启中，退出后设备将无法上网。确定退出吗？": "Devices will lose internet after exit. Quit anyway?",
    "返回": "Back",
    "确认退出": "Quit",

    # ---- 迷你悬浮窗 ----
    "台在线": " online",
    "下行 ↓ <b>{v}</b>": "Down ↓ <b>{v}</b>",
    "上行 ↑ <b>{v}</b>": "Up ↑ <b>{v}</b>",
    "点击 开/关 热点": "Toggle hotspot",
    "返回主界面": "Back to main window",
    "开关热点": "Toggle hotspot",
    "关闭浮窗": "Close mini window",

    # ---- 后端动态消息 ----
    "{d} 秒前": "{d}s ago",
    "{d} 分钟前": "{d}m ago",
    "{d} 小时前": "{d}h ago",
    "{d} 天前": "{d}d ago",
    "在线 {n} 台": "{n} online",
    "{ssid} · 运行中 · 在线 {n} 台": "{ssid} · running · {n} online",
    "WiFi 热点管理器 · 热点未开启": "WiFi Hotspot Manager · hotspot off",
    "操作进行中，请稍候": "Operation in progress, please wait",
    "操作失败：{e}": "Operation failed: {e}",
    "已取消定时关闭": "Auto stop cancelled",
    "定时时间到，正在关闭热点…": "Timer expired, stopping hotspot…",
    "将在 {m} 分钟后自动关闭热点": "Hotspot stops in {m} min",
    "临时密码已生效：{pw}": "Temporary password active: {pw}",
    "临时密码已设置：{pw}（热点开启后生效）": "Temp password set: {pw} (applies when hotspot starts)",
    "临时密码：{pw}": "Temp password: {pw}",
    "临时密码已到期，已恢复原密码": "Temp password expired, original restored",
    "临时密码已到期，热点密码已恢复": "Temp password expired, hotspot password restored",
    "临时密码 {pw}，{h} 小时后自动恢复": "Temp password {pw}, restored in {h}h",
    "已取消临时密码（密码保持当前值不变）": "Temp password cancelled (password unchanged)",
    "生效中：{pw} · 剩余 {m} 分钟": "Active: {pw} · {m} min left",
    "生效中：{pw}": "Active: {pw}",
    "缺少 qrcode 库：pip install qrcode": "Missing qrcode: pip install qrcode",
    "悬浮窗不可用": "Mini window unavailable",

    # ---- 核心层透传的静态消息（精确匹配自动翻译）----
    "热点已开启": "Hotspot started",
    "热点已关闭": "Hotspot stopped",
    "没有可用的热点后端": "No usable hotspot backend",
    "强制门户已在运行": "Portal already running",
    "强制门户已停止": "Portal stopped",
    "欢迎页已在运行": "Portal already running",
    "欢迎页未运行": "Portal not running",
    "欢迎页已停止": "Portal stopped",
    "文件共享已在运行": "File sharing already running",
    "文件共享已停止": "File sharing stopped",
    "端口必须是数字": "Port must be a number",
    "端口范围 1-65535": "Port range 1-65535",
    "规则不存在": "Rule not found",
    "Windows portproxy 仅支持 TCP，UDP 请使用防火墙放行":
        "Windows portproxy supports TCP only",
    "找不到可共享的上网连接（请先连上有线/无线网）":
        "No internet connection to share (connect to a network first)",
    "未找到热点网卡（请先开启热点再启用共享）": "Hotspot adapter not found (start hotspot first)",
    "未找到热点网卡": "Hotspot adapter not found",
    "未找到热点网卡（热点未开启？）": "Hotspot adapter not found (hotspot off?)",

    # ---- 设备类型（TYPE_LABEL）----
    "手机": "Phone",
    "平板": "Tablet",
    "笔记本": "Laptop",
    "台式机": "Desktop",
    "电视 / 盒子": "TV / Box",
    "游戏机": "Game console",
    "手表 / 手环": "Watch / Band",
    "音箱": "Speaker",
    "摄像头": "Camera",
    "打印机": "Printer",
    "路由 / AP": "Router / AP",
    "NAS / 服务器": "NAS / Server",
    "智能家居": "Smart home",
    "车机": "Car",
    "未知设备": "Unknown device",

    # ---- 厂商 / 网卡特征（vendor / OUI）----
    "随机化 MAC 设备": "Randomized MAC device",
    "移动热点 (WinRT)": "Mobile hotspot (WinRT)",
    "承载网络 (netsh)": "Hosted network (netsh)",

    # ---- 标题栏按钮 title ----
    "切换浅色/深色": "Toggle light/dark theme",
    "统计": "Stats",
    "工具箱": "Toolbox",
    "迷你悬浮窗": "Mini floating window",
    "最小化": "Minimize",
    "关闭": "Close",
}

_current = "zh_CN"


def detect_lang() -> str:
    """读 Windows 用户 UI 语言：简中 → zh_CN，其余一律 en。"""
    try:
        import ctypes
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFFFF
        return "zh_CN" if lid in (0x0004, 0x0804) else "en"
    except Exception:
        return "zh_CN"


def set_lang(lang: str) -> str:
    """lang: auto（跟随系统）/ zh_CN / en。返回生效语言。"""
    global _current
    if lang == "auto":
        lang = detect_lang()
    _current = "en" if lang == "en" else "zh_CN"
    return _current


def current() -> str:
    return _current


def t(text: str, **kw) -> str:
    """翻译：en 模式查字典（未命中原样返回），再填充 {占位符}。"""
    out = ZH2EN.get(text, text) if _current == "en" else text
    if kw:
        try:
            out = out.format(**kw)
        except Exception:
            pass
    return out
