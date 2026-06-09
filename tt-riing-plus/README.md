# Thermaltake Riing Plus — Linux Control Software

Vollständige Fan- & RGB-Steuerung für **Thermaltake RGB Controller** unter Linux (Pop!_OS / Ubuntu).

## Features

- **HID-basiert** (kein pyusb / libusb nötig) — kommuniziert über `/dev/hidraw*`
- **Thermaltake Riing Plus** (PID 0x1fa5) und kompatible Controller
- **8 Modi:** Flow, Spectrum, Ripple, Blink, Pulse, Wave, Per-LED, Full
- **4 Geschwindigkeitsstufen:** Extreme, Fast, Normal, Slow
- **PWM Lüftersteuerung** (0–100 %)
- **5 Kanále** mit je bis zu 12 RGB-LEDs
- **Echtzeit-Vorschau** der LED-Ringe im GUI
- **Automatische Controller-Erkennung** via udev
- **Dark Mode** UI

## Installation

```bash
# Dateien aus dem Repo herunterladen (github.com/bjk201/hermes, Ordner tt-riing-plus/)
chmod +x *.sh
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

## Autostart einrichten

Damit die App beim Systemstart automatisch läuft:

```bash
# Service-Datei erstellen
sudo cp tt-riing-plus.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tt-riing-plus.service
sudo systemctl start tt-riing-plus.service

# Status prüfen
sudo systemctl status tt-riing-plus.service
```

> **Voraussetzung:** Das Display-Environment muss verfügbar sein (der User muss eingeloggt sein). Der Service nutzt `graphical-session.target`.

## Deinstallation

```bash
chmod +x uninstall.sh
./uninstall.sh
```

## Unterstützte Controller (automatisch erkannt via VID 0x264a)

| PID | Gerät |
|-----|-------|
| `0x1fa5` | Riing Plus |
| `0x1fa6` | Riing Plus (2. Controller / Hub) |
| `0x206b` | Riing Trio |
| `0x2070` | Riing Quad |
| `0x206e` | Flo 360 (AIO) |
| `0x206c` | TOUGHRGB |

## Troubleshooting

| Problem | Lösung |
|---------|--------|
| `Permission denied /dev/hidraw*` | udev-Regel erstellen (siehe unten) |
| App startet im Demo-Modus | Controller eingesteckt? USB-Kabel OK? |
| LEDs reagieren nicht | Diagnose-Button → Log prüfen |
| Kein DISPLAY | `DISPLAY=:0 ./tt-riing-plus.sh` |

### udev-Regel für nicht-root Zugriff

```bash
sudo tee /etc/udev/rules.d/99-thermaltake.rules << 'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="264a", MODE="0666"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
```

## Technische Details

- **Protokoll:** HID Reports über `/dev/hidraw*`
- **Init:** `[Report-ID 0x00][0xFE][0x33]`
- **RGB:**  `[Report-ID 0x00][0x32][0x52][port][mode+speed][GRB LED-Daten]`
- **Fan:**  `[Report-ID 0x00][0x32][0x51][port][percent]`
- **Paketgröße:** 65 Bytes (1 Report-ID + 64 Daten)
- **Abhängigkeiten:** PyQt5 (pip in venv), Python 3.10+
- **Log:** `~/.config/tt-riing-plus/tt-riing-plus.log`

## Lizenz

MIT
