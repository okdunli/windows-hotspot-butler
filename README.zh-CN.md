<div align="center">
<img alt="Windows热点管理器" src="icon.svg" height="128">

<h1>Windows热点管理器</h1>

[![English](https://img.shields.io/badge/English-README-blue?style=for-the-badge&labelColor=000000)](README.md)
[![License](https://img.shields.io/badge/license-Apache%202.0-green?style=for-the-badge&labelColor=000000)](LICENSE)

</div>

Windows热点管理器是一个面向 Windows 10 / 11 的 WiFi 热点管理工具。在系统移动热点与承载网络的基础上，提供热点控制、设备识别、流量统计、强制门户以及配套的局域网工具，全部集成在同一个桌面应用中。

## 项目简介

Windows 自带的移动热点仅提供基础的开关能力，无法查看接入设备、区分每台设备的流量占用，也不支持访客准入提示、单设备断网等管理操作。

Windows热点管理器在系统能力之上补齐了这些部分：

- 统一管理热点的开启、关闭与参数配置；
- 自动识别接入设备的厂商、类型，并保留历史档案；
- 按 MAC 统计每台设备的上下行流量，提供趋势报表与导出；
- 提供强制门户（Captive Portal），支持访客阅知后放行；
- 附带扫码连接、文件共享、端口转发等常用辅助功能。

适用于需要长期使用热点、并希望对接入设备进行管理的场景。

---

## 功能特性

- **热点控制**：支持 Windows 移动热点（WinRT）与承载网络（netsh）两种后端，启动时依据网卡实际能力自动选择，也可在设置中手动指定。SSID、密码、频段（2.4 GHz / 5 GHz / 自动）均可配置；支持程序启动时自动开启热点，以及写入注册表 `HKCU\...\Run` 实现开机自启。
- **设备管理**：合并热点客户端列表、系统 ARP 表与本地设备档案，实时展示每台设备的 IP、MAC、主机名、厂商与设备类型。内置 OUI 库覆盖常见手机、笔记本、电视、IoT 模组厂商，并对随机化 MAC（手机隐私地址）单独标注。设备名称与类型可手动指定，指定后不再被自动识别覆盖；支持单设备禁止上网、移除档案，新设备接入时推送桌面通知与托盘气泡。
- **流量统计**：提供 scapy 抓包、网卡计数、演示数据三种数据源，按可用性自动降级。抓包模式下按 MAC 精确区分每台设备的上下行，每 5 秒落盘 SQLite，重启后可继续累计；实时速率曲线保留最近 3 分钟。统计界面提供 14 天用量趋势、用量 TOP 8 设备、域名排行与最近 DNS 查询记录，支持导出 CSV（UTF-8 BOM，Excel 可直接打开）。
- **强制门户**：接管 UDP 53 后，未放行设备的域名解析统一指向热点网关；同时对 iOS / Android / Windows 的联网探测路径（`/generate_204`、`/hotspot-detect.html`、`/ncsi.txt` 等）返回 302 重定向，由系统自动弹出登录提示。内置极光、海风、商务、极简 4 套欢迎页模板，支持自定义文案、访问密码与放行时段；放行名单按 IP 与 MAC 持久化，也支持手动放行或撤销。门户默认随热点自动启停。
- **辅助工具**：二维码扫码连接（`WIFI:` 标准格式）、局域网文件共享（HTTP 8081，浏览器直接上传下载）、端口转发（基于 `netsh interface portproxy`）、下行速率测试（Cloudflare 端点）、临时密码（到期自动还原原密码）、定时关闭热点、配置导入导出。
- **界面与交互**：PyWebView + Edge WebView2 网页界面，支持深色 / 浅色主题与中英文双语；提供 160×64 悬浮窗（窗口置顶、可拖拽、位置记忆）、系统托盘最小化与全局热键 `Ctrl + Alt + H`。传统 Tkinter 界面保留作为兼容回退，可通过 `--tk` 启用。

---

## 快速开始

### 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 1607 及以上 / Windows 11 |
| Python | 3.10 及以上（推荐 3.12） |
| 运行权限 | 建议以管理员身份运行（热点开关、DNS 劫持、防火墙规则均需该权限） |
| 可选驱动 | Npcap，安装时勾选 WinPcap API 兼容模式，用于按设备的精确流量统计 |

### 安装与启动

```bash
cd windows-hotspot-butler
pip install -r requirements.txt
python main.py
```

也可双击 `Run.bat` 启动，脚本会自动检测 Python 环境并检查 pywebview 是否已安装，缺失时给出提示。若使用免安装版 Python，将其解压至项目根目录下的 `tools\python\`，启动脚本会优先采用该解释器。

首次使用建议按以下顺序操作：

1. 在设置页填写 SSID 与密码，点击「保存」写入本地配置；
2. 点击「立即下发配置」将参数写入系统热点；
3. 点击主界面中央开关启动热点；
4. 点击密码右侧的二维码按钮，使用手机扫码连接。

### 命令行参数

| 参数 | 说明 |
|------|------|
| （无参数） | 启动网页界面（默认） |
| `--tk` | 启动传统 Tkinter 界面 |
| `--selftest` | 仅执行自检，输出各模块与网卡能力信息，不打开窗口 |
| `--no-elevate` | 跳过提权提示 |
| `--verbose` | 输出详细日志 |
| `--debug` | 开启 WebView 调试工具 |

### 依赖说明

| 依赖 | 用途 | 是否必需 |
|------|------|----------|
| pywebview | 网页界面宿主（Edge WebView2） | 必需 |
| pystray、Pillow | 系统托盘图标 | 可选，缺失时托盘功能不可用 |
| scapy、Npcap | 按设备的精确流量统计 | 可选，缺失时回退为网卡总量统计 |
| qrcode | 生成 WiFi 二维码 | 可选，缺失时扫码功能不可用 |

可选依赖可一次性安装：`pip install pystray pillow qrcode`

---

## 使用说明

### 界面构成

主界面分为左右两栏：左栏为热点开关与运行状态，包含在线设备数、实时上下行速率、运行时长与最近 3 分钟速率曲线；右栏为设备列表，每条记录显示名称、类型图标、IP、当前速率与累计用量。

标题栏右侧依次为：主题切换、流量统计、工具箱、强制门户、悬浮窗、设置。

### 悬浮窗与托盘

悬浮窗尺寸为 160×64，窗口置顶显示在线设备数与实时速率，支持拖拽，退出时记录位置并在下次启动恢复。开启「关闭时最小化到托盘」后，点击关闭按钮不退出程序，而是最小化至系统托盘；有新设备接入时通过托盘气泡通知。全局热键 `Ctrl + Alt + H` 可在任意界面下切换热点状态。

### 服务端口与地址

| 服务 | 访问地址 |
|------|----------|
| 强制门户 | `http://192.168.137.1/`（监听 80 端口） |
| 文件共享 | `http://192.168.137.1:8081/` |
| 端口转发 | 任意 TCP 端口，由 `netsh interface portproxy` 承载 |

上述地址以热点网卡实际分配的 IP 为准，通常为 `192.168.137.1`。

---

## 工作原理

### 热点后端选择

Windows 下发射 WiFi 信号有三条互不相同的路径，不同无线网卡的支持情况差异较大：

| 方式 | 对应后端 | 说明 |
|------|----------|------|
| Wi-Fi Direct | 移动热点 (WinRT) | Windows 10/11 系统移动热点的底层实现，多数 USB 网卡走此路径 |
| 软 AP (Soft AP) | 移动热点 (WinRT) | 旧式能力标志，显示不支持并不代表无法发射热点 |
| 承载网络 (hostednetwork) | 承载网络 (netsh) | 与猎豹 / 360 免费 WiFi 方案相同 |

程序启动时执行 `netsh wlan show drivers` 与 `netsh wlan show wirelesscapabilities`，解析上述三项能力后选择后端：默认优先 WinRT（支持 5 GHz），不可用时回退 netsh。netsh 后端仅负责建立 AP，因此在其启动后会额外启用 Internet 连接共享（ICS），否则设备虽能连上但无法访问外网。

以上判定结果（网卡型号、无线电类型、频段支持、加密能力、当前后端、被共享的上网连接）均可在「诊断」面板中查看，执行 `python main.py --selftest` 也可输出同样内容。

### 强制门户

门户由两部分组成：一是抢占 UDP 53 的 DNS 代理，将未放行设备的 A 记录查询应答为热点网关地址，同时记录域名日志；二是监听 80 端口的 HTTP 服务，负责返回门户页并处理 `/accept` 放行请求。ICS 自带的 DNS 代理通常占用 UDP 53，程序会尝试暂停 SharedAccess 服务完成抢占后恢复，该过程需要管理员权限。

### 线程模型

所有耗时操作（PowerShell 调用、ARP 查询、系统 API 调用）均在后台线程或线程池中执行，暴露给前端的 `get_state()` 仅读取内存快照，因此界面刷新不会阻塞后台任务。前端轮询间隔为 1.2 秒。

---

## 配置与数据文件

用户数据统一保存在 `%APPDATA%\WifiHotspotManager\`：

| 文件 | 内容 |
|------|------|
| `config.json` | 应用全部配置 |
| `devices.json` | 设备档案：名称、类型、备注、历史 IP、首末次上线时间 |
| `traffic.sqlite3` | 流量数据库，含累计用量、每日用量、会话与 DNS 查询记录 |
| `blocked.json` | 被禁止上网的设备 MAC |
| `portal_accepted.json` | 门户放行名单（IP、MAC、User-Agent、放行时间） |
| `portproxy.json` | 端口转发规则 |
| `gateway.txt` | 网关地址缓存，避免每次请求 PowerShell |
| `export-*.json`、`traffic-*.csv` | 手动导出的配置文件与流量明细 |
| `share/` | 文件共享目录 |
| `logs/app.log` | 运行日志，512 KB × 3 滚动 |

以上文件均为纯文本，可直接删除以恢复默认状态。

---

## 常见问题

**热点开关无响应。**
多数由未以管理员身份运行导致，标题栏左侧徽标会显示当前权限。若仍失败，可通过「诊断」查看后端状态与错误信息。

**提示网卡不支持发射热点。**
诊断面板会分别列出 Wi-Fi Direct、软 AP、承载网络三项能力的探测结论。三项均不可用时属于网卡硬件或驱动限制，需更换支持上述能力之一的无线网卡，或安装自带虚拟 AP 网卡驱动的方案。

**设备可连接热点但无法访问外网。**
使用 netsh 后端时通常由 ICS 未成功启用导致，建议以管理员身份重启程序；若诊断显示 WinRT 可用，也可切换至该后端。

**门户页未自动弹出。**
自动弹出依赖 DNS 劫持，需要抢占 UDP 53 端口。若该端口被占用且无法抢占，需手动访问 `http://192.168.137.1/` 打开门户页。

**流量显示为网卡总量而非按设备区分。**
表示未启用抓包数据源。需安装 Npcap（勾选 WinPcap API 兼容模式）并执行 `pip install scapy`，重启后自动切换。

**托盘图标或二维码不可用。**
分别需要 pystray + Pillow 与 qrcode，执行 `pip install pystray pillow qrcode` 安装即可。

---

## 开发说明

```
windows-hotspot-butler/
├── main.py                  入口：参数解析 / 自检 / 界面分发
├── Run.bat                  一键启动脚本，自动检测运行环境
├── requirements.txt
├── hotspot/
│   ├── core/                核心逻辑，与界面无关
│   │   ├── hotspot.py       热点控制：WinRT 与 netsh 双后端
│   │   ├── wificaps.py      网卡能力探测与后端推荐
│   │   ├── captive.py       强制门户：DNS 劫持 + HTTP 门户 + 放行名单
│   │   ├── portal.py        欢迎页模板渲染
│   │   ├── traffic.py       流量统计引擎（scapy / adapter / demo）
│   │   ├── deviceman.py     设备台账：识别、改名、类型管理
│   │   ├── storage.py       SQLite 流量库与设备档案读写
│   │   ├── netinfo.py       网卡、ARP、ICS 信息采集
│   │   ├── ics.py           Internet 连接共享启停
│   │   ├── share.py         局域网文件共享服务
│   │   ├── portforward.py   端口转发（netsh portproxy）
│   │   ├── speedtest.py     Cloudflare 下行测速
│   │   ├── oui.py           MAC 厂商库与设备类型推断
│   │   ├── config.py        配置定义与持久化
│   │   └── paths.py         路径常量与日志初始化
│   ├── webui/               网页界面（默认）
│   │   ├── app.py           窗口、悬浮窗、托盘、热键
│   │   ├── backend.py       JS↔Python 桥接层，耗时操作走线程池
│   │   ├── tray.py          托盘图标
│   │   ├── hotkey.py        全局热键注册与消息循环
│   │   ├── i18n.py          中英文翻译表
│   │   └── ui/              前端 HTML / CSS / JS 与 mini.html
│   ├── ui/                  Tkinter 传统界面（--tk 回退）
│   ├── templates/           门户欢迎页模板（4 套）
│   └── scripts/             PowerShell 脚本（tethering.ps1 / ics.ps1）
└── LICENSE                  Apache 2.0
```

常见定制方式：

- **新增门户模板**：将 HTML 文件放入 `hotspot/templates/`，并在 `config.json` 中将 `portal.template` 设置为文件名（不含 `.html`）。模板支持 `{{ssid}}`、`{{title}}`、`{{notice_li}}`、`{{button}}`、`{{gateway}}` 等占位符，渲染采用字符串替换方式，与模板内的 CSS 花括号不冲突。
- **扩展厂商库**：在 `%APPDATA%\WifiHotspotManager\oui_extra.csv` 中按 `MAC前缀,厂商名,设备类型` 逐行写入，例如 `A4C138,Telink,iot`。设备类型取值参见 `hotspot/core/oui.py` 中的 `DEVICE_TYPES`。
- **修改界面**：前端文件位于 `webui/ui/`，修改后刷新即可生效，配合 `--debug` 可打开 WebView 开发者工具。需要向前端暴露新接口时，在 `webui/backend.py` 中新增公共方法即可自动挂载到 `window.pywebview.api`。
- **环境自检**：执行 `python main.py --selftest`，可在不打开界面的情况下验证各核心模块与网卡能力。

---

## 许可证

Windows热点管理器基于 [Apache License 2.0](LICENSE) 开源。
