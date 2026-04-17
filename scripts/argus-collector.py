#\!/usr/bin/env python3
"""
Argus — Predictive Disk Health Monitor
argus-collector.py: Collects SMART attributes from physical disks and appends
to argus-history.json. Designed to run every 6h via cron.

https://github.com/pdegidio/argus-disk
"""

import json
import subprocess
import re
import os
import sys
import configparser
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── Config loader ────────────────────────────────────────────────────────
DEFAULT_CONFIG = Path("/opt/argus/config/argus.conf")

def load_config(config_path: Path = None) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg_file = config_path or DEFAULT_CONFIG
    if not cfg_file.exists():
        print(f"[\!] Config not found: {cfg_file} — using defaults", file=sys.stderr)
        return cfg
    cfg.read(cfg_file)
    return cfg

def get_config_path() -> Path:
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--config" and i < len(sys.argv):
            return Path(sys.argv[i + 1])
    return DEFAULT_CONFIG

def parse_disks_from_config(cfg: configparser.ConfigParser) -> list:
    """Parse disk entries from config sections [disk:alias]."""
    disks = []
    for section in cfg.sections():
        if not section.startswith("disk:"):
            continue
        alias = section[5:]
        device = cfg.get(section, "device", fallback=None)
        dtype  = cfg.get(section, "type",   fallback="sat")
        dclass = cfg.get(section, "class",  fallback="hdd")
        if device:
            disks.append({"device": device, "type": dtype,
                          "alias": alias,   "class": dclass})
    return disks

# ─── Paths ────────────────────────────────────────────────────────────────
def get_history_file(cfg: configparser.ConfigParser) -> Path:
    return Path(cfg.get("argus", "history_file",
                        fallback="/var/lib/argus/argus-history.json"))

def get_retention_days(cfg: configparser.ConfigParser) -> int:
    return cfg.getint("argus", "retention_days", fallback=180)

# ─── SMART attribute map ──────────────────────────────────────────────────
ATTR_MAP = {
    "5":   "reallocated_sector_ct",
    "7":   "seek_error_rate",
    "9":   "power_on_hours",
    "10":  "spin_retry_count",
    "12":  "power_cycle_count",
    "184": "end_to_end_error",
    "187": "reported_uncorrect",
    "188": "command_timeout",
    "190": "airflow_temp_celsius",
    "193": "load_cycle_count",
    "194": "temperature_celsius",
    "196": "reallocated_event_count",
    "197": "current_pending_sector",
    "198": "offline_uncorrectable",
    "199": "udma_crc_error_count",
    # SanDisk SSD
    "165": "total_write_erase_count",
    "166": "min_we_cycle",
    "167": "min_bad_block_die",
    "169": "total_bad_block",
    "171": "program_fail_count",
    "172": "erase_fail_count",
    "173": "avg_we_cycle",
    "174": "unexpect_power_loss_ct",
    "232": "perc_avail_resrvd_space",
    "233": "total_nand_writes_gib",
    "241": "total_lbas_written",
    "242": "total_reads_gib",
    "244": "thermal_throttle",
}

# ─── Parser SMART ─────────────────────────────────────────────────────────
def run_smartctl(device: str, dev_type: str) -> str:
    try:
        r = subprocess.run(
            ["smartctl", "-a", "-d", dev_type, device],
            capture_output=True, text=True, timeout=30
        )
        return r.stdout
    except Exception as e:
        print(f"[\!] smartctl error on {device}: {e}", file=sys.stderr)
        return ""

def parse_smart_output(output: str) -> dict:
    result = {"model": None, "serial": None, "capacity_bytes": None, "attrs": {}}
    for line in output.splitlines():
        if "Device Model:" in line or "Product:" in line:
            result["model"] = line.split(":", 1)[1].strip()
        elif "Serial Number:" in line or "Serial number:" in line:
            result["serial"] = line.split(":", 1)[1].strip()
        elif "User Capacity:" in line:
            m = re.search(r"([\d.,]+)\s+bytes", line)
            if m:
                try:
                    result["capacity_bytes"] = int(m.group(1).replace(".", "").replace(",", ""))
                except ValueError:
                    pass
    attr_section = False
    for line in output.splitlines():
        if re.match(r"^ID#\s+ATTRIBUTE_NAME", line.strip()):
            attr_section = True
            continue
        if not attr_section:
            continue
        if not line.strip():
            if result["attrs"]:
                break
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        attr_id = parts[0]
        if attr_id not in ATTR_MAP:
            continue
        try:
            value_norm = int(parts[3])
            raw = parts[9]
            raw_m = re.match(r"(\d+)", raw)
            raw_val = int(raw_m.group(1)) if raw_m else 0
        except (ValueError, IndexError):
            continue
        key = ATTR_MAP[attr_id]
        result["attrs"][key] = {"raw": raw_val, "norm": value_norm}
    return result

# ─── Collector ────────────────────────────────────────────────────────────
def collect_sample(disks: list) -> dict:
    sample = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disks": {},
    }
    for cfg_disk in disks:
        device = cfg_disk["device"]
        alias  = cfg_disk["alias"]
        if not os.path.exists(device):
            sample["disks"][alias] = {"error": "device not found"}
            continue
        out = run_smartctl(device, cfg_disk["type"])
        if not out:
            sample["disks"][alias] = {"error": "smartctl failed"}
            continue
        parsed = parse_smart_output(out)
        parsed["device"]     = device
        parsed["smart_type"] = cfg_disk["type"]
        parsed["class"]      = cfg_disk["class"]
        sample["disks"][alias] = parsed
    return sample

def load_history(history_file: Path) -> dict:
    if not history_file.exists():
        return {"samples": []}
    try:
        return json.loads(history_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"[\!] history load error: {e} — starting fresh", file=sys.stderr)
        return {"samples": []}

def save_history(history: dict, history_file: Path, retention_days: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    history["samples"] = [
        s for s in history["samples"]
        if datetime.fromisoformat(s["ts"]) > cutoff
    ]
    history_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = history_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(history, indent=2))
    tmp.replace(history_file)

def main() -> int:
    dry_run = "--dry-run" in sys.argv
    cfg = load_config(get_config_path())
    disks = parse_disks_from_config(cfg)
    history_file = get_history_file(cfg)
    retention_days = get_retention_days(cfg)

    if not disks:
        print("[\!] No disks configured. Edit argus.conf and add [disk:alias] sections.")
        return 1

    sample = collect_sample(disks)

    if dry_run:
        print(json.dumps(sample, indent=2))
        print(f"\n[dry-run] NOT written. Would go to: {history_file}")
        return 0

    history = load_history(history_file)
    history["samples"].append(sample)
    save_history(history, history_file, retention_days)

    n = len(history["samples"])
    print(f"[argus-collector] sample #{n} saved → {history_file}")
    for alias, data in sample["disks"].items():
        if "error" in data:
            print(f"  {alias}: ERROR ({data['error']})")
        else:
            temp    = data["attrs"].get("temperature_celsius", {}).get("raw", "?")
            realloc = data["attrs"].get("reallocated_sector_ct", {}).get("raw", "?")
            pending = data["attrs"].get("current_pending_sector", {}).get("raw", "?")
            print(f"  {alias}: temp={temp}°C  realloc={realloc}  pending={pending}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
