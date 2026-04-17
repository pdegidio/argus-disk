#\!/usr/bin/env python3
"""
Argus — Predictive Disk Health Monitor
argus-analyzer.py: Analyses SMART history and produces per-disk status,
linear regression forecasts, and human-readable warnings.

Usage:
  python3 argus-analyzer.py [--json] [--config /path/to/argus.conf]
  from argus_analyzer import analyze_all  # library use

https://github.com/pdegidio/argus-disk
"""

import json
import sys
import configparser
import statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_CONFIG = Path("/opt/argus/config/argus.conf")

def get_config_path() -> Path:
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--config" and i < len(sys.argv):
            return Path(sys.argv[i + 1])
    return DEFAULT_CONFIG

def load_config(config_path: Path = None) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg_file = config_path or DEFAULT_CONFIG
    if cfg_file.exists():
        cfg.read(cfg_file)
    return cfg

def get_history_file(cfg: configparser.ConfigParser) -> Path:
    return Path(cfg.get("argus", "history_file",
                        fallback="/var/lib/argus/argus-history.json"))

FORECAST_WINDOW_DAYS  = 30
FORECAST_HORIZON_DAYS = 180
MIN_SAMPLES_FOR_FORECAST = 5

# ─── Thresholds (Backblaze-calibrated) ───────────────────────────────────
# seek_error_rate intentionally omitted: Seagate packs seek totals in upper 32 bits,
# making cross-vendor comparison unreliable. Backblaze doesn't use it either.
# udma_crc_error_count warn=5 (not 1): a single historical CRC on multi-year disks
# is physiological. Growth is what matters — the forecast captures it.
HDD_THRESHOLDS = {
    "reallocated_sector_ct":  {"warn": 1,  "crit": 10},
    "current_pending_sector": {"warn": 1,  "crit": 1},
    "offline_uncorrectable":  {"warn": 1,  "crit": 1},
    "reported_uncorrect":     {"warn": 1,  "crit": 10},
    "command_timeout":        {"warn": 1,  "crit": 10},
    "udma_crc_error_count":   {"warn": 5,  "crit": 100},
    "spin_retry_count":       {"warn": 1,  "crit": 1},
    "temperature_celsius":    {"warn": 50, "crit": 55},
    "airflow_temp_celsius":   {"warn": 50, "crit": 55},
}

SSD_THRESHOLDS = {
    "reallocated_sector_ct":  {"warn": 1,  "crit": 10},
    "udma_crc_error_count":   {"warn": 1,  "crit": 100},
    "program_fail_count":     {"warn": 1,  "crit": 5},
    "erase_fail_count":       {"warn": 1,  "crit": 5},
    "reported_uncorrect":     {"warn": 1,  "crit": 10},
    "end_to_end_error":       {"warn": 1,  "crit": 1},
    "thermal_throttle":       {"warn": 1,  "crit": 10},
    "temperature_celsius":    {"warn": 60, "crit": 70},
}

SSD_DECREASING = {
    "perc_avail_resrvd_space": {"warn": 50, "crit": 20},
}

def thresholds_for(disk_class: str) -> tuple:
    if disk_class == "ssd":
        return SSD_THRESHOLDS, SSD_DECREASING
    return HDD_THRESHOLDS, {}

# ─── Linear regression forecast ──────────────────────────────────────────
def linear_forecast(points: list, target: float) -> float | None:
    if len(points) < MIN_SAMPLES_FOR_FORECAST:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    n = len(points)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    slope = num / den
    if slope <= 0:
        return None
    intercept = mean_y - slope * mean_x
    x_target = (target - intercept) / slope
    x_now = max(xs)
    days_until = x_target - x_now
    if days_until <= 0:
        return 0.0
    if days_until > FORECAST_HORIZON_DAYS:
        return None
    return round(days_until, 1)

# ─── Temperature anomaly detection ───────────────────────────────────────
def temp_anomaly(series: list) -> dict | None:
    if len(series) < 10:
        return None
    recent = series[-1]
    past = series[:-1]
    mu = statistics.mean(past)
    sd = statistics.pstdev(past) or 1.0
    z = (recent - mu) / sd
    if abs(z) >= 2.0:
        return {"current": recent, "mean": round(mu, 1), "z": round(z, 2)}
    return None

# ─── Per-disk analysis ────────────────────────────────────────────────────
def extract_series(samples: list, alias: str, attr: str) -> list:
    out = []
    if not samples:
        return out
    t0 = datetime.fromisoformat(samples[0]["ts"])
    for s in samples:
        disk = s["disks"].get(alias)
        if not disk or "error" in disk:
            continue
        val = disk.get("attrs", {}).get(attr, {}).get("raw")
        if val is None:
            continue
        ts = datetime.fromisoformat(s["ts"])
        days = (ts - t0).total_seconds() / 86400.0
        out.append((days, float(val)))
    return out

def analyze_disk(alias: str, samples: list) -> dict:
    result = {
        "alias": alias, "status": "UNKNOWN", "warnings": [],
        "forecasts": {}, "current": {}, "model": None,
        "serial": None, "class": None,
    }
    latest = None
    for s in reversed(samples):
        d = s["disks"].get(alias)
        if d and "error" not in d:
            latest = d
            break
    if latest is None:
        result["status"] = "NO_DATA"
        result["warnings"].append(f"{alias}: no valid sample found")
        return result

    result["model"]  = latest.get("model")
    result["serial"] = latest.get("serial")
    result["class"]  = latest.get("class", "hdd")
    inc_thresh, dec_thresh = thresholds_for(result["class"])

    cutoff = datetime.now(timezone.utc) - timedelta(days=FORECAST_WINDOW_DAYS)
    window = [s for s in samples if datetime.fromisoformat(s["ts"]) > cutoff]

    status_level = 0

    for attr, th in inc_thresh.items():
        raw = latest.get("attrs", {}).get(attr, {}).get("raw")
        if raw is None:
            continue
        result["current"][attr] = raw
        if raw >= th["crit"]:
            result["warnings"].append(f"🔴 {attr}={raw} ≥ CRITICAL ({th['crit']})")
            status_level = max(status_level, 2)
        elif raw >= th["warn"]:
            result["warnings"].append(f"🟡 {attr}={raw} ≥ WARN ({th['warn']})")
            status_level = max(status_level, 1)
        series = extract_series(window, alias, attr)
        if raw < th["crit"]:
            days = linear_forecast(series, th["crit"])
            if days is not None:
                result["forecasts"][attr] = {
                    "days_to_critical": days, "current": raw, "target": th["crit"],
                }
                if days < 30:
                    result["warnings"].append(f"⚠️  {attr}: forecast {days}d to critical")
                    status_level = max(status_level, 1)

    for attr, th in dec_thresh.items():
        raw  = latest.get("attrs", {}).get(attr, {}).get("raw")
        norm = latest.get("attrs", {}).get(attr, {}).get("norm")
        val  = norm if norm is not None else raw
        if val is None:
            continue
        result["current"][attr] = val
        if val <= th["crit"]:
            result["warnings"].append(f"🔴 {attr}={val} ≤ CRITICAL ({th['crit']})")
            status_level = max(status_level, 2)
        elif val <= th["warn"]:
            result["warnings"].append(f"🟡 {attr}={val} ≤ WARN ({th['warn']})")
            status_level = max(status_level, 1)

    for temp_key in ("temperature_celsius", "airflow_temp_celsius"):
        series = [p[1] for p in extract_series(window, alias, temp_key)]
        anomaly = temp_anomaly(series)
        if anomaly:
            result["warnings"].append(
                f"🌡️  temp anomaly: {anomaly['current']}°C "
                f"(mean {anomaly['mean']}°C, z={anomaly['z']})"
            )
            status_level = max(status_level, 1)
            break

    result["status"] = ["OK", "WARNING", "CRITICAL"][status_level]
    return result

def health_score(disk_analysis: dict) -> int:
    status = disk_analysis["status"]
    base = {"OK": 100, "WARNING": 70, "CRITICAL": 20, "NO_DATA": 50, "UNKNOWN": 50}[status]
    penalty = min(len(disk_analysis["warnings"]) * 5, 40)
    return max(0, base - penalty)

# ─── Public API ───────────────────────────────────────────────────────────
def analyze_all(history_file: Path = None, cfg: configparser.ConfigParser = None) -> dict:
    if cfg is None:
        cfg = load_config()
    if history_file is None:
        history_file = get_history_file(cfg)

    if not history_file.exists():
        return {"error": f"history file not found: {history_file}", "disks": {}}

    history = json.loads(history_file.read_text())
    samples = history.get("samples", [])
    if not samples:
        return {"error": "history empty — has argus-collector run yet?", "disks": {}}

    aliases = set()
    for s in samples:
        aliases.update(s.get("disks", {}).keys())

    result = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_samples": len(samples),
        "window_days": FORECAST_WINDOW_DAYS,
        "disks": {},
        "overall_status": "OK",
    }
    worst = 0
    for alias in sorted(aliases):
        d = analyze_disk(alias, samples)
        d["health_score"] = health_score(d)
        result["disks"][alias] = d
        lvl = {"OK": 0, "WARNING": 1, "CRITICAL": 2,
               "NO_DATA": 0, "UNKNOWN": 0}[d["status"]]
        worst = max(worst, lvl)
    result["overall_status"] = ["OK", "WARNING", "CRITICAL"][worst]
    return result

def print_text_summary(analysis: dict) -> None:
    print(f"👁️  Argus SMART Analysis — {analysis.get('ts', 'n/a')}")
    print(f"   Samples: {analysis.get('n_samples', 0)} "
          f"(forecast window: {analysis.get('window_days', '?')}d)")
    print(f"   Overall: {analysis.get('overall_status', '?')}\n")
    if "error" in analysis:
        print(f"[\!] {analysis['error']}")
        return
    for alias, d in analysis["disks"].items():
        icon = {"OK": "✅", "WARNING": "🟡", "CRITICAL": "🔴",
                "NO_DATA": "❔", "UNKNOWN": "❔"}[d["status"]]
        print(f"{icon} {alias} ({d.get('model', '?')})  "
              f"health={d['health_score']}/100  status={d['status']}")
        for w in d.get("warnings", []):
            print(f"    {w}")
        for attr, fc in d.get("forecasts", {}).items():
            print(f"    📈 {attr}: {fc['current']}→{fc['target']} in {fc['days_to_critical']}d")
        print()

def main() -> int:
    as_json = "--json" in sys.argv
    cfg = load_config(get_config_path())
    analysis = analyze_all(cfg=cfg)
    if as_json:
        print(json.dumps(analysis, indent=2))
    else:
        print_text_summary(analysis)
    return {"OK": 0, "WARNING": 1, "CRITICAL": 2}.get(
        analysis.get("overall_status", "OK"), 0)

if __name__ == "__main__":
    sys.exit(main())
