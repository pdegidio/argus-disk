# Changelog

All notable changes to Argus will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/)

## [Unreleased]

### Planned
- Grafana dashboard (health score, forecast timeline, temp history)
- Weekly summary digest via ntfy
- NVMe-specific attributes (media errors, unsafe shutdowns)
- HTTP API endpoint for local dashboard integration

## [1.0.0] — 2026-04-17

### Added
- `argus-collector.py` — SMART collection every 6h via cron, config-driven disk list
- `argus-analyzer.py` — per-disk status (OK/WARNING/CRITICAL), linear regression forecast,
  temperature anomaly detection (z-score), Backblaze-calibrated thresholds
- `argus-exporter.py` — Prometheus metrics exporter (port 9193), zero stdlib dependencies
- `argus-watcher.py` — 30-min alert loop, status-change detection, ntfy routing
- `argus.conf.example` — fully documented config with disk type reference
- `install.sh` — interactive installer, auto disk discovery via smartctl --scan
- `docs/argus-exporter.service` — hardened systemd unit
- Support for DAS enclosures with JMicron JMB576 (TerraMaster D5-300, etc.)
- Support for SSD wear indicators (SanDisk perc_avail_resrvd_space, program/erase fail)
- `--dry-run` flag on collector and watcher
- `--json` flag on analyzer for machine-readable output
- `--once` flag on exporter for one-shot metric dump

[Unreleased]: https://github.com/pdegidio/argus-disk/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/pdegidio/argus-disk/releases/tag/v1.0.0
