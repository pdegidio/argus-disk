# 👁️ Argus — Monitor Predittivo per la Salute dei Dischi

> Monitoring SMART che ti dice *quando* un disco si guasterà, non solo *che* si sta guastando. Soglie calibrate su dati Backblaze, forecast con regressione lineare, supporto enclosure DAS. Nessun cloud. Nessun abbonamento.

\![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
\![Python](https://img.shields.io/badge/Python-3.10+-blue)
\![Prometheus](https://img.shields.io/badge/Metriche-Prometheus-red)

---

## Perché Argus?

La maggior parte dei monitor SMART ti dice che un disco ha settori rilocati. Argus ti dice che il disco raggiungerà la soglia critica **tra 47 giorni** — e ti manda una notifica prima che succeda.

- **Predittivo** — regressione lineare su 30 giorni di storico SMART per prevedere i guasti in anticipo
- **Compatibile con DAS** — supporto nativo per JMicron JMB576 pass-through (TerraMaster D5-300 e simili)
- **Calibrato su Backblaze** — soglie basate sui dati reali di Backblaze, non sui default del produttore
- **Nessun cloud** — tutto gira sul tuo hardware. I dati non lasciano mai la tua rete.

---

## Cos'è Argus?

Argus è un demone di monitoring SMART per sistemi Linux homelab. Raccoglie attributi SMART ogni 6 ore, costruisce uno storico rolling di 180 giorni, e analizza i trend per darti un segnale precoce di guasti imminenti.

- **Raccolta SMART** ogni 6h su tutti i dischi configurati
- **Forecast con regressione lineare** — prevede i giorni al raggiungimento della soglia critica
- **Rilevamento anomalie di temperatura** — basato su z-score, cattura eventi termici
- **Alert ntfy** — notifiche al cambio di status con routing per priorità
- **Metriche Prometheus** — health score per disco, giorni al forecast, temperatura, contatori settori
- **Supporto enclosure DAS** — pass-through JMicron JMB576 per enclosure multi-bay

---

## Architettura

```
┌──────────────────────────────────────────────────┐
│                  Il tuo Homelab                  │
│                                                  │
│  /dev/sd*  ──► argus-collector ──► history.json  │
│  Slot DAS          (cron 6h)                     │
│                        │                         │
│               argus-analyzer ──► argus-watcher   │
│               (motore forecast)    (cron 30m)    │
│                                        │         │
│                                   alert ntfy     │
│                                        │         │
│               argus-exporter ──► Prometheus      │
│               (porta 9193)        └──► Grafana   │
└──────────────────────────────────────────────────┘
```

| Script | Descrizione |
|---|---|
| `argus-collector.py` | Raccoglie attributi SMART, li aggiunge allo storico JSON |
| `argus-analyzer.py` | Analizza lo storico, produce status e forecast |
| `argus-watcher.py` | Esegue l'analisi ogni 30 min, invia notifiche al cambio di stato |
| `argus-exporter.py` | Exporter metriche Prometheus (porta 9193) |

---

## Requisiti

- Host Linux (Debian 12 / Ubuntu 22.04+ raccomandato)
- Python 3.10+
- `smartmontools` (`sudo apt install smartmontools`)
- Istanza ntfy (opzionale, per gli alert)
- Prometheus + Grafana (opzionale, per le dashboard)

**Testato su:** Debian 12.5, smartctl 7.3, Python 3.11

---

## Avvio Rapido

**In breve:** clona → `bash install.sh` → aggiungi i tuoi dischi ad `argus.conf` → pronto in ~10 minuti.

### 1. Clona il repository

```bash
git clone https://github.com/pdegidio/argus-disk.git
cd argus-disk
```

### 2. Esegui l'installer

```bash
bash install.sh
```

### 3. Aggiungi i tuoi dischi alla configurazione

```bash
sudo nano /opt/argus/config/argus.conf
```

```ini
[disk:mio-ssd]
device = /dev/sda
type   = sat
class  = ssd

[disk:mio-hdd]
device = /dev/sdb
type   = sat
class  = hdd
```

Per enclosure DAS con JMicron JMB576 (es. TerraMaster D5-300):

```ini
[disk:das-slot1]
device = /dev/sdb
type   = jmb39x,0
class  = hdd
```

### 4. Prima raccolta

```bash
python3 /opt/argus/scripts/argus-collector.py
```

### 5. Controlla lo status

```bash
python3 /opt/argus/scripts/argus-analyzer.py
```

---

## Passare a Cortex

Argus monitora i tuoi dischi. **[Cortex](https://github.com/pdegidio/cortex-homelab)** monitora l'intero stack homelab — container Docker, servizi *arr, analisi log via LLM locale.

Entrambi sono progettati per girare insieme sulla stessa macchina senza conflitti.

Disponibile su: **[paolodegidio.gumroad.com/l/cortex-homelab](https://paolodegidio.gumroad.com/l/cortex-homelab)**

---

## Licenza

MIT — usalo, modificalo, distribuiscilo. L'attribuzione è apprezzata ma non obbligatoria.
