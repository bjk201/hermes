# Thermaltake Riing Plus — Linux Control Software

Vollständige Fan- & RGB-Steuerung für **Thermaltake RGB Controller** unter Linux (Pop!_OS / Ubuntu).

## Installation

```bash
# 1. Berechtigungen setzen (einmalig)
chmod +x install.sh tt-riing-plus.sh clean-reinstall.sh

# 2. Installieren
bash install.sh
```

Oder **komplett sauber neu** (löscht alles alte):
```bash
bash clean-reinstall.sh
```

> **Wichtig:** `bash install.sh` ausführen (nicht `./install.sh`), weil das Script `sudo` für apt braucht.

## Start

```bash
./tt-riing-plus.sh
```

## Headless Diagnose (ohne GUI)

```bash
.venv/bin/python3 tt_riing_plus.py --diag
```

## Deinstallation

```bash
chmod +x uninstall.sh
./uninstall.sh
```

## Unterstützte Controller (automatisch erkannt)

| PID | Gerät |
|-----|-------|
| `0x1fa5` | Riing Plus |
| `0x1fa6` | Riing Plus (2. Controller / Hub) |
| `0x206b` | Riing Trio |
| `0x2070` | Riing Quad |
| `0x206e` | Flo 360 (AIO) |
| `0x206c` | TOUGHRGB |

## Troubleshooting

- **❓ Hilfe-Button** — udev-Befehl + Einstellungen
- **📋 Log-Button** — Live-Log mit Filter
- **🔍 Diagnose-Button** — USB-Bus-Scan
- **Log-Datei:** `~/.config/tt-riing-plus/tt-riing-plus.log`
- **USB-Endpoint:** Die App findet das korrekte OUT/IN-Endpoint automatisch (kein hardcoded 0x02 mehr)
- **Python 3.10–3.12:** Unterstützt — die App explizit mit venv-Python gestartet

## Lizenz

MIT
