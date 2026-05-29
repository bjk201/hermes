# ☀️ PV-Amortisations-Rechner

Webapp zur Amortisationsberechnung einer PV-Anlage mit Home Assistant Energiedaten.

## Features

- **CSV-Import**: Home Assistant Energy Dashboard Export (stündlich → täglich aggregiert)
- **Stromtarife**: Arbeitspreis (ct/kWh) + Grundgebühr (€/Monat) mit Zeitraum
- **Einspeisevergütung**: Vergütungssatz (ct/kWh) mit Zeitraum
- **Kostenverwaltung**: Manuelle Kosteneinträge mit Kategorien (Anschaffung, Erweiterung, Wartung, etc.)
- **Amortisation**: Kumulierte Einnahmen vs. Kosten mit Chart.js-Diagramm
- **Dunkles Theme**, deutsche Sprache

## Quick Start (Docker)

```bash
# Repository klonen
git clone -b feature/pv-rechner https://github.com/bjk201/hermes.git pv-rechner
cd pv-rechner

# Umgebungsvariablen anpassen (optional)
cp .env.example .env

# Starten
docker compose up -d

# App erreichbar auf http://localhost:8080
# Standard-Passwort: pv2024
```

## Umgebungsvariablen

| Variable | Default | Beschreibung |
|---|---|---|
| `POSTGRES_DB` | pvrechner | Datenbank-Name |
| `POSTGRES_USER` | pvuser | DB-User |
| `POSTGRES_PASSWORD` | pvpass | DB-Passwort |
| `APP_PASSWORD` | pv2024 | Passwort für die Webapp |
| `APP_PORT` | 8080 | Port auf dem Host |
| `SECRET_KEY` | change-me | Flask Secret Key |

## CSV-Format

Die App erwartet Home Assistant Energy Export CSVs mit:
- Spalte 0: `entity_id` (z.B. `sensor.sma_wechselrichter_pv_gen_meter`)
- Spalte 1: `type` (z.B. `solar_production`, `calculated_consumed_solar`)
- Spalte 2: `unit` (z.B. `kWh`)
- Spalte 3+: ISO-Datumswerte (stündlich)

## Verarbeitete Sensoren

| Kategorie | Sensoren |
|---|---|
| PV-Produktion | `sma_wechselrichter_pv_gen_meter`, `victronsolarcharger_yield_today229/239` |
| Batterie | `speicher_basengreen_input/output` |
| Berechnet | `calculated_consumed_solar`, `calculated_consumed_grid`, `calculated_solar_to_grid`, `calculated_solar_to_battery`, `calculated_consumed_battery`, `calculated_consumption` |
