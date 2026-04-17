#\!/usr/bin/env python3
"""
Argus — Predictive Disk Health Monitor
argus-watcher.py: Runs argus-analyzer every 30 minutes, compares current
status with previous, and sends ntfy alerts for status changes or new warnings.

https://github.com/pdegidio/argus-disk
"""

import json
import sys
import configparser
import requests
from datetime import datetime, timezone
from pathlib import Path

# Import analyzer as library
sys.path.insert(0, str(Path(__file__).parent))
from argus_analyzer import analyze_all, load_config, get_history_file

DEFAULT_CONFIG = Path("/opt/argus/config/argus.conf")

def get_config_path() -> Path:
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--config" and i < len(sys.argv):
            return Path(sys.argv[i + 1])
    return DEFAULT_CONFIG

def get_state_file(cfg: configparser.ConfigParser) -> Path:
    return Path(cfg.get("argus", "state_file",
                        fallback="/var/lib/argus/argus-state.json"))

def get_ntfy_config(cfg: configparser.ConfigParser) -> dict:
    return {
        "url":      cfg.get("ntfy", "url",      fallback=""),
        "topic":    cfg.get("ntfy", "topic",    fallback="argus-disk"),
        "token":    cfg.get("ntfy", "token",    fallback=""),
    }

def load_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text())
    except Exception:
        return {}

def save_state(state: dict, state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))

def send_ntfy(ntfy: dict, title: str, message: str, priority: str = "default",
              tags: list = None) -> None:
    if not ntfy["url"] or not ntfy["topic"]:
        print(f"[ntfy] not configured — would send: {title}")
        return
    url = f"{ntfy['url'].rstrip('/')}/{ntfy['topic']}"
    headers = {
        "Title":    title,
        "Priority": priority,
        "Tags":     ",".join(tags or ["floppy_disk"]),
    }
    if ntfy["token"]:
        headers["Authorization"] = f"Bearer {ntfy['token']}"
    try:
        r = requests.post(url, data=message.encode("utf-8"),
                          headers=headers, timeout=10)
        r.raise_for_status()
        print(f"[ntfy] sent: {title}")
    except Exception as e:
        print(f"[ntfy] failed: {e}", file=sys.stderr)

def status_priority(status: str) -> str:
    return {"OK": "low", "WARNING": "default", "CRITICAL": "urgent",
            "NO_DATA": "low", "UNKNOWN": "low"}.get(status, "default")

def check_and_alert(analysis: dict, prev_state: dict, ntfy: dict,
                    dry_run: bool = False) -> dict:
    new_state = {}
    overall = analysis.get("overall_status", "OK")

    for alias, disk in analysis.get("disks", {}).items():
        status = disk["status"]
        score  = disk.get("health_score", 100)
        prev   = prev_state.get(alias, {})
        prev_status = prev.get("status", "UNKNOWN")
        prev_warnings = set(prev.get("warnings", []))
        curr_warnings = set(disk.get("warnings", []))
        new_warnings  = curr_warnings - prev_warnings

        new_state[alias] = {
            "status":   status,
            "warnings": list(curr_warnings),
            "ts":       analysis["ts"],
        }

        # Alert on status change (to worse)
        status_rank = {"OK": 0, "WARNING": 1, "CRITICAL": 2,
                       "NO_DATA": -1, "UNKNOWN": -1}
        if (status_rank.get(status, 0) > status_rank.get(prev_status, 0)):
            icon = "🔴" if status == "CRITICAL" else "🟡"
            title = f"{icon} Argus: {alias} → {status}"
            body_lines = [
                f"Disk: {alias} ({disk.get('model', '?')})",
                f"Status: {prev_status} → {status}",
                f"Health score: {score}/100",
                "",
            ]
            body_lines += list(curr_warnings)[:8]
            if disk.get("forecasts"):
                body_lines.append("")
                for attr, fc in list(disk["forecasts"].items())[:3]:
                    body_lines.append(
                        f"📈 {attr}: {fc['current']}→{fc['target']} in {fc['days_to_critical']}d"
                    )
            message = "\n".join(body_lines)
            if not dry_run:
                send_ntfy(ntfy, title, message,
                          priority=status_priority(status),
                          tags=["warning" if status == "WARNING" else "rotating_light", "floppy_disk"])
            else:
                print(f"[dry-run] would send ntfy: {title}\n{message}\n")

        elif new_warnings:
            title = f"⚠️  Argus: new warning on {alias}"
            body_lines = [f"Disk: {alias} ({disk.get('model', '?')})"] + list(new_warnings)[:5]
            message = "\n".join(body_lines)
            if not dry_run:
                send_ntfy(ntfy, title, message, priority="default",
                          tags=["warning", "floppy_disk"])
            else:
                print(f"[dry-run] would send ntfy: {title}\n{message}\n")

    # Overall recovery alert
    prev_overall = prev_state.get("__overall__", {}).get("status", "UNKNOWN")
    if overall == "OK" and prev_overall in ("WARNING", "CRITICAL"):
        title = "✅ Argus: all disks healthy"
        message = "All monitored disks returned to OK status."
        if not dry_run:
            send_ntfy(ntfy, title, message, priority="low", tags=["white_check_mark"])
        else:
            print(f"[dry-run] would send ntfy: {title}")

    new_state["__overall__"] = {"status": overall, "ts": analysis["ts"]}
    return new_state

def main() -> int:
    dry_run = "--dry-run" in sys.argv
    cfg = load_config(get_config_path())
    ntfy = get_ntfy_config(cfg)
    state_file = get_state_file(cfg)

    print(f"[argus-watcher] running — {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    analysis = analyze_all(cfg=cfg)

    if "error" in analysis:
        print(f"[\!] analyzer error: {analysis['error']}")
        return 1

    prev_state = load_state(state_file)
    new_state  = check_and_alert(analysis, prev_state, ntfy, dry_run=dry_run)

    if not dry_run:
        save_state(new_state, state_file)

    # Print summary
    overall = analysis.get("overall_status", "?")
    icon = {"OK": "✅", "WARNING": "🟡", "CRITICAL": "🔴"}.get(overall, "❔")
    print(f"{icon} Overall: {overall}")
    for alias, d in analysis["disks"].items():
        disk_icon = {"OK": "✅", "WARNING": "🟡", "CRITICAL": "🔴",
                     "NO_DATA": "❔"}.get(d["status"], "❔")
        print(f"  {disk_icon} {alias}: {d['status']}  health={d.get('health_score',0)}/100")

    return {"OK": 0, "WARNING": 1, "CRITICAL": 2}.get(overall, 0)

if __name__ == "__main__":
    sys.exit(main())
