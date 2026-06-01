#!/bin/sh
# PV-Amortisations-Rechner — Installation
# ./portainer-setup.sh

DEPLOY_DIR="/opt/pv-rechner"

echo ""
echo "=== PV-Amortisations-Rechner Installation ==="
echo ""

# 1. Check dependencies
echo "[1/5] Pruefe Docker..."
if ! command -v docker >/dev/null 2>&1; then
    echo "FAIL: Docker nicht gefunden"
    exit 1
fi

if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
elif docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
else
    echo "FAIL: Docker Compose nicht gefunden"
    exit 1
fi
echo "  OK"

# 2. Create directory
echo "[2/5] Erstelle $DEPLOY_DIR ..."
mkdir -p "$DEPLOY_DIR"

# 3. Download code
echo "[3/5] Lade Code von GitHub..."
TMP_DIR="/tmp/pv-dl-$$"
mkdir -p "$TMP_DIR"

URL="https://codeload.github.com/bjk201/hermes/zip/refs/heads/feature/pv-rechner"

if command -v wget >/dev/null 2>&1; then
    wget -q "$URL" -O "$TMP_DIR/code.zip"
else
    curl -sL "$URL" -o "$TMP_DIR/code.zip"
fi

cd "$TMP_DIR"
unzip -q code.zip

# Find extracted folder
EXTRACTED=""
for d in hermes-*/; do
    EXTRACTED="$d"
    break
done

if [ -z "$EXTRACTED" ]; then
    echo "FAIL: ZIP-Inhalt nicht gefunden"
    exit 1
fi

# Move files to deploy dir (preserve .env if exists)
if [ -f "$DEPLOY_DIR/.env" ]; then
    mv "$DEPLOY_DIR/.env" "$TMP_DIR/.env.backup"
fi

cp -r "$EXTRACTED"* "$DEPLOY_DIR/"

rm -rf "$TMP_DIR"

if [ -f "$DEPLOY_DIR/.env.backup" ]; then
    mv "$DEPLOY_DIR/.env.backup" "$DEPLOY_DIR/.env"
fi

echo "  OK"

# 4. Create .env if needed
cd "$DEPLOY_DIR"

if [ ! -f ".env" ]; then
    echo "[4/5] Erstelle .env ..."

    if command -v openssl >/dev/null 2>&1; then
        DB_PASS="*** "$(openssl rand -hex 12)
        SECRET="*** "$(openssl rand -hex 32)
        APP_PASS="*** "$(openssl rand -hex 8)
    else
        DB_PASS="pv"$(date +%s)
        SECRET="sk"$(date +%s)$
        APP_PASS="app"$(date +%s)
    fi

    echo "POSTGRES_DB=pvrechner"    > .env
    echo "POSTGRES_USER=pvuser"    >> .env
    echo "POSTGRES_PASSWORD=$DB_PASS" >> .env
    echo "APP_PASSWORD=$APP_PASS"  >> .env
    echo "SECRET_KEY=$SECRET"      >> .env
    echo "APP_PORT=3333"           >> .env

    echo ""
    echo "  NOTIZE:"
    echo "  APP_PASSWORD = $APP_PASS"
    echo "  POSTGRES_PASSWORD = $DB_PASS"
    echo ""
else
    echo "[4/5] .env existiert bereits"
fi

# 5. Start containers
echo "[5/5] Starte Container..."
$COMPOSE up -d --build --remove-orphans

echo ""
echo "Warte auf Datenbank..."
for i in $(seq 1 30); do
    if $COMPOSE exec -T db pg_isready -U pvuser -d pvrechner >/dev/null 2>&1; then
        echo "  DB bereit"
        break
    fi
    echo -n "."
    sleep 2
done

# Done
SERVER_IP=$(ip -4 addr show 2>/dev/null | grep -o 'inet [0-9.]*' | grep -v 127.0.0.1 | head -1 | awk '{print $2}')

echo ""
echo "=================================="
echo "  FERTIG!"
echo "  App: http://$SERVER_IP:3333"
echo "  Login: APP_PASSWORD aus .env"
echo "=================================="
echo ""
echo "  Danach: -> HA-Einstellungen"
echo "  HA URL: http://192.168.1.103:8123"
echo ""
