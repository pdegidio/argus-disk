#\!/usr/bin/env python3
"""
Argus — Predictive Disk Health Monitor
argus-exporter.py: Prometheus metrics exporter on port 9193 (default).
Zero dependencies beyond stdlib + argus-analyzer.

Metrics exposed:
  argus_disk_health_score        (gauge, per disk)
  argus_disk_status              (gauge: 0=OK, 1=WARNING, 2=CRITICAL)
  argus_disk_temperature_celsius (gauge, per disk)
  argus_disk_power_on_hours      (gauge, per disk)
  argus_disk_reallocated_sectors (gauge, per disk)
  argus_disk_pending_sectors     (gauge, per disk)
  argus_disk_crc_errors          (gauge, per disk)
  argus_disk_forecast_days       (gauge, per disk+attribute — days to critical)
  argus_last_run_timestamp       (gauge)
  argus_last_run_age_seconds     (gauge)
  argus_overall_status           (gauge: 0=OK, 1=WARNING, 2=CRITICAL)
  argus_exporter_up              (gauge)
  argus_build_info               (gauge, labels)

https://github.com/pdegidio/argus-disk
"""

import sys
import json
import time
import configparser
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from argus_analyzer import analyze_all, load_config

DEFAULT_CONFIG = Path("/opt/argus/config/argus.conf")
VERSION = "1.0.0"

def get_config_path() -> Path:
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--config" and i < len(sys.argv):
            return Path(sys.argv[i + 1])
    return DEFAULT_CONFIG

def get_port(cfg: configparser.ConfigParser) -> int:
    return cfg.getint("prometheus", "port", fallback=9193)

STATUS_MAP = {"OK": 0, "WARNING": 1, "CRITICAL": 2, "NO_DATA": -1, "UNKNOWN": -1}

def build_metrics(cfg: configparser.ConfigParser) -> str:
    analysis = analyze_all(cfg=cfg)
    lines = []
    now = time.time()

    def g(name, val, labels="", help_text="", typ="gauge"):
        nonlocal lines
        if not any(f"# HELP {name}" in l for l in lines):
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {typ}")
        label_str = f"{{{labels}}}" if labels else ""
        lines.append(f"{name}{label_str} {val}")

    g("argus_exporter_up", 1, help_text="Argus exporter is running")
    g("argus_build_info", 1,
      labels=f'version="{VERSION}"',
      help_text="Argus build info")

    if "error" in analysis:
        g("argus_last_run_timestamp", 0, help_text="Unix timestamp of last collector run")
        g("argus_last_run_age_seconds", -1, help_text="Seconds since last collector run")
        g("argus_overall_status", -1, help_text="Overall disk fleet status")
        return "\n".join(lines) + "\n"

    # Timestamp from analysis
    try:
        ts = datetime.fromisoformat(analysis["ts"]).timestamp()
    except Exception:
        ts = now
    g("argus_last_run_timestamp", int(ts), help_text="Unix timestamp of last analyzer run")
    g("argus_last_run_age_seconds", int(now - ts), help_text="Seconds since last analyzer run")
    g("argus_overall_status", STATUS_MAP.get(analysis.get("overall_status", "UNKNOWN"), -1),
      help_text="Overall fleet status (0=OK, 1=WARNING, 2=CRITICAL)")

    for alias, disk in analysis.get("disks", {}).items():
        model  = (disk.get("model") or "unknown").replace('"', '')
        serial = (disk.get("serial") or "unknown").replace('"', '')
        base_labels = f'disk="{alias}",model="{model}",serial="{serial}"'

        g("argus_disk_health_score", disk.get("health_score", 0),
          labels=base_labels, help_text="Disk health score (0-100)")
        g("argus_disk_status", STATUS_MAP.get(disk.get("status", "UNKNOWN"), -1),
          labels=base_labels, help_text="Disk status (0=OK, 1=WARNING, 2=CRITICAL)")

        attrs = disk.get("current", {})
        simple_metrics = {
            "temperature_celsius":   "argus_disk_temperature_celsius",
            "power_on_hours":        "argus_disk_power_on_hours",
            "reallocated_sector_ct": "argus_disk_reallocated_sectors",
            "current_pending_sector":"argus_disk_pending_sectors",
            "udma_crc_error_count":  "argus_disk_crc_errors",
        }
        for attr_key, metric_name in simple_metrics.items():
            val = attrs.get(attr_key)
            if val is not None:
                g(metric_name, val, labels=base_labels)

        for fc_attr, fc in disk.get("forecasts", {}).items():
            fc_labels = f'{base_labels},attribute="{fc_attr}"'
            g("argus_disk_forecast_days", fc["days_to_critical"],
              labels=fc_labels,
              help_text="Days until attribute reaches critical threshold (linear forecast)")

    return "\n".join(lines) + "\n"


class MetricsHandler(BaseHTTPRequestHandler):
    cfg = None

    def do_GET(self):
        if self.path not in ("/metrics", "/"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            output = build_metrics(self.cfg)
            self.send_response(200)
            self.send_header("Content-Type",
                             "text/plain; version=0.0.4; charset=utf-8")
            self.end_headers()
            self.wfile.write(output.encode("utf-8"))
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def log_message(self, fmt, *args):
        pass  # silence access log


def main() -> int:
    cfg = load_config(get_config_path())
    port = get_port(cfg)
    once = "--once" in sys.argv

    if once:
        print(build_metrics(cfg))
        return 0

    MetricsHandler.cfg = cfg
    server = HTTPServer(("0.0.0.0", port), MetricsHandler)
    print(f"[argus-exporter] listening on :{port}/metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[argus-exporter] stopped")
    return 0

if __name__ == "__main__":
    sys.exit(main())
