# Thermaltake Riing Plus — Linux Control Software

Vollständige Fan- & RGB-Steuerung für den **Thermaltake Riing Plus RGB Controller** unter Linux (Pop!_OS 24.04 / Ubuntu 24.04).

![Python](https://img.shields.io/badge/Python-3.8+-blue) ![Qt](https://img.shields.io/badge/GUI-PyQt5-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

- 🌀 **PWM-Lüftersteuerung** (0–100 %) pro Kanal mit Presets (Silent/Normal/Performance/Full)
- 🌈 **RGB-Lichtsteuerung** mit 7 Effekten: Static, Breathing, Wave, Ripple, Pulse, Spectrum Cycle, Rainbow Wave
- 🔄 **Live-Vorschau** der 12-LED-Ringe direkt in der GUI
- ⚡ **5 Kanäle** unterstützt (bis zu 5 Lüfter pro Kanal)
- 🔍 **USB-Diagnose** — automatische Hardware-Prüfung + Log-Ausgabe
- 📋 **Live-Logging** — farbcodiertes Log-Fenster + Datei-Log
- 💾 **Dark-Theme** UI
- 🔌 **Automatische Controller-Erkennung** — 5 bekannte PIDs

## Installation (empfohlen)

Das Install-Script nutzt ein **Virtual Environment** (venv) — kein System-Python nötig, kein `--break-system-packages`.

```bash
chmod +x install.sh tt-riing-plus.sh
./install.sh
```

## Start

```bash
# Eins-Click-Start (aktiviert venv automatisch):
./tt-riing-plus.sh

# Oder manuell:
source .venv/bin/activate
python3 tt_riing_plus.py

# Headless USB-Diagnose (ohne GUI):
python3 tt_riing_plus.py --diag
```

> **Wichtig:** Bei `pip3 install pyusb` Fehler wegen `externally-managed-environment` — einfach die `install.sh` nutzen, die macht automatisch ein venv.

## Deinstallation

```bash
chmod +x uninstall.sh
./uninstall.sh
```

Entfernt: udev-Regel, Virtual Environment, Config/Log (mit Bestätigung).

## Hardware

```
[Mainboard USB] ←→ [Thermaltake RGB Controller] ←→ [Riing Plus Lüfter 1..N]
                                   ↓ max 5 Kanäle
                           12 LEDs pro Lüfter Ring
```

**Unterstützte Controller (automatisch erkannt):**

| PID | Gerät |
|-----|-------|
| `0x1fa5` | Riing Plus |
| `0x206b` | Riing Trio |
| `0x2070` | Riing Quad |
| `0x206e` | Flo 360 (AIO) |
| `0x206c` | TOUGHRGB |

**USB-Protokoll:** HID/Control Transfer (pyusb)

## Troubleshooting

1. **❓ Hilfe-Button** — zeigt udev-Befehl und Einstellungen
2. **`./tt-riing-plus.sh`** — startet mit venv, falls `pyusb` nicht im System installiert war
3. **`python3 tt_riing_plus.py --diag`** — headless USB-Diagnose
4. **`~/.config/tt-riing-plus/tt-riing-plus.log`** — detailreich Log
5. **`🔍 Diagnose`** Button — USB-Bus-Scan mit Kernel-Driver-Status falls die App crasht

## Architektur

| Modul | Funktion |
|-------|----------|
| `TTController` | USB-Com, Packet-Init, `diagnose()` |
| `RingWidget` | LED-Ring Vorschau Widget |
| `ChannelControl` | Pro-Kanal UI (Speed, Effekt, Farbe) |
| `MainWindow` | Hauptfenster, Tabs, globale Aktionen |
| `LogWindow` | Live-Log-Viewer Dialog |
| `DiagnosticDialog` | USB-Diagnose Dialog |

## Lizenz

MIT — nutzbar, änderbar, weiterverbreitbar.

## Credits

- [OpenRGB](https://openrgb.org/) — Reverse-Engineered USB Protokoll
- [tt-rgb](https://github.com/thelinuxkid/tt-rgb) — Referenzimplementierung
- OWL für Bjk201 🐧
