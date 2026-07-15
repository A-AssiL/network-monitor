<div align="center">

# 🛰️ Network Monitor Pro

### Real-time local network monitoring for the desktop

*Discover devices, watch live bandwidth, chart traffic, and keep a persistent history — all in a clean, dark-themed PySide6 application.*

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython/)
[![Scapy](https://img.shields.io/badge/Networking-Scapy-000000)](https://scapy.net/)
[![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-0078D6?logo=windows&logoColor=white)](#-supported-platforms)
[![Version](https://img.shields.io/badge/version-0.1.0-blue)](#-roadmap)
[![License](https://img.shields.io/badge/License-MIT-green)](#-license)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](#-contributing)
[![Code Style](https://img.shields.io/badge/code%20style-PEP%208-orange)](#-coding-standards)

</div>

---

## 📖 Overview

**Network Monitor Pro** is a modular, real-time desktop application for monitoring local networks.

Written in **Python** with **PySide6**, it discovers devices on your local network using **ARP**, monitors your computer's **upload and download bandwidth** in real time, displays **interactive traffic graphs**, and stores device and traffic history inside a **SQLite** database.

The application follows a **layered architecture** with a clean separation between the **UI**, **Services**, **Network layer**, and **Database layer**. It is designed to be **scalable** and to later support packet capture, SNMP monitoring, alerts, router APIs, and remote monitoring.

> [!NOTE]
> Network Monitor Pro is intended for monitoring networks that **you own or are authorized to administer**. Always ensure you have permission before scanning a network.

---

## 📑 Table of Contents

- [Overview](#-overview)
- [Screenshots](#-screenshots)
- [Demo](#-demo)
- [Feature Overview](#-feature-overview)
- [Detailed Feature List](#-detailed-feature-list)
- [Technology Stack](#-technology-stack)
- [Architecture](#-architecture)
  - [Architecture Diagram](#architecture-diagram)
  - [Design Principles](#design-principles)
- [Project Structure](#-project-structure)
- [Installation](#-installation)
  - [Prerequisites](#prerequisites)
  - [Windows Installation](#windows-installation)
  - [Linux Installation](#linux-installation)
- [Configuration](#-configuration)
- [Running the Application](#-running-the-application)
- [Usage](#-usage)
  - [Example Workflow](#example-workflow)
- [Troubleshooting](#-troubleshooting)
- [FAQ](#-faq)
- [Performance Notes](#-performance-notes)
- [Logging](#-logging)
- [Database](#-database)
- [Roadmap](#-roadmap)
- [Contributing](#-contributing)
  - [Development Guidelines](#development-guidelines)
  - [Coding Standards](#-coding-standards)
- [Future Improvements](#-future-improvements)
- [License](#-license)
- [Author](#-author)
- [Acknowledgements](#-acknowledgements)

---

## 🖼️ Screenshots

> Screenshots live in `app/assets/` (or `docs/screenshots/`). Replace the placeholders below with your own captures.

| Dashboard | Devices |
|:---:|:---:|
| ![Dashboard screenshot placeholder](docs/screenshots/dashboard.png) | ![Devices screenshot placeholder](docs/screenshots/devices.png) |
| **Live metric cards & bandwidth curve** | **Discovered devices table** |

| Traffic | History |
|:---:|:---:|
| ![Traffic screenshot placeholder](docs/screenshots/traffic.png) | ![History screenshot placeholder](docs/screenshots/history.png) |
| **Windowed traffic charts (1 / 5 / 10 min)** | **Persistent device & traffic history** |

| Settings |
|:---:|
| ![Settings screenshot placeholder](docs/screenshots/settings.png) |
| **Interface, interval, and theme configuration** |

---

## 🎬 Demo

> Animated demo placeholder — drop a recorded GIF at `docs/demo.gif` to showcase a live session.

![Animated demo placeholder](docs/demo.gif)

```text
[ Launch ] → [ Scan Network ] → [ Devices appear ] → [ Live bandwidth streams ] → [ History persists ]
```

---

## ✨ Feature Overview

| Area | What you get |
|------|--------------|
| 🔍 **Discovery** | ARP-based device discovery with IP, MAC, hostname, vendor, status, and last-seen |
| 📈 **Bandwidth** | Real-time upload/download monitoring with live charts |
| 🗂️ **History** | Persistent traffic and device history stored in SQLite |
| 🎨 **Interface** | Dark-themed, sidebar-driven GUI with five dedicated pages |
| 🧵 **Performance** | All network and database work runs on background `QThread`s |
| ⚙️ **Configurable** | Interface, refresh interval, theme, and log level |

---

## 📋 Detailed Feature List

### 🔍 Network Discovery (ARP)

- ARP network discovery across the local subnet
- **IP address** detection
- **MAC address** detection
- **Hostname lookup** (reverse DNS)
- **Vendor lookup** via IEEE OUI database
- **Online / offline** detection
- **Last seen** timestamp per device

### 📈 Bandwidth Monitoring

- Live **upload** monitoring
- Live **download** monitoring
- Configurable sampling interval

### 🖥️ User Interface

- **Dashboard** — live metric cards, device counts, and a real-time bandwidth curve
- **Devices** page — sortable table of discovered devices
- **Traffic** page — interactive charts with selectable time windows
- **History** page — persisted device and traffic history
- **Settings** page — runtime configuration

### 🗄️ Persistence & Data

- **SQLite** persistence layer
- **Traffic history** storage
- **Device history** storage
- **Real-time charts** powered by PyQtGraph

### 🧰 Platform & Quality

- **Dark theme** throughout
- **Background threads** using `QThread`
- **Configurable refresh interval**
- **Configurable network interface**
- Structured **logging** (console + rotating file)

---

## 🧑‍💻 Technology Stack

| Technology | Role | Notes |
|------------|------|-------|
| **Python 3.12+** | Core language | Modern typing & performance |
| **PySide6** | GUI framework | Official Qt for Python bindings |
| **PyQtGraph** | Charting | High-performance real-time plots |
| **Scapy** | Networking | ARP sweeps & packet primitives |
| **psutil** | System stats | Bandwidth / interface counters |
| **SQLite** | Database | Embedded, zero-config, WAL mode |
| **QThread** | Concurrency | Off-GUI-thread work |
| **threading** | Concurrency | Locks, events, buffers |
| **JSON** | Configuration | Human-editable `config.json` |

---

## 🏗️ Architecture

Network Monitor Pro is built as a set of **decoupled layers**. The GUI **never blocks**: all networking and database access happens on **background threads**, and results are delivered to the UI exclusively through **Qt Signals**. Networking and SQLite code **never touch the GUI directly**.

### Architecture Diagram

```text
              ┌───────────────────────────────────────────────┐
              │                     UI LAYER                    │
              │   Dashboard · Devices · Traffic · History ·     │
              │                 Settings (PySide6)              │
              └───────────────────────────────────────────────┘
                        ▲                         │
              Qt Signals│ (results)               │ user intent
                        │                         ▼
              ┌───────────────────────────────────────────────┐
              │                  SERVICE LAYER                  │
              │  MonitorService · ScanService ·                 │
              │  PersistenceService   (QThread / QObject)       │
              └───────────────────────────────────────────────┘
                        ▲                         │
                        │                         ▼
         ┌────────────────────────┐   ┌────────────────────────┐
         │     NETWORK LAYER      │   │     DATABASE LAYER      │
         │  ArpScanner (Scapy)    │   │   Database (SQLite)     │
         │  BandwidthMonitor      │   │   Models / schema       │
         │  Vendor / Hostname     │   │   WAL · thread-safe     │
         └────────────────────────┘   └────────────────────────┘
```

**Data flow (top to bottom for intent, bottom to top for results):**

```text
UI  →  Services  →  Network Layer
UI  →  Services  →  Database Layer
```

### Design Principles

> [!IMPORTANT]
> These principles keep the codebase scalable. Please respect them when contributing.

1. **Never block the GUI thread.** Scanning, sampling, and DB writes all run on background threads.
2. **One-way data flow.** Services emit domain objects; the main window fans them out to pages. Pages are pure views.
3. **UI-agnostic core.** The network and database layers know nothing about Qt or the UI.
4. **Signals over shared state.** Threads communicate via Qt Signals and thread-safe buffers, not direct calls.
5. **Thread safety.** `threading.Lock` / `RLock` / `Event`; SQLite uses `check_same_thread=False` + a re-entrant lock and WAL journaling.
6. **Graceful degradation.** Optional dependencies are soft-imported so a missing piece never crashes startup.

---

## 📁 Project Structure

```text
network-monitor/
├── main.py                     # Entry point: logging, config, database, launch UI
├── requirements.txt            # Python dependencies
├── config.json                 # Runtime configuration (auto-created on first run)
├── README.md                   # This file
├── logs/                       # Rotating log files (auto-created)
└── app/
    ├── __init__.py             # App metadata (name, version, author)
    ├── ui/                     # PySide6 pages & the main window (integration hub)
    │   ├── main_window.py
    │   ├── dashboard.py
    │   ├── devices_page.py
    │   ├── graphs_page.py
    │   ├── history_page.py
    │   └── settings_page.py
    ├── widgets/                # Reusable UI widgets (e.g. metric cards)
    │   └── metric_card.py
    ├── network/                # Networking primitives (Scapy / psutil), UI-agnostic
    │   ├── scanner.py
    │   ├── monitor.py
    │   ├── vendor_lookup.py
    │   └── hostname.py
    ├── services/               # Background QThread services bridging core <-> UI
    │   ├── monitor_service.py
    │   ├── scan_service.py
    │   └── persistence_service.py
    ├── database/               # SQLite wrapper + data models
    │   ├── database.py
    │   └── models.py
    ├── utils/                  # Shared helpers
    └── resources/              # Static resources
```

---

## 📥 Installation

### Prerequisites

| Requirement | Windows | Linux |
|-------------|:-------:|:-----:|
| Python 3.12+ | ✅ | ✅ |
| Npcap | ✅ (required) | — |
| libpcap | — | ✅ (usually preinstalled) |
| Admin / root privileges | ✅ (for ARP) | ✅ (for ARP) |

> [!WARNING]
> ARP scanning requires **elevated privileges**. On Windows, run your terminal **as Administrator**; on Linux, run with **`sudo`**.

### Windows Installation

```powershell
# 1. Clone the repository
git clone https://github.com/<your-username>/network-monitor.git
cd network-monitor

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install Python dependencies
pip install -r requirements.txt
```

Then install **Npcap**:

1. Download it from <https://npcap.com>.
2. During installation, enable **"Install Npcap in WinPcap API-compatible Mode"**.
3. Reboot if prompted.

> [!TIP]
> In Windows PowerShell, run each command on its own line — `&&` is **not** a valid statement separator.

### Linux Installation

```bash
# 1. Clone the repository
git clone https://github.com/<your-username>/network-monitor.git
cd network-monitor

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Ensure libpcap is available (Debian/Ubuntu example)
sudo apt-get install libpcap-dev
```

---

## ⚙️ Configuration

On first launch, a `config.json` file is created automatically in the project root. You can edit it directly or change values from the **Settings** page.

```json
{
  "refresh_interval": 1.0,
  "interface": null,
  "theme": "dark",
  "database_path": "network_monitor.db",
  "log_level": "INFO"
}
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `refresh_interval` | `float` | `1.0` | Seconds between bandwidth samples |
| `interface` | `string \| null` | `null` | Network interface to monitor (`null` = auto/all) |
| `theme` | `string` | `"dark"` | UI theme |
| `database_path` | `string` | `"network_monitor.db"` | Path to the SQLite database file |
| `log_level` | `string` | `"INFO"` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

---

## ▶️ Running the Application

### Windows (as Administrator)

```powershell
.venv\Scripts\Activate.ps1
python main.py
```

### Linux (as root)

```bash
source .venv/bin/activate
sudo python3 main.py
```

---

## 🧭 Usage

1. **Launch** the application (with elevated privileges).
2. Open the **Dashboard** to watch live upload/download bandwidth.
3. Click **Scan Network** to discover devices on your LAN.
4. Browse the **Devices** page for a detailed table of every device.
5. Open the **Traffic** page to inspect bandwidth over 1 / 5 / 10-minute windows.
6. Visit the **History** page to review persisted device and traffic history.
7. Adjust the interface, refresh interval, or theme on the **Settings** page.

### Example Workflow

```text
┌──────────────────────────────────────────────────────────────────┐
│ 1. Start the app (Administrator / root)                            │
│ 2. Dashboard shows live bandwidth updating every ~1s              │
│ 3. Click "Scan Network"                                            │
│      → ScanService runs an ARP sweep on a background thread        │
│      → Devices stream into the Devices table as they are found     │
│      → Device counts update on the Dashboard                       │
│      → Results are persisted to SQLite                             │
│ 4. Traffic page renders live + historical charts                   │
│ 5. History page lists discovered devices and past traffic          │
│ 6. Close the app → services stop → database is closed cleanly      │
│ 7. Reopen later → History is still there (persisted)               │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🩺 Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `winpcap is not installed` / no packets captured | Npcap missing or wrong mode | Install **Npcap** in *WinPcap API-compatible* mode and run as Administrator |
| `No module named 'PySide6'` | Dependencies not installed / venv inactive | Activate the venv, then `pip install -r requirements.txt` |
| Bandwidth cards stuck at `--` | No samples being produced | Verify the selected `interface` in Settings / `config.json` |
| `&&` error in PowerShell | PowerShell syntax | Run each command on a separate line |
| ARP scan returns nothing | Insufficient privileges or wrong subnet | Run elevated; confirm the correct interface/subnet |
| `Permission denied` on Linux | ARP requires root | Run with `sudo` |
| GUI fails to start on a headless server | No display available | Run on a machine/session with a graphical environment |

---

## ❓ FAQ

<details>
<summary><strong>Does this capture or inspect packet contents?</strong></summary>

Not yet. The current release performs **ARP discovery** and **bandwidth monitoring**. Deep **packet capture** is planned for a future release (see the [Roadmap](#-roadmap)).
</details>

<details>
<summary><strong>Why do I need Administrator / root privileges?</strong></summary>

ARP scanning sends and receives link-layer packets, which the operating system only allows for elevated processes.
</details>

<details>
<summary><strong>Why does it need Npcap on Windows?</strong></summary>

Scapy relies on a packet-capture driver. **Npcap** (in WinPcap API-compatible mode) provides that capability on modern Windows.
</details>

<details>
<summary><strong>Where is my data stored?</strong></summary>

All history is stored locally in the SQLite database defined by `database_path` (default: `network_monitor.db`). Nothing is sent anywhere.
</details>

<details>
<summary><strong>Can I monitor a specific network interface?</strong></summary>

Yes. Set the `interface` value in `config.json` or choose it on the **Settings** page. Leave it `null` to auto-select.
</details>

<details>
<summary><strong>Does it run on macOS?</strong></summary>

macOS is not officially listed among supported platforms yet. It may work with a compatible libpcap setup, but it is untested.
</details>

---

## ⚡ Performance Notes

- **Non-blocking UI.** Every long-running operation (ARP scans, bandwidth sampling, database I/O) executes on a background `QThread`, keeping the interface responsive at all times.
- **Batched writes.** Traffic samples are buffered and flushed to SQLite in batches rather than one write per sample, reducing disk churn.
- **WAL journaling.** The database uses Write-Ahead Logging for better read/write concurrency.
- **Bounded in-memory history.** Live charts keep a rolling window of samples to cap memory usage.
- **Configurable sampling.** Increase `refresh_interval` to reduce CPU usage on lower-powered machines.
- **History pruning.** The database supports pruning old traffic rows to keep the file size in check.

---

## 🪵 Logging

Network Monitor Pro uses Python's standard `logging` module with two handlers:

- **Console** — human-readable output while running.
- **Rotating file** — `logs/network_monitor.log`, rotated at **1 MB** with **5 backups**.

Log format:

```text
2026-01-01 12:00:00 | INFO     | app.services.scan_service | Scan complete: 4 device(s) found
```

Set the verbosity with the `log_level` key in `config.json` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).

---

## 🗃️ Database

The SQLite schema is created and migrated automatically on first run (versioned via `PRAGMA user_version`). It is opened in **WAL** mode and is fully **thread-safe**.

### Tables

| Table | Purpose |
|-------|---------|
| `devices` | Current known devices (one row per MAC) |
| `discovery_history` | Append-only log of every scan sighting |
| `traffic_history` | Periodic bandwidth samples for graphs/history |
| `alerts` | Notifications such as unknown-device detections |

### `devices`

| Column | Type | Description |
|--------|------|-------------|
| `mac` | `TEXT` (PK) | Device MAC address |
| `ip` | `TEXT` | Last known IP |
| `hostname` | `TEXT` | Resolved hostname |
| `vendor` | `TEXT` | OUI vendor |
| `is_known` | `INTEGER` | Trusted/known flag |
| `online` | `INTEGER` | Online status |
| `first_seen` | `REAL` | First discovery time |
| `last_seen` | `REAL` | Most recent sighting |

### `traffic_history`

| Column | Type | Description |
|--------|------|-------------|
| `id` | `INTEGER` (PK) | Row id |
| `timestamp` | `REAL` | Sample time |
| `download_mbps` | `REAL` | Download throughput |
| `upload_mbps` | `REAL` | Upload throughput |
| `bytes_recv` | `INTEGER` | Bytes received |
| `bytes_sent` | `INTEGER` | Bytes sent |
| `interface` | `TEXT` | Interface name |

### `discovery_history`

| Column | Type | Description |
|--------|------|-------------|
| `id` | `INTEGER` (PK) | Row id |
| `mac` | `TEXT` | Device MAC |
| `ip` | `TEXT` | IP at time of sighting |
| `hostname` | `TEXT` | Hostname at time of sighting |
| `vendor` | `TEXT` | Vendor at time of sighting |
| `online` | `INTEGER` | Status at time of sighting |
| `scanned_at` | `REAL` | Sighting timestamp |

### `alerts`

| Column | Type | Description |
|--------|------|-------------|
| `id` | `INTEGER` (PK) | Row id |
| `level` | `TEXT` | Severity (`info`, `warning`, ...) |
| `category` | `TEXT` | Alert category |
| `message` | `TEXT` | Human-readable message |
| `created_at` | `REAL` | Creation timestamp |
| `acknowledged` | `INTEGER` | Acknowledged flag |

---

## 🗺️ Roadmap

### ✅ Version 0.2

- Live bandwidth monitoring
- Traffic graphs
- SQLite persistence
- History page

### 🔭 Version 0.3

- Packet capture
- Port scanner
- Unknown device alerts
- Notifications

### 🔭 Version 0.4

- SNMP monitoring
- Router API integration
- Remote monitoring
- CSV export
- PDF export
- Speed test

### 🎯 Version 1.0

- A professional, full-featured network monitoring suite

| Version | Theme | Status |
|---------|-------|:------:|
| 0.2 | Core monitoring & persistence | ✅ |
| 0.3 | Deep inspection & alerting | 🔜 |
| 0.4 | Enterprise integrations & exports | 🔜 |
| 1.0 | Complete monitoring suite | 🎯 |

---

## 🤝 Contributing

Contributions are welcome and appreciated! Whether it's a bug report, a feature request, or a pull request, your help makes Network Monitor Pro better.

### Development Guidelines

1. **Fork** the repository and create a feature branch:
   ```bash
   git checkout -b feature/my-awesome-feature
   ```
2. **Set up** a development environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate      # or .venv\Scripts\Activate.ps1 on Windows
   pip install -r requirements.txt
   ```
3. **Make your changes**, keeping the layered architecture intact:
   - UI changes belong in `app/ui/` or `app/widgets/`.
   - Networking logic belongs in `app/network/`.
   - Threading/bridging logic belongs in `app/services/`.
   - Persistence changes belong in `app/database/`.
4. **Test** your changes locally (run the app, exercise the affected pages).
5. **Commit** using clear, descriptive messages.
6. **Open a pull request** describing what changed and why.

> [!TIP]
> Keep the GUI thread free — any new blocking work must run inside a service on a background thread and report back via Qt Signals.

### 📐 Coding Standards

| Standard | Requirement |
|----------|-------------|
| Style | **PEP 8** |
| Typing | Full **type hints** (`from __future__ import annotations`) |
| Docs | **Docstrings** on modules, classes, and public methods |
| Logging | `logging.getLogger(__name__)` — no `print` in library code |
| Models | Dataclasses with `slots=True` |
| Threading | `threading.Lock` / `Event` + `QThread`; never block the GUI |
| Layering | Network/DB layers stay **UI-agnostic** |

---

## 🚧 Future Improvements

- Typed end-to-end models (convert DB rows into dataclasses across all layers)
- Unit and integration test suite with CI
- Packaged binaries / installers (PyInstaller) for Windows and Linux
- Light theme and theming system
- Internationalization (i18n)
- Plugin architecture for custom monitors and exporters
- Configurable alert rules engine
- Per-device bandwidth attribution

---

## 📄 License

This project is licensed under the **MIT License**. See the [`LICENSE`](LICENSE) file for details.

```text
MIT License — you are free to use, modify, and distribute this software
with attribution and without warranty.
```

---

## 👤 Author

**ANOUNE ASSIL**

Network Monitor Pro · v0.1.0

---

## 🙏 Acknowledgements

- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython/) — the GUI framework
- [PyQtGraph](https://www.pyqtgraph.org/) — fast, real-time charts
- [Scapy](https://scapy.net/) — powerful packet crafting and scanning
- [psutil](https://github.com/giampaolo/psutil) — cross-platform system metrics
- [Npcap](https://npcap.com/) — Windows packet capture driver
- [SQLite](https://www.sqlite.org/) — the embedded database engine
- The open-source community for inspiration and tooling

---

<div align="center">

### 🛰️ Network Monitor Pro

*Built with Python & PySide6 — monitor your network, own your data.*

**[⬆ Back to top](#-network-monitor-pro)**

</div>
