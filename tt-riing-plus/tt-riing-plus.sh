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
    echo "⚠️  Virtual Environment nicht gefunden."
    echo "   Erstelle mit: cd $SCRIPT_DIR && ./install.sh"
    echo ""
    echo "   Oder einmalig ohne venv (nicht empfohlen):"
    echo "   pip3 install --break-system-packages pyusb"
    echo "   python3 $SCRIPT_DIR/tt_riing_plus.py"
    exit 1
fi

# venv aktivieren und App starten
source "$VENV_DIR/bin/activate"
cd "$SCRIPT_DIR"
python3 tt_riing_plus.py "$@"
