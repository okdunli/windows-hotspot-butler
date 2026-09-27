<div align="center">
<img alt="Windows Hotspot Butler" src="icon.svg" height="128">

<h1>Windows Hotspot Butler</h1>

[![中文](https://img.shields.io/badge/中文-README-blue?style=for-the-badge&labelColor=000000)](README.zh-CN.md)
[![License](https://img.shields.io/badge/license-Apache%202.0-green?style=for-the-badge&labelColor=000000)](LICENSE)

</div>

Windows Hotspot Butler is a WiFi hotspot management utility for Windows 10 / 11. Built on top of the Windows mobile hotspot and hosted network, it adds hotspot control, device identification, traffic accounting, a captive portal and a set of LAN utilities in a single desktop application.

## Overview

The mobile hotspot built into Windows offers only basic on/off control. It does not expose the connected devices, does not break down traffic per device, and provides no guest acceptance page, no per-device network access control, and no usage records.

Windows Hotspot Butler fills those gaps:

- unified control of hotspot startup, shutdown and parameters;
- automatic identification of vendor and device type, with persistent per-device profiles;
- upload/download accounting keyed by MAC address, with trend reports and export;
- a captive portal that requires guests to acknowledge the usage notice before access is granted;
- auxiliary tools: QR-code join, LAN file sharing, port forwarding and others.

Intended for machines that serve as a hotspot regularly and where some control over the connected clients is wanted.

---

## Features

- **Hotspot control.** Two backends are supported — Windows mobile hotspot (WinRT) and hosted network (netsh) — selected automatically at startup from the adapter's actual capabilities, or pinned manually in the settings. SSID, password and band (2.4 GHz / 5 GHz / auto) are configurable, as are starting the hotspot automatically on launch and starting the application with Windows (a `HKCU\...\Run` registry entry).
- **Device management.** The hotspot client list, the system ARP table and locally stored device profiles are merged into one view showing IP, MAC, hostname, vendor and device type. A built-in OUI database covers common phone, laptop, TV and IoT vendors, and randomized MACs (privacy addresses used by phones) are flagged separately. Names and types can be pinned by hand, after which automatic detection no longer overrides them. Individual devices can be denied internet access or removed from the roster; new arrivals raise a desktop notification and a tray balloon.
- **Traffic accounting.** Three data sources are available — scapy capture, adapter counters and demo data — chosen automatically according to availability. In capture mode traffic is attributed per MAC, flushed to SQLite every five seconds, so totals survive restarts; the live rate graph keeps the last three minutes. The statistics view offers a 14-day trend, the top 8 devices by usage, a domain ranking and recent DNS lookups, plus CSV export (UTF-8 with BOM, opens cleanly in Excel).
- **Captive portal.** With UDP 53 taken over, DNS lookups from devices not yet allowed are answered with the hotspot gateway address, while the connectivity probes used by iOS, Android and Windows (`/generate_204`, `/hotspot-detect.html`, `/ncsi.txt` and others) receive a 302 redirect so the operating system raises its sign-in prompt on its own. Four welcome page templates ship with the app (aurora, ocean, corporate, minimal); copy, an optional access password and an allowed time window are configurable. Acceptances persist by IP and MAC and can be granted or revoked manually. The portal follows the hotspot up and down by default.
- **Utilities.** QR-code join (standard `WIFI:` format), LAN file sharing over HTTP on port 8081 with browser upload and download, port forwarding based on `netsh interface portproxy`, a download speed test against Cloudflare endpoints, temporary passwords that revert automatically when they expire, a scheduled hotspot shutdown, and configuration import/export.
- **Interface.** A PyWebView + Edge WebView2 front end with dark/light themes and Chinese/English languages, a 160×64 always-on-top floating widget (draggable, position remembered), minimize-to-tray, and the global hotkey `Ctrl + Alt + H`. The legacy Tkinter interface is retained as a fallback via `--tk`.

---

## Getting started

### Requirements

| Item | Requirement |
|------|-------------|
| OS | Windows 10 1607+ / Windows 11 |
| Python | 3.10 or newer (3.12 recommended) |
| Privileges | Run as Administrator — hotspot toggling, DNS hijacking and firewall rules all require it |
| Optional driver | Npcap, installed with "WinPcap API-compatible Mode", required for accurate per-device traffic statistics |

### Installation

```bash
cd windows-hotspot-butler
pip install -r requirements.txt
python main.py
```

Alternatively, double-click `Run.bat`; the script detects the Python environment, checks that pywebview is installed and reports anything missing. When a portable Python is used, unpack it into `tools\python\` under the project root and the launcher will prefer that interpreter.

Recommended first-run sequence:

1. Enter an SSID and password on the settings page and click Save to write the local configuration.
2. Click Apply now to push the parameters into the system hotspot.
3. Click the central toggle to start the hotspot.
4. Click the QR-code button beside the password field and scan it with a phone.

### Command-line options

| Option | Description |
|--------|-------------|
| *(none)* | Start the web UI (default) |
| `--tk` | Start the legacy Tkinter UI |
| `--selftest` | Run the self-check only; prints module and adapter capability information without opening a window |
| `--no-elevate` | Skip the elevation prompt |
| `--verbose` | Verbose logging |
| `--debug` | Open the WebView developer tools |

### Dependencies

| Package | Purpose | Required |
|---------|---------|----------|
| pywebview | Web UI host (Edge WebView2) | Yes |
| pystray, Pillow | System tray icon | No — tray unavailable without them |
| scapy, Npcap | Accurate per-device traffic statistics | No — falls back to adapter totals |
| qrcode | WiFi QR code generation | No — QR feature unavailable without it |

All optional dependencies can be installed at once: `pip install pystray pillow qrcode`

---

## Usage

### Interface layout

The main window has two panels. The left panel holds the hotspot toggle and status: online device count, live down/up rates, uptime and the rate curve of the last three minutes. The right panel is the device list, where each row shows the name, type icon, IP, current rate and lifetime usage.

The buttons at the right of the title bar are, in order: theme toggle, traffic statistics, toolbox, captive portal, floating widget and settings.

### Floating widget and tray

The floating widget measures 160×64, stays on top, and shows the online device count together with live rates. It can be dragged anywhere; the position is saved on exit and restored on the next launch. With minimize-to-tray enabled, the close button minimizes the application to the system tray instead of exiting, and new devices raise a tray balloon. The global hotkey `Ctrl + Alt + H` toggles the hotspot regardless of which window has focus.

### Ports and addresses

| Service | Address |
|---------|---------|
| Captive portal | `http://192.168.137.1/` (listens on port 80) |
| File sharing | `http://192.168.137.1:8081/` |
| Port forwarding | Any TCP port, carried by `netsh interface portproxy` |

`192.168.137.1` is the usual address; the actual value follows the IP assigned to the hotspot adapter.

---

## How it works

### Backend selection

Windows offers three unrelated ways to make a wireless adapter broadcast an SSID, and support varies considerably between cards:

| Method | Backend | Notes |
|--------|---------|-------|
| Wi-Fi Direct | Mobile hotspot (WinRT) | What the Windows 10/11 mobile hotspot is actually built on; most USB adapters use this path |
| Soft AP | Mobile hotspot (WinRT) | The older capability flag; reporting "not supported" here does not mean a hotspot is impossible |
| Hosted network | Hosted network (netsh) | Same approach as Cheetah/360 WiFi |

At startup the application runs `netsh wlan show drivers` and `netsh wlan show wirelesscapabilities`, resolves those three capabilities, and picks a backend: WinRT first (5 GHz capable), falling back to netsh when unavailable. The netsh backend only creates the access point, so starting it also enables Internet Connection Sharing — without ICS, clients associate but have no route out.

Every one of those findings (adapter model, radio types, band support, encryption support, active backend, the upstream connection being shared) is listed in the diagnostics panel and also printed by `python main.py --selftest`.

### Captive portal

The portal consists of two parts: a DNS proxy holding UDP 53 that answers A-record queries from unallowed devices with the hotspot gateway address while logging the requested domains, and an HTTP service on port 80 that serves the portal page and handles the `/accept` request. The DNS proxy that ships with ICS usually occupies UDP 53, so the application pauses SharedAccess, takes the port, then restores the service — Administrator rights are required for that step.

### Threading model

Everything slow (PowerShell invocations, ARP queries, system API calls) runs on background threads or a thread pool, while the `get_state()` exposed to the front end only reads an in-memory snapshot. Interface refresh therefore never blocks the background work. The front end polls every 1.2 seconds.

---

## Configuration and data files

All user data is stored under `%APPDATA%\WifiHotspotManager\`:

| File | Contents |
|------|----------|
| `config.json` | The complete application configuration |
| `devices.json` | Device profiles: name, type, note, last IP, first/last seen |
| `traffic.sqlite3` | Traffic database: lifetime totals, daily usage, sessions, DNS log |
| `blocked.json` | MAC addresses denied internet access |
| `portal_accepted.json` | Portal allow-list (IP, MAC, User-Agent, acceptance time) |
| `portproxy.json` | Port forwarding rules |
| `gateway.txt` | Cached gateway address, avoiding a PowerShell round trip each time |
| `export-*.json`, `traffic-*.csv` | Manually exported configuration and traffic details |
| `share/` | Shared files directory |
| `logs/app.log` | Rolling log, 512 KB × 3 |

Every entry is plain text and can be deleted to return to defaults.

---

## Troubleshooting

**The hotspot toggle has no effect.**
Almost always caused by running without Administrator rights; the badge at the left of the title bar reports the current privilege level. If it persists, open diagnostics for backend state and error details.

**The application reports that the adapter cannot host a hotspot.**
The diagnostics panel lists the probe result for Wi-Fi Direct, Soft AP and hosted network separately. When all three come back negative it is a hardware or driver limitation: use a wireless card that supports at least one of them, or install a solution that brings its own virtual AP driver.

**Devices associate but cannot reach the internet.**
On the netsh backend this is normally ICS failing to start; restart the application as Administrator. Switching to the WinRT backend also resolves it when diagnostics shows WinRT as available.

**The portal page does not appear automatically.**
Automatic display depends on DNS hijacking, which requires taking UDP 53. If the port is held and cannot be taken, open `http://192.168.137.1/` on the device manually.

**Traffic is reported as adapter totals instead of per device.**
Indicates that packet capture is not active. Install Npcap with the WinPcap-compatible option and run `pip install scapy`; the switch happens automatically on restart.

**The tray icon or the QR code does not work.**
They need pystray + Pillow and qrcode respectively. Run `pip install pystray pillow qrcode` to install them.

---

## Development

```
windows-hotspot-butler/
├── main.py                  Entry point: args / selftest / UI dispatch
├── Run.bat                  One-click launcher with environment detection
├── requirements.txt
├── hotspot/
│   ├── core/                Core logic, UI-independent
│   │   ├── hotspot.py       Hotspot control: WinRT and netsh backends
│   │   ├── wificaps.py      Adapter capability probing and backend recommendation
│   │   ├── captive.py       Portal: DNS hijack + HTTP portal + allow-list
│   │   ├── portal.py        Welcome page template rendering
│   │   ├── traffic.py       Traffic engine (scapy / adapter / demo)
│   │   ├── deviceman.py     Device roster: identify, rename, type
│   │   ├── storage.py       SQLite traffic DB and device profile I/O
│   │   ├── netinfo.py       Adapter, ARP and ICS collection
│   │   ├── ics.py           Internet Connection Sharing toggle
│   │   ├── share.py         LAN file sharing service
│   │   ├── portforward.py   Port forwarding (netsh portproxy)
│   │   ├── speedtest.py     Cloudflare download speed test
│   │   ├── oui.py           MAC vendor database and type inference
│   │   ├── config.py        Config schema and persistence
│   │   └── paths.py         Path constants and logging setup
│   ├── webui/               Web UI (default)
│   │   ├── app.py           Window, floating widget, tray, hotkey
│   │   ├── backend.py       JS↔Python bridge; heavy work on a thread pool
│   │   ├── tray.py          Tray icon
│   │   ├── hotkey.py        Global hotkey registration and message loop
│   │   ├── i18n.py          Chinese/English translation table
│   │   └── ui/              Front-end HTML / CSS / JS and mini.html
│   ├── ui/                  Legacy Tkinter UI (--tk fallback)
│   ├── templates/           Portal welcome page templates (4)
│   └── scripts/             PowerShell scripts (tethering.ps1 / ics.ps1)
└── LICENSE                  Apache 2.0
```

Common customizations:

- **Add a portal template.** Drop an HTML file into `hotspot/templates/` and set `portal.template` in `config.json` to its name without the `.html` extension. The placeholders `{{ssid}}`, `{{title}}`, `{{notice_li}}`, `{{button}}` and `{{gateway}}` are available. Rendering uses plain string substitution, so CSS braces in the template are left untouched.
- **Extend the vendor database.** Add lines to `%APPDATA%\WifiHotspotManager\oui_extra.csv` in the format `MACprefix,Vendor,devicetype`, for example `A4C138,Telink,iot`. Valid device types are listed in `DEVICE_TYPES` in `hotspot/core/oui.py`.
- **Modify the interface.** The front end lives in `webui/ui/`; reload after editing, and use `--debug` to open the WebView developer tools. To expose a new call from Python, add a public method to `webui/backend.py` — it is mounted automatically onto `window.pywebview.api`.
- **Check an environment.** Run `python main.py --selftest` to exercise the core modules and adapter probing without opening a window.

---

## License

Released under the [Apache License 2.0](LICENSE).
