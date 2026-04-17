# 👁️ Argus — Predictive Disk Health Monitor

> SMART monitoring that tells you *when* a disk will fail, not just *that* it's failing. Backblaze-calibrated thresholds, linear regression forecasts, DAS enclosure support. No cloud. No subscriptions.

\![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
\![Python](https://img.shields.io/badge/Python-3.10+-blue)
\![Prometheus](https://img.shields.io/badge/Metrics-Prometheus-red)

---

## Why Argus?

Most SMART monitors tell you a disk has reallocated sectors. Argus tells you the disk will hit its critical threshold **in 47 days** — and sends you a notification before it gets there.

- **Predictive** — linear regression on 30-day SMART history forecasts failure before it happens
- **DAS-aware** — native support for JMicron JMB576 pass-through (TerraMaster D5-300 and similar)
- **Backblaze-calibrated** — thresholds based on Backblaze's real-world failure data, not vendor defaults
- **No cloud** — everything runs on your hardware. Data never leaves your network.

---

## What is Argus?

Argus is a SMART monitoring daemon for Linux homelab systems. It collects SMART attributes every 6 hours, builds a rolling 180-day history, and analyses trends to give you early warning of disk failures.

- **SMART collection** every 6h across all configured disks
- **Linear regression forecast** — predicts days until critical threshold
- **Temperature anomaly detection** — z-score based, catches thermal events
- **ntfy alerts** — status-change notifications with priority routing
- **Prometheus metrics** — per-disk health score, forecast days, temperature, sector counts
- **DAS enclosure support** — JMicron JMB576 pass-through for multi-bay enclosures

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│                  Your Homelab                    │
│                                                  │
│  /dev/sd*  ──► argus-collector ──► history.json  │
│  DAS slots         (6h cron)                     │
│                        │                         │
│               argus-analyzer ──► argus-watcher   │
│               (forecast engine)    (30m cron)    │
│                                        │         │
│                                   ntfy alerts    │
│                                        │         │
│               argus-exporter ──► Prometheus      │
│               (port 9193)        └──► Grafana    │
└──────────────────────────────────────────────────┘
```

| Script | Description |
|---|---|
| `argus-collector.py` | Collects SMART attributes, appends to history JSON |
| `argus-analyzer.py` | Analyses history, produces status + forecasts |
| `argus-watcher.py` | Runs analyzer every 30 min, sends ntfy on change |
| `argus-exporter.py` | Prometheus metrics exporter (port 9193) |

---

## Requirements

- Linux host (Debian 12 / Ubuntu 22.04+ recommended)
- Python 3.10+
- `smartmontools` (`sudo apt install smartmontools`)
- ntfy instance (optional, for alerts)
- Prometheus + Grafana (optional, for dashboards)

**Tested on:** Debian 12.5, smartctl 7.3, Python 3.11

---

## Quick Start

**TL;DR:** clone → `bash install.sh` → add your disks to `argus.conf` → done in ~10 minutes.

### 1. Clone the repository

```bash
git clone https://github.com/pdegidio/argus-disk.git
cd argus-disk
```

### 2. Run the installer

```bash
bash install.sh
```

The installer will:
- Check requirements
- Create a dedicated `argus` system user
- Auto-discover your disks via `smartctl --scan`
- Walk you through the config
- Install cron jobs

### 3. Add your disks to the config

```bash
sudo nano /opt/argus/config/argus.conf
```

Add a section for each disk:

```ini
[disk:my-ssd]
device = /dev/sda
type   = sat
class  = ssd

[disk:my-hdd]
device = /dev/sdb
type   = sat
class  = hdd
```

For DAS enclosures with JMicron JMB576 (e.g. TerraMaster D5-300):

```ini
[disk:das-slot1]
device = /dev/sdb
type   = jmb39x,0
class  = hdd

[disk:das-slot2]
device = /dev/sdb
type   = jmb39x,1
class  = hdd
```

### 4. Run the first collection

```bash
python3 /opt/argus/scripts/argus-collector.py
```

### 5. Check status

```bash
python3 /opt/argus/scripts/argus-analyzer.py
```

Example output:
```
👁️  Argus SMART Analysis — 2026-04-17T09:00:00+00:00
   Samples: 28 (forecast window: 30d)
   Overall: OK

✅ ssd-system (SanDisk Ultra II 960GB)  health=95/100  status=OK
✅ hdd-data (WD Red Pro 8TB)            health=100/100  status=OK
🟡 das-slot5 (WD Red Pro 8TB)           health=70/100  status=WARNING
    🟡 udma_crc_error_count=1 ≥ WARN (5)
    📈 udma_crc_error_count: 1→100 in 142.3d
```

---

## Noise Filtering

Argus is calibrated to avoid common false positives:

- `seek_error_rate` is intentionally excluded — Seagate packs seek totals in the upper 32 bits, making cross-vendor comparison unreliable. Backblaze doesn't use it either.
- `udma_crc_error_count` warns at 5 (not 1) — a single historical CRC on multi-year disks is physiological. Growth is what matters — the forecast captures it.
- Temperature anomaly requires z-score ≥ 2.0 over 10+ samples.

---

## Prometheus Metrics

The exporter exposes the following on port `9193`:

| Metric | Description |
|---|---|
| `argus_disk_health_score` | Health score per disk (0–100) |
| `argus_disk_status` | Status per disk (0=OK, 1=WARNING, 2=CRITICAL) |
| `argus_disk_temperature_celsius` | Current temperature |
| `argus_disk_power_on_hours` | Power-on hours |
| `argus_disk_reallocated_sectors` | Reallocated sector count |
| `argus_disk_pending_sectors` | Current pending sectors |
| `argus_disk_crc_errors` | UDMA CRC error count |
| `argus_disk_forecast_days` | Days until attribute reaches critical (per attribute) |
| `argus_overall_status` | Fleet-wide status |
| `argus_last_run_age_seconds` | Seconds since last analyzer run |

---

## Directory Structure

```
argus-disk/
├── config/
│   └── argus.conf.example
├── docs/
│   └── argus-exporter.service
├── scripts/
│   ├── argus-collector.py
│   ├── argus-analyzer.py
│   ├── argus-watcher.py
│   └── argus-exporter.py
├── install.sh
├── CHANGELOG.md
└── README.md
```

---

## Upgrading to Cortex

Argus monitors your disks. **[Cortex](https://github.com/pdegidio/cortex-homelab)** monitors your entire homelab stack — Docker containers, *arr services, log analysis via local LLM.

Both are designed to run together on the same machine with zero conflicts.

Available at: **[paolodegidio.gumroad.com/l/cortex-homelab](https://paolodegidio.gumroad.com/l/cortex-homelab)**

---

## Contributing

Issues and pull requests are welcome. If Argus saves a disk for you, consider starring the repo.

---

## License

MIT — use it, modify it, ship it. Attribution appreciated but not required.
