#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Start-Script für Thermaltake Riing Plus Control
# ─────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Prüfen ob venv existiert
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo "⚠️  Python venv nicht gefunden: $VENV_DIR/bin/python3"
    echo ""
    echo "Erstelle zuerst mit:"
    echo "  cd $SCRIPT_DIR && bash install.sh"
    exit 1
fi

# App starten MIT dem venv-Python (nicht System-Python!)
cd "$SCRIPT_DIR"
exec "$VENV_DIR/bin/python3" tt_riing_plus.py "$@"
