#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# PV-Amortisations-Rechner — Ein-Klick-Installation
# 
# Ausführen im Terminal deines Alpine-Servers (SSH oder Portainer Exec):
#
#   wget https://raw.githubusercontent.com/bjk201/hermes/feature/pv-rechner/portainer-setup.sh
#   chmod +x portainer-setup.sh
#   ./portainer-setup.sh
# ═══════════════════════════════════════════════════════════════

set -e

DEPLOY_DIR="/opt/pv-rechner"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  PV-Amortisations-Rechner Installation       ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── 1. Abhängigkeiten prüfen ──────────────────────────────────
echo "→ Prüfe Abhängigkeiten..."

if ! command -v docker &> /dev/null; then
    echo "❌ Docker nicht gefunden. Bitte zuerst installieren:"
    echo "   apk add docker"
    echo "   rc-update add docker boot"
    echo "   service docker start"
    exit 1
fi

# docker-compose oder docker compose
if command -v docker-compose &> /dev/null; then
    COMPOSE="docker-compose"
elif docker compose version &> /dev/null 2>&1; then
    COMPOSE="docker compose"
else
    echo "❌ Docker Compose nicht gefunden. Bitte zuerst installieren:"
    echo "   apk add docker-compose"
    exit 1
fi

echo "   ✅ Docker gefunden"
echo "   ✅ Docker Compose gefunden"

# ── 2. Verzeichnis anlegen ─────────────────────────────────────
echo ""
echo "→ Erstelle Verzeichnis $DEPLOY_DIR ..."
mkdir -p "$DEPLOY_DIR"
cd "$DEPLOY_DIR"

# ── 3. Code herunterladen ──────────────────────────────────────
echo ""
echo "→ Lade Code von GitHub herunter..."

# Temporäres Verzeichnis für Download
TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

DOWNLOAD_URL="https://codeload.github.com/bjk201/hermes/zip/refs/heads/feature/pv-rechner"

if command -v wget &> /dev/null; then
    wget -q "$DOWNLOAD_URL" -O "$TMP_DIR/code.zip"
elif command -v curl &> /dev/null; then
    curl -sL "$DOWNLOAD_URL" -o "$TMP_DIR/code.zip"
else
    echo "❌ Weder wget noch curl gefunden. Bitte eines installieren:"
    echo "   apk add wget"
    exit 1
fi

# Entpacken
cd "$TMP_DIR"
unzip -q code.zip
EXTRACTED_DIR=$(find . -maxdepth 1 -name "hermes-*" -type d | head -1)

if [ -z "$EXTRACTED_DIR" ]; then
    echo "❌ Fehler: ZIP-Inhalt nicht wie erwartet"
    exit 1
fi

# In Deploy-Verzeichnis kopieren (außer .env)
cd "$DEPLOY_DIR"
if [ -f ".env" ]; then
    cp .env .env.backup
fi

cp -r "$TMP_DIR/$EXTRACTED_DIR/"* .

if [ -f ".env.backup" ]; then
    mv .env.backup .env
fi

echo "   ✅ Code erfolgreich geladen"

# ── 4. .env Datei ──────────────────────────────────────────────
echo ""
if [ ! -f ".env" ]; then
    echo "→ Erstelle .env Datei..."

    # Zufällige Passwörter generieren
    if command -v openssl &> /dev/null; then
        DB_PASS=$(openssl rand -hex 12)
        SECRET_KEY=*** -hex 32)
    else
        DB_PASS=$(head -c 16 /dev/urandom | head -c 32)
        SECRET_KEY=*** -c 32 /dev/urandom | head -c 64)
    fi

    cat > .env << ENVEOF
POSTGRES_DB=pvrechner
POSTGRES_USER=pvuser
POSTGRES_PASSWORD=***...ll)
echo ""
    echo "⚠️  WICHTIG: Bitte .env Datei anpassen!"
    echo ""
    echo "   Aktuell gesetzte Werte:"
    echo "   - POSTGRES_PASSWORD: $DB_PASS"
    echo "   - SECRET_KEY:        $SECRET_KEY"
    echo "   - APP_PASSWORD:      pv2024 (bitte ändern!)"
    echo ""
    echo "   Zum Ändern:"
    echo "   vi $DEPLOY_DIR/.env"
    echo ""
    read -p ".env jetzt automatisch mit sicherem APP_PASSWORD generieren? [J/n] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo "   OK, .env bleibt wie oben. Bitte später manuell ändern!"
    else
        APP_PASS=$(openssl rand -hex 8)
        sed -i "s/APP_PASSWORD=pv2024/APP_PASSWORD=$APP_PASS/" .env
        echo "   ✅ APP_PASSWORD gesetzt: $APP_PASS"
        echo "      (Notiere dir dieses Passwort!)"
    fi
else
    echo "   ✅ .env bereits vorhanden (nicht überschrieben)"
fi

# ── 5. Docker Compose starten ──────────────────────────────────
echo ""
echo "→ Starte Container..."
echo "$COMPOSE up -d --build"

$COMPOSE up -d --build

# ── 6. Warten auf DB ───────────────────────────────────────────
echo ""
echo "→ Warte auf Datenbank..."
for i in $(seq 1 30); do
    if docker exec pv-rechner-db-1 pg_isready -U pvuser -d pvrechner &> /dev/null; then
        echo "   ✅ Datenbank bereit"
        break
    fi
    sleep 2
done

# ── 7. Fertig ──────────────────────────────────────────────────
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "SERVER-IP")

echo ""
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ✅ Installation abgeschlossen!                          ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║                                                          ║"
echo "║  App erreichbar auf:                                     ║"
echo "║  http://$SERVER_IP:3333                    ║"
echo "║                                                          ║"
echo "║  Login: APP_PASSWORD aus .env                            ║"
echo "║                                                          ║"
echo "║  Nach dem Login:                                         ║"
echo "║  1. → HA-Einstellungen                                   ║"
echo "║  2. HA URL: http://192.168.1.103:8123                    ║"
echo "║  3. Long-Lived Access Token eintragen                    ║"
echo "║  4. Sensor-Entity-IDs zuweisen                           ║"
echo "║  5. Verbindung testen → History importieren             ║"
echo "║                                                          ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Stack-Dateien in: $DEPLOY_DIR"
echo ""
echo "Nützliche Befehle:"
echo "  cd $DEPLOY_DIR && $COMPOSE logs -f    # Logs anzeigen"
echo "  cd $DEPLOY_DIR && $COMPOSE restart     # Neustart"
echo "  cd $DEPLOY_DIR && $COMPOSE down       # Stoppen"
echo ""
