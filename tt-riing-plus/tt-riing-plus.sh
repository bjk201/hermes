#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Start-Script für Thermaltake Riing Plus Control
# Aktiviert venv und startet die App
# ─────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Prüfen ob venv existiert
if [ ! -d "$VENV_DIR" ]; then
    echo "⚠️  Virtual Environment nicht gefunden: $VENV_DIR"
    echo ""
    echo "Erstelle zuerst mit:"
    echo "  cd $SCRIPT_DIR"
    echo "  bash install.sh"
    exit 1
fi

# venv aktivieren und App starten
source "$VENV_DIR/bin/activate"
cd "$SCRIPT_DIR"
exec python3 tt_riing_plus.py "$@"