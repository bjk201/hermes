# Thermaltake Riing Plus — Linux Fan & RGB Control

Steuerung von Thermaltake Riing Plus Lüftern und RGB-LEDs unter Linux (Pop!_OS, Ubuntu, etc.).

## Features

- Bis zu 5 Kanäle (max 5 Lüfter pro Kanal)
- PWM-Geschwindigkeit 0–100 %
- RGB-Farben pro Kanal
- RGB-Effekte: Static, Breathing, Wave, Ripple, Pulse, Spectrum Cycle
- Echtzeit-Vorschau der Ring-LEDs
- Automatische Controller-Erkennung (5 bekannte PIDs)

## Unterstützte Controller

| PID | Name |
|-----|------|
| 0x1fa5 | Riing Plus |
| 0x1fa6 | Riing Plus (Hub) |
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

- Funktioniert ohne root, wenn der User im `dialout`-Gruppe ist (wird bei install.sh automatisch hinzugefügt)
- Farbe funktioniert nur im "Static"-Effekt
- Werte unter ~20% PWM können Lüfter stoppen lassen
