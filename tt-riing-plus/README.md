# Thermaltake Riing Plus — Linux Fan & RGB Control

Steuerung von Thermaltake Riing Plus Lüftern und RGB-LEDs unter Linux (Pop!_OS, Ubuntu, etc.).

## Features

- **Bis zu 5 Kanäle pro Controller** (bei 2 Controllern = 10 Kanäle, automatisch erkannt)
- **PWM-Geschwindigkeit 0–100 %** pro Kanal
- **RGB-Farben pro Kanal** mit Echtzeit-Vorschau der Ring-LEDs
- **RGB-Effekte:** Static, Flow, Spectrum, Ripple, Blink, Pulse, Wave, Per-LED
- **LED-Helligkeitsregler** pro Kanal (0–100 %)
- **Kanalbeschreibung** pro Kanal (z.B. „CPU Radiator", „Front Fans", „Top LED Stripe") — wird gespeichert und beim Start wiederhergestellt
- **Automatische Controller-Erkennung** (alle bekannten PIDs)
- **Multi-Controller-Support** (RGB + Hub gleichzeitig, beide im Header angezeigt)
- **Profile:** Speichern und Laden von Fan + RGB + Helligkeit-Einstellungen (Kanalbeschreibungen bleiben erhalten)
- **Auto-Modus:** Temperaturbasierte Lüftersteuerung mit wählbarem Sensor (benötigt `psutil`)
- **Live-Graph:** Temperatur + Lüftergeschwindigkeit über Zeit (benötigt `pyqtgraph`, optional)
- **System-Integration:** `.desktop`-Eintrag, systemd User-Service für Auto-Start

## Unterstützte Controller

| PID    | Name                  |
|--------|-----------------------|
| 0x1fa5 | Riing Plus (RGB)      |
| 0x1fa6 | Riing Plus (Hub/Fan)  |
| 0x206e | Flo 360               |
| 0x206c | TOUGHRGB              |
| 0x206b | Riing Trio            |
| 0x2070 | Riing Quad            |

## Voraussetzungen

- Python ≥ 3.10
- Linux mit X11 oder Wayland
- USB-Zugriff auf den Controller (wird über udev-Regel eingerichtet)

## Installation

### Schnellinstallation (empfohlen)

```bash
cd /tmp && rm -rf hermes && git clone https://github.com/bjk201/hermes.git && cp -r hermes/tt-riing-plus ~/tt-riing-plus && cd ~/tt-riing-plus && bash install.sh
```

Das Installationsskript:
1. Installiert System-Abhängigkeiten (`python3-venv`, `python3-pip`)
2. Erstellt ein Virtual Environment (`.venv`)
3. Installiert Python-Pakete (`PyQt5`, `hidapi`, `psutil`)
4. Richtet eine udev-Regel für USB-Zugriff ohne root ein
5. Installiert `.desktop`-Eintrag (erscheint im Anwendungsmenü)
6. Installiert optionalen systemd User-Service für Auto-Start

### Manuelle Installation

```bash
cd ~/tt-riing-plus
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### udev-Regel manuell (falls install.sh nicht als root läuft)

```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="264a", MODE="0666"' | sudo tee /etc/udev/rules.d/99-thermaltake.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

## Starten

```bash
cd ~/tt-riing-plus
./tt-riing-plus.sh
```

Nach der Installation auch verfügbar über:
- **Anwendungsmenü:** „Thermaltake Riing Plus Control"
- **Auto-Start aktivieren:** `systemctl --user enable --now tt-riing-plus.service`

## Diagnose (Controller-Erkennung testen)

```bash
cd ~/tt-riing-plus
./tt-riing-plus.sh --diag
```

Zeit alle gefundenen HID-Geräte und ob der Controller geöffnet werden kann.

## Deinstallation

```bash
cd ~/tt-riing-plus
bash uninstall.sh
```

Entfernt udev-Regel, `.desktop`-Eintrag, systemd-Service und optional venv + Config.

## Hinweise

- **Farbe** funktioniert nur im „Static"-Effekt. Andere Effekte (Flow, Spectrum etc.) verwenden ihre eigenen Farben.
- **PWM-Werte unter ~20 %** können Lüfter stoppen lassen (Hardware-Limit).
- **Controller-Status auslesen:** Das Thermaltake-Protokoll ist write-only. Die App zeigt beim Start Standardwerte (50 %) an. Tatsächliche Werte auf dem Controller können nicht ausgelesen werden.
- **Kanalbeschreibungen** werden in `~/.config/tt-riing-plus/channel_descriptions.json` gespeichert und sind unabhängig von Profilen.
- **Profile** werden in `~/.config/tt-riing-plus/profiles.json` gespeichert.
- **Multi-Controller:** RGB-Controller (0x1fa5) und Hub (0x1fa6) werden automatisch erkannt und zusammengeführt. Im Header werden beide angezeigt.
- **Auto-Modus:** Regelt die Lüfter nur wenn aktiv. Der gewählte Sensor wird mit ◀ markiert. Die aktuelle Temperatur und Fan-Speed werden live angezeigt.
- **Live-Graph** benötigt `pyqtgraph` (optional): `.venv/bin/pip install pyqtgraph`

## Projektstruktur

```
tt-riing-plus/
├── tt_riing_plus.py        # Hauptdatei: GUI + Controller-Kommunikation
├── tt_features.py          # Profile, Auto-Modus, History, Channel-Descriptions
├── tt-riing-plus.sh        # Start-Script (venv + Python)
├── install.sh              # Installation (venv, udev, .desktop, systemd)
├── uninstall.sh            # Deinstallation
├── tt-riing-plus.desktop   # XDG Desktop Entry
├── Makefile                # Packaging-Helfer (deb-Build vorbereitet)
├── requirements.txt        # Python-Abhängigkeiten
└── README.md               # Diese Datei
```

## Technische Details

- **Protokoll:** Basierend auf OpenRGB `ThermaltakeRiingController.cpp` + `chestm007/linux_thermaltake_riing`
- **Backend:** hidapi (kein pyusb)
- **GUI:** PyQt5
- **Fan-Paket:** `[0x32, 0x51, port, 0x01, speed]` + Commit `[0x32, 0x53]`
- **RGB-Paket:** `[0x00, 0x32, 0x52, port, mode+speed, GRB…]` (65 Bytes)
- **Mode-Werte:** FLOW=0x00, SPECTRUM=0x04, RIPPLE=0x08, BLINK=0x0C, PULSE=0x10, WAVE=0x14, BY_LED=0x18, STATIC=0x19
