#!/bin/sh
# ═══════════════════════════════════════════════════════════════
# PV-Amortisations-Rechner — Ein-Klick-Installation
# 
# Ausführen im Terminal:
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
echo "→ Pruefe Abhaengigkeiten..."

if ! command -v docker > /dev/null 2>&1; then
    echo "❌ Docker nicht gefunden."
    echo "   apk add docker && rc-update add docker boot && service docker start"
    exit 1
fi

if command -v docker-compose > /dev/null 2>&1; then
    COMPOSE="docker-compose"
elif docker compose version > /dev/null 2>&1; then
    COMPOSE="docker compose"
else
    echo "❌ Docker Compose nicht gefunden."
    echo "   apk add docker-compose"
    exit 1
fi

echo "   ✅ Docker + Compose gefunden"

# ── 2. Verzeichnis anlegen ─────────────────────────────────────
echo ""
echo "→ Erstelle $DEPLOY_DIR ..."
mkdir -p "$DEPLOY_DIR"

# ── 3. Code herunterladen ──────────────────────────────────────
echo ""
echo "→ Lade Code von GitHub herunter..."

TMP_DIR="/tmp/pv-download-$$"
mkdir -p "$TMP_DIR"

DOWNLOAD_URL="https://codeload.github.com/bjk201/hermes/zip/refs/heads/feature/pv-rechner"

if command -v wget > /dev/null 2>&1; then
    wget -q "$DOWNLOAD_URL" -O "$TMP_DIR/code.zip"
elif command -v curl > /dev/null 2>&1; then
    curl -sL "$DOWNLOAD_URL" -o "$TMP_DIR/code.zip"
else
    echo "❌ wget oder curl noetig: apk add wget"
    exit 1
fi

cd "$TMP_DIR"
unzip -q code.zip

# Entpackten Ordner finden
EXTRACTED=$(find . -maxdepth 1 -name "hermes-*" -type d | head -1)

mv "$EXTRACTED"/* "$DEPLOY_DIR/"

# Aufräumen
rm -rf "$TMP_DIR"

echo "   ✅ Code geladen nach $DEPLOY_DIR"

# ── 4. .env Datei ──────────────────────────────────────────────
cd "$DEPLOY_DIR"

if [ ! -f ".env" ]; then
    echo ""
    echo "→ Erstelle .env Datei..."

    if command -v openssl > /dev/null 2>&1; then
        DB_PASS=$(openssl rand -hex 12)
        SECRET_KEY=*** -hex 32)
        APP_PASS=$(openssl rand -hex 8)
    else
        DB_PASS="pvdb$(date +%s | tail -c 9)"
        SECRET_KEY="pvsecret$(date +%s%N | tail -c 16)"
        APP_PASS="pv$(date +%s | tail -c 7)"
    fi

    {
        echo "POSTGRES_DB=pvrechner"
        echo "POSTGRES_USER=pvuser"
        echo "POSTGRES_PASSWORD=***"
        echo "APP_PASSWORD=$APP_PASS"
        echo "SECRET_KEY=$SECRET_KEY"
        echo "APP_PORT=3333"
    } > .env

    echo ""
    echo "   ⚠️  Notiere dir diese Zugangsdaten:"
    echo ""
    echo "   DB_PASSWORD:  $DB_PASS"
    echo "   APP_PASSWORD: $APP_PASS"
    echo "   SECRET_KEY:   $SECRET_KEY"
    echo ""
    echo "   Zum Ändern: vi $DEPLOY_DIR/.env"
    echo ""
else
    echo ""
    echo "   ✅ .env bereits vorhanden"
fi

# ── 5. Container starten ───────────────────────────────────────
echo "→ Starte Container..."
$COMPOSE up -d --build --remove-orphans

# ── 6. Warten auf DB ───────────────────────────────────────────
echo ""
echo "→ Warte auf Datenbank..."
for i in $(seq 1 30); do
    if $COMPOSE exec -T db pg_isready -U pvuser -d pvrechner > /dev/null 2>&1; then
        echo "   ✅ Datenbank bereit"
        break
    fi
    printf "\r   ⏳ Warte... (%s/30)" "$i"
    sleep 2
done

# ── 7. Fertig ──────────────────────────────────────────────────
SERVER_IP=$(ip -4 addr show | grep -oP '(?<=inet\s)[\d.]+' | grep -v 127.0.0.1 | head -1)

echo ""
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ✅ Installation abgeschlossen!                          ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║                                                          ║"
echo "║  App: http://${SERVER_IP}:3333                            ║"
echo "║  Login: siehe APP_PASSWORD oben                          ║"
echo "║                                                          ║"
echo "║  Nach Login: → HA-Einstellungen                         ║"
echo "║  HA URL: http://192.168.1.103:8123                       ║"
echo "║                                                          ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Befehle:"
echo "  cd $DEPLOY_DIR && $COMPOSE logs -f    # Logs"
echo "  cd $DEPLOY_DIR && $COMPOSE restart     # Restart"
echo "  cd $DEPLOY_DIR && $COMPOSE down       # Stoppen"
echo ""
