#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Uninstall-Script für Thermaltake Riing Plus Control
# Pop!_OS 24.04 / Ubuntu 24.04
# ─────────────────────────────────────────────
set -euo pipefail

APP_NAME="tt-riing-plus"
UDEV_RULE="/etc/udev/rules.d/99-thermaltake.rules"
CONFIG_DIR="$HOME/.config/$APP_NAME"
LOG_FILE="$CONFIG_DIR/${APP_NAME}.log"

echo "🗑  Deinstalliere ${APP_NAME}..."
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

# 2) Config-Verzeichnis (Log, etc.)
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

# 3) System-Pakete (nur wenn nichts anderes sie braucht)
echo ""
echo "ℹ️  Installierte Pakete können entfernt werden, falls nichts anderes sie benötigt:"
echo "     sudo apt remove --purge python3-pyqt5 libusb-1.0-0-dev"
echo "     pip3 uninstall pyusb"
echo "   (Übersprungen — sicherheitshalber nicht automatisch entfernt)"

echo ""
echo "✅ Deinstallation abgeschlossen!"
