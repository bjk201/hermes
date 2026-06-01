#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# PV-Amortisations-Rechner Deployment-Skript
# 
# Voraussetzungen:
# - Alpine Linux mit Docker + Portainer
# - LXC muss Zugriff auf die Dateien haben
#
# Usage:
#   1. Dieses Skript auf dem Server ausführen
#   2. In Portainer → Stacks → Add Stack → web_upload → docker-compose.yml hochladen
#   3. Stack "pv-rechner" deployen
# ═══════════════════════════════════════════════════════════════

set -e

echo "=== PV-Amortisations-Rechner Deployment ==="

# ── 1. Verzeichnis anlegen ──
DEPLOY_DIR="/opt/pv-rechner"
mkdir -p "$DEPLOY_DIR"
cd "$DEPLOY_DIR"

# ── 2. Code von GitHub ziehen ──
if [ -d ".git" ]; then
    echo "→ Aktualisiere bestehendes Repo..."
    git pull origin feature/pv-rechner
else
    echo "→ Clone Repository..."
    git clone https://github.com/bjk201/hermes.git .
    git checkout feature/pv-rechner
fi

# ── 3. .env Datei erstellen (falls nicht vorhanden) ──
if [ ! -f ".env" ]; then
    echo "→ Erstelle .env Datei..."
    cat > .env << 'ENVEOF'
POSTGRES_DB=pvrechner
POSTGRES_USER=pvuser
POSTGRES_PASSWORD=PVDB_$(openssl rand -hex 12)
APP_PASSWORD=pv2024
SECRET_KEY=$(openssl rand -hex 32)
APP_PORT=3333
ENVEOF

    echo ""
    echo "⚠️  WICHTIG: Passe die .env Datei an!"
    echo "   nano .env"
    echo ""
    echo "   Mindestens ändern:"
    echo "   - APP_PASSWORD  (dein Login-Passwort für die Webapp)"
    echo "   - POSTGRES_PASSWORD  (DB-Passwort)"
    echo ""
fi

# ── 4. .dockerignore erstellen ──
cat > .dockerignore << 'DOCKERIGNORE'
.git
.gitignore
.env.example
*.md
.dockerignore
DOCKERIGNORE

echo ""
echo "=== Deployment vorbereitet in $DEPLOY_DIR ==="
echo ""
echo "Stack jetzt in Portainer starten:"
echo "  1. Portainer öffnen → Stacks → Add Stack"
echo "  2. Name: pv-rechner"
echo "  3. Build method: Repository"
echo "     - Repository URL: https://github.com/bjk201/hermes.git"
echo "     - Branch: feature/pv-rechner"
echo "  4. Environment variables: (aus .env)"
echo "  5. Deploy the stack"
echo ""
echo "ODER lokal per Docker Compose:"
echo "  cd $DEPLOY_DIR && docker-compose up -d --build"
