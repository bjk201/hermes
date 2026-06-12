# Thermaltake Riing Plus — Linux Fan & RGB Control

Steuerung von Thermaltake Riing Plus Lüftern und RGB-LEDs unter Linux (Pop!_OS, Ubuntu, etc.).

## Features

- Bis zu 5 Kanäle pro Controller (bei 2 Controllern = 10 Kanäle, automatisch erkannt)
- PWM-Geschwindigkeit 0–100 %
- RGB-Farben pro Kanal
- RGB-Effekte: Static, Flow, Spectrum, Ripple, Blink, Pulse, Wave, Per-LED
- LED-Helligkeitsregler pro Channel (0–100%)
- Echtzeit-Vorschau der Ring-LEDs
- Automatische Controller-Erkennung (alle bekannten PIDs)
- Multi-Controller-Support (RGB + Hub gleichzeitig)
- Auto-Modus: Temperatur-basierte Lüftersteuerung (benötigt psutil)
- Live-Graph: Temperatur + Lüftergeschwindigkeit über Zeit (benötigt pyqtgraph)
- Profile: Speichern/Laden von Fan+RGB-Einstellungen

## Unterstützte Controller

| PID | Name |
|-----|------|
| 0x1fa5 | Riing Plus (RGB) |
| 0x1fa6 | Riing Plus (Hub/Fan) |
| 0x206e | Flo 360 |
| 0x206c | TOUGHRGB |
| 0x206b | Riing Trio |
| 0x2070 | Riing Quad |

## Installation (aktuellste Version)

```bash
cd /tmp && rm -rf hermes && git clone https://github.com/bjk201/hermes.git && cp -r hermes/tt-riing-plus ~/Downloads/ && cd ~/Downloads/tt-riing-plus && bash install.sh
```

## Starten

```bash
cd ~/Downloads/tt-riing-plus
./tt-riing-plus.sh
```

## Diagnose (Controller-Erkennung testen)

```bash
cd ~/Downloads/tt-riing-plus
./tt-riing-plus.sh --diag
```

## Deinstallation

```bash
cd ~/Downloads/tt-riing-plus
bash uninstall.sh
```

## Hinweise

- Funktioniert ohne root, wenn der User in der `dialout`-Gruppe ist (wird bei install.sh automatisch hinzugefügt)
- Farbe funktioniert nur im "Static"-Effekt
- Werte unter ~20% PWM können Lüfter stoppen lassen
- Auto-Modus benötigt `psutil` (wird bei install.sh installiert)
- Live-Graph benötigt `pyqtgraph` (optional: `pip3 install pyqtgraph`)
- Multi-Controller: RGB-Controller (0x1fa5) und Hub (0x1fa6) werden automatisch erkannt und zusammengeführt
