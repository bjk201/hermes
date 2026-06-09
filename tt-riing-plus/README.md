# Thermaltake Riing Plus — Linux Control Software

Vollständige Fan- & RGB-Steuerung für den **Thermaltake Riing Plus RGB Controller** unter Linux (Pop!_OS 24.04 / Ubuntu 24.04).

![Python](https://img.shields.io/badge/Python-3.8+-blue) ![Qt](https://img.shields.io/badge/GUI-PyQt5-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

- 🌀 **PWM-Lüftersteuerung** (0–100 %) pro Kanal mit Presets (Silent/Normal/Performance/Full)
- 🌈 **RGB-Lichtsteuerung** mit 7 Effekten: Static, Breathing, Wave, Ripple, Pulse, Spectrum Cycle, Rainbow Wave
- 🔄 **Live-Vorschau** der 12-LED-Ringe direkt in der GUI
- ⚡ **5 Kanäle** unterstützt (bis zu 5 Lüfter pro Kanal)
- 💾 **Dark-Theme** UI

## HW-Setup

```
[Mainboard USB] ←→ [Thermaltake Riing Plus Controller] ←→ [Riing Plus Lüfter 1..N]
                                   ↓ max 5 Kanäle
                           12 LEDs pro Lüfter Ring
```

- **USB VID:PID:** `0x264a:0x1fa5`
- **USB-Protokoll:** HID/Control Transfer (pyusb)

## Installation

```bash
# Option A: Install-Script (empfohlen)
chmod +x install.sh
./install.sh

# Option B: Manuelle Installation
sudo apt install python3-pyqt5 libusb-1.0-0-dev
pip3 install pyusb

# udev-Regel für USB-Zugriff ohne root
sudo tee /etc/udev/rules.d/99-thermaltake.rules << 'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="264a", ATTR{idProduct}=="1fa5", MODE="0666"
EOF
sudo udevadm control --reload
sudo udevadm trigger
```

## Start

```bash
python3 tt_riing_plus.py
```

## Nutzung

1. **Tab wählen** → CH1 bis CH5
2. **Lüftergeschwindigkeit** per Slider oder Preset-Button
3. **RGB-Effekt** auswählen (farblich active nur bei "Static")
4. **Farbe wählen** (nur bei Static)
5. **"Auf Kanal anwenden"** oder **"Alle anwenden"**

> ⚠️ **Tipp:** Lüfter stoppen möglicherweise unter ~20 PWM-Stufe — hardwareabhängig.

## Architektur

| Modul              | Funktion                                              |
|--------------------|-------------------------------------------------------|
| `TTController`     | USB-Kommunikation, Packet-Building, Init             |
| `RingWidget`       | Qt Custom Widget — LED-Ring Vorschau                   |
| `ChannelControl`   | Steuerelemente pro Kanal (Speed, Effekt, Color)        |
| `MainWindow`       | Hauptfenster, Tab-Navigation, globale Aktionen         |

## Lizenz

MIT — nutzbar, änderbar, weiterverbreitbar.

## Credits

- [OpenRGB](https://openrgb.org/) — Reverse-Engineered USB Protokoll
- [tt-rgb](https://github.com/thelinuxkid/tt-rgb) — Referenzimplementierung
- OWL für Bjk201 🐧
