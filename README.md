# Network Monitor Pro

A cross-platform (Windows / Linux) desktop application for monitoring your local
network in real time. It shows live upload/download bandwidth, discovers devices
on your LAN via ARP scanning, and captures live traffic with a Wireshark-style
packet viewer — all in a clean, dark-themed GUI backed by a local SQLite
database.

Built with **PySide6** (Qt for Python), **Scapy**, **psutil**, and
**pyqtgraph**.

---

## Features

- **Live bandwidth monitor** — real-time download/upload throughput (Mbps),
  sampled ~once per second, for all interfaces or a chosen one.
- **Device discovery** — ARP scans your subnet, lists devices by MAC/IP with
  hostname and vendor (OUI) lookup, and remembers them across runs.
- **Packet capture (Wireshark-style)** — live packet table with BPF filter,
  Start/Stop, protocol colouring, and a per-packet detail + hexdump pane.
- **History & graphs** — bandwidth history charts and a browsable log of every
  device sighting and traffic sample.
- **Persistence** — everything is stored in a thread-safe SQLite database so
  your history survives restarts.
- **Dark themed UI** — a sidebar-navigated shell (Dashboard, Devices, Traffic,
  Capture, History, Settings).
- **Robust by design** — heavy work runs on background threads and never blocks
  the GUI; optional dependencies degrade gracefully instead of crashing.

---

## Requirements

- **Python 3.10+**
- Dependencies (see `requirements.txt`):

  | Package    | Version  | Used for                         |
  |------------|----------|----------------------------------|
  | PySide6    | 6.11.1   | GUI (Qt for Python)              |
  | shiboken6  | 6.11.1   | PySide6 runtime binding          |
  | scapy      | 2.7.0    | ARP scan + packet capture        |
  | pyqtgraph  | 0.14.0   | Live bandwidth / history graphs  |
  | psutil     | 7.2.2    | Bandwidth counters               |
  | numpy      | 2.4.6    | Graph data handling              |

- **Npcap** (Windows) — required for ARP scanning and packet capture.
  Install from https://npcap.com with "WinPcap API-compatible mode" enabled.
- On Linux, `libpcap` is used (usually already present).

---

## Installation

```bash
# 1. Clone / copy the project, then from the project root:
python -m venv .venv

# 2. Activate the virtual environment
#    Windows (PowerShell):
.venv\Scripts\Activate.ps1
#    Linux / macOS:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Running

```bash
python main.py
```

> **Elevated privileges are required** for ARP scanning and packet capture,
> because they use raw sockets:
>
> - **Windows** — run your terminal **as Administrator** (and install Npcap).
> - **Linux** — run with `sudo python main.py`, or grant capabilities:
>   `sudo setcap cap_net_raw,cap_net_admin=eip $(readlink -f $(which python))`.
>
> Without elevation the GUI still starts; bandwidth monitoring works, but
> scanning/capture will report that they are unavailable.

---

## Configuration

On first run a `config.json` is created in the project root with sane defaults.
You can edit it directly or change values from the in-app **Settings** page
(changes are saved back to this file).

```json
{
  "refresh_interval": 1.0,
  "interface": null,
  "theme": "dark",
  "database_path": "network_monitor.db",
  "log_level": "INFO"
}
```

| Key                | Meaning                                                        |
|--------------------|----------------------------------------------------------------|
| `refresh_interval` | Seconds between bandwidth samples.                             |
| `interface`        | Interface name to monitor, or `null` for all interfaces.       |
| `theme`            | UI theme (`"dark"`).                                           |
| `database_path`    | Path to the SQLite database file.                              |
| `log_level`        | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`).           |

**Optional:** drop an OUI CSV at `app/resources/oui.csv` for full vendor-name
resolution. Without it, a small built-in vendor table is used.

---

## Usage

1. **Dashboard** — at-a-glance bandwidth, device counts, alerts, and packets
   captured.
2. **Devices** — click **Scan Network** (toolbar) to discover devices on your
   LAN. Results persist across runs.
3. **Traffic** — live download/upload graphs over time.
4. **Capture** — enter an optional BPF filter (e.g. `tcp port 443`, `arp`),
   click **Start Capture**, and watch packets stream in. Click any packet to
   inspect its details. Leaving the filter blank captures everything.
5. **History** — browse stored traffic samples and device sightings.
6. **Settings** — change the monitored interface, sample interval, etc.

---

## Architecture

A strict, layered design keeps features decoupled and the GUI responsive:

```
           +---------------------------+
           |            UI             |   PySide6 pages (pure views)
           |  dashboard / devices /    |
           |  graphs / capture /       |
           |  history / settings       |
           +-------------+-------------+
                         |  Qt signals only
           +-------------v-------------+
           |         Services          |   QThread orchestration
           |  monitor / scan /         |
           |  capture / persistence    |
           +------+-------------+------+
                  |             |
        +---------v--+       +--v----------+
        |  Network   |       |  Database   |
        | monitor /  |       |  SQLite     |
        | scanner /  |       |  (WAL,      |
        | packet_cap |       |  threadsafe)|
        +------------+       +-------------+
```

- **UI never touches the network or DB directly.** Pages emit request signals
  and receive results through slots.
- **Services** run all blocking work on background `QThread`s and communicate
  outward via Qt signals, so the GUI thread is never blocked.
- **Packet capture is batched** — captured packets are buffered and delivered
  to the UI in bundles a few times a second (and persisted in bulk), keeping
  the window smooth even under a full, unfiltered capture.
- **The database** uses a single connection with `check_same_thread=False`, a
  re-entrant lock, and WAL mode for safe concurrent access from workers.

### Project layout

```
network-monitor/
  main.py                     # Entry point: logging, config, DB, launch window
  config.json                 # Runtime configuration (auto-created)
  requirements.txt
  logs/                       # Rotating log files
  app/
    ui/
      main_window.py          # Navigation shell + service wiring
      dashboard.py
      devices_page.py
      graphs_page.py
      capture_page.py         # Wireshark-style packet view
      history_page.py
      settings_page.py
    widgets/
      metric_card.py          # Reusable dark metric card
    network/
      monitor.py              # BandwidthMonitor (psutil)
      scanner.py              # ArpScanner (scapy)
      packet_capture.py       # PacketCapture (scapy sniffer)
      vendor_lookup.py        # OUI -> vendor
      hostname.py
    services/
      monitor_service.py      # Streams BandwidthSample
      scan_service.py         # ARP scan + persist devices
      capture_service.py      # Batched packet capture bridge
      persistence_service.py  # Buffered traffic write-through / read-back
    database/
      database.py             # Thread-safe SQLite wrapper (schema v2)
      models.py
    resources/
      oui.csv                 # Optional OUI vendor data
```

---

## Data & storage

All data lives in a local SQLite file (`network_monitor.db` by default) with
these stores:

- `devices` — current known/seen devices (one row per MAC).
- `discovery_history` — append-only log of every scan sighting.
- `traffic_history` — periodic bandwidth samples.
- `packets` — optional captured-packet metadata.
- `alerts` — notifications such as unknown-device detections.

The schema is versioned (`PRAGMA user_version`) and migrates forward
automatically on startup.

---

## Logging

Logs are written to both the console and `logs/network_monitor.log`
(rotated at 1 MB, 5 backups). Set `log_level` in `config.json` to `DEBUG` for
verbose diagnostics.

---

## Troubleshooting

- **"Packet capture unavailable" / no scan results** — run as Administrator
  (Windows, with Npcap installed) or with `sudo` (Linux).
- **No devices found** — confirm you're on the right subnet; some devices don't
  answer ARP. Check the interface setting.
- **Generic vendor names** — add `app/resources/oui.csv` for full lookup.
- **UI feels slow during a huge capture** — apply a BPF filter (e.g.
  `tcp port 443`) to reduce the packet rate. The capture pipeline batches and
  caps memory, but a narrower filter is always lighter.

---

## Roadmap

- [x] Live bandwidth monitoring
- [x] ARP device discovery + persistence
- [x] Packet capture (Wireshark-style)
- [ ] Port scanner
- [ ] Unknown-device alerts
- [ ] SNMP / router-API integration
- [ ] CSV / PDF export
- [ ] Remote monitoring

---

## License

This project is provided as-is for educational and personal use.

## Disclaimer

Only monitor and capture traffic on networks you own or are authorized to
analyze. You are responsible for complying with all applicable laws.
