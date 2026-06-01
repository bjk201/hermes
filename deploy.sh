#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# PV-Amortisations-Rechner Deployment-Skript (ohne Git)
#
# Voraussetzungen:
# - Alpine Linux mit Docker + Portainer
# - wget oder curl installiert
#
# Usage:
#   1. Skript auf Server kopieren
#   2. chmod +x deploy.sh && ./deploy.sh
# ═══════════════════════════════════════════════════════════════

set -e

echo "=== PV-Amortisations-Rechner Deployment ==="

DEPLOY_DIR="/opt/pv-rechner"
mkdir -p "$DEPLOY_DIR"
cd "$DEPLOY_DIR"

# ── 1. Code als ZIP von GitHub laden ──
echo "→ Lade Code von GitHub herunter..."

# Bereinige altes Verzeichnis (außer .env)
if [ -d "app" ]; then
    echo "→ Entferne alten Code..."
    find . -maxdepth 1 ! -name ".env" ! -name "." -exec rm -rf {} +
fi

# ZIP herunterladen (codeload funktioniert ohne Git)
wget -q "https://codeload.github.com/bjk201/hermes/zip/refs/heads/feature/pv-rechner" -O code.zip
# oder: curl -L -o code.zip "https://codeload.github.com/bjk201/hermes/zip/refs/heads/feature/pv-rechner"

# Entpacken
unzip -q -o code.zip
# Entpacktes Verzeichnis: hermes-feature-pv-rechner/
mv hermes-feature-pv-rechner/* .
rm -rf hermes-feature-pv-rechner code.zip
rm -f deploy.sh  # Eigenes Skript aufräumen

echo "→ Code erfolgreich geladen."

# ── 2. .env Datei erstellen (falls nicht vorhanden) ──
if [ ! -f ".env" ]; then
    echo "→ Erstelle .env Datei..."

    # Zufällige Passwörter generieren (Alpine-kompatibel)
    DB_PASS=$(head -c 16 /dev/urandom | xxd -p 2>/dev/null || openssl rand -hex 12)
    SECRET_KEY=$(head -c 32 /dev/urandom | xxd -p 2>/dev/null || openssl rand -hex 32)

    cat > .env << ENVEOF
POSTGRES_DB=pvrechner
POSTGRES_USER=pvuser
POSTGRES_PASSWORD=CHANGE_ME_PLEASE_set-a-strong-password)
APP_PASSWORD=pv2024
SECRET_KEY=CHANGE_ME_PLEASE_eep-this-secret)
APP_PORT=3333
ENVEOF

    echo ""
    echo "⚠️  WICHTIG: Passe die .env Datei an!"
    echo "   vi .env"
    echo ""
    echo "   Mindestens ändern:"
    echo "   - POSTGRES_PASSWORD  (DB-Passwort)"
    echo "   - APP_PASSWORD       (Login-Passwort für die Webapp)"
    echo "   - SECRET_KEY         (beliebiger langer String)"
    echo ""
fi

# ── 3. Docker Compose starten ──
echo "→ Starte Docker Compose..."

# Prüfe ob docker-compose oder docker compose
if command -v docker-compose &> /dev/null; then
    docker-compose up -d --build
elif docker compose version &> /dev/null; then
    docker compose up -d --build
else
    echo ""
    echo "❌ Docker Compose nicht gefunden!"
    echo "   Installiere es zuerst:"
    echo "   apk add docker-compose"
    echo "   ODER: Verwende Portainer Stack GUI"
    echo ""
fi

echo ""
echo "✅ Deployment abgeschlossen!"
echo ""
echo "App erreichbar auf: http://$(hostname -I | awk '{print $1}'):3333"
echo "Login mit APP_PASSWORD aus .env"
echo ""
echo "Nach dem Login:"
echo "  1. → HA-Einstellungen"
echo "  2. HA URL: http://192.168.1.103:8123"
echo "  3. Long-Lived Access Token eintragen"
echo "  4. Sensor-Entity-IDs zuweisen"
echo "  5. Verbindung testen ✓"
