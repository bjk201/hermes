#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Uninstall-Script für Thermaltake Riing Plus Control
# Pop!_OS 24.04 / Ubuntu 24.04
# ─────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
UDEV_RULE="/etc/udev/rules.d/99-thermaltake.rules"
CONFIG_DIR="$HOME/.config/tt-riing-plus"
LOG_FILE="$CONFIG_DIR/tt-riing-plus.log"

echo "🗑  Deinstalliere tt-riing-plus..."
echo ""

# 1) udev-Regel entfernen
if [ -f "$UDEV_RULE" ]; then
    echo "📋  Entferne udev-Regel: $UDEV_RULE"
    sudo rm -f "$UDEV_RULE"
    sudo udevadm control --reload 2>/dev/null || true
    sudo udevadm trigger 2>/dev/null || true
    echo "   ✅ Regel entfernt"
else
    echo "   — Keine udev-Regel vorhanden, übersprungen"
fi

# 2) Virtual Environment entfernen
if [ -d "$VENV_DIR" ]; then
    read -p "📋  Virtual Environment '$VENV_DIR' löschen? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$VENV_DIR"
        echo "   ✅ venv gelöscht"
    else
        echo "   — venv behalten"
    fi
else
    echo "   — Kein venv vorhanden, übersprungen"
fi

# 3) Config-Verzeichnis (Log, etc.)
if [ -d "$CONFIG_DIR" ]; then
    read -p "📋  Config-Verzeichnis '$CONFIG_DIR' löschen? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$CONFIG_DIR"
        echo "   ✅ Config gelöscht"
    else
        echo "   — Config behalten"
    fi
else
    echo "   — Kein Config-Verzeichnis, übersprungen"
fi

# 4) System-Pakete (nur wenn nichts anderes sie braucht)
echo ""
echo "ℹ️  Installierte Pakete können entfernt werden, falls nichts anderes sie benötigt:"
echo "     sudo apt remove --purge python3-pyqt5 libusb-1.0-0-dev python3-venv"
echo "   (Übersprungen — sicherheitshalber nicht automatisch entfernt)"

echo ""
echo "✅ Deinstallation abgeschlossen!"
