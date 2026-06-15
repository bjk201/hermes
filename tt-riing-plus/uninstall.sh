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
DESKTOP_FILE="$HOME/.local/share/applications/tt-riing-plus.desktop"
SYSTEMD_SERVICE="$HOME/.config/systemd/user/tt-riing-plus.service"

echo "🗑  Deinstalliere tt-riing-plus..."
echo ""

# 0) systemd service stop + disable
if [ -f "$SYSTEMD_SERVICE" ]; then
    echo "📋  Stoppe & deaktiviere systemd service..."
    systemctl --user stop tt-riing-plus.service 2>/dev/null || true
    systemctl --user disable tt-riing-plus.service 2>/dev/null || true
    rm -f "$SYSTEMD_SERVICE"
    echo "   ✅ Service entfernt"
fi

# 1) .desktop file
if [ -f "$DESKTOP_FILE" ]; then
    echo "📋  Entferne .desktop: $DESKTOP_FILE"
    rm -f "$DESKTOP_FILE"
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$(dirname "$DESKTOP_FILE")" 2>/dev/null || true
    fi
    echo "   ✅ .desktop entfernt"
else
    echo "   — Keine .desktop vorhanden, übersprungen"
fi

# 2) udev-Regel entfernen
if [ -f "$UDEV_RULE" ]; then
    echo "📋  Entferne udev-Regel: $UDEV_RULE"
    sudo rm -f "$UDEV_RULE"
    sudo udevadm control --reload 2>/dev/null || true
    sudo udevadm trigger 2>/dev/null || true
    echo "   ✅ Regel entfernt"
else
    echo "   — Keine udev-Regel vorhanden, übersprungen"
fi

# 3) Virtual Environment entfernen
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

# 4) Config-Verzeichnis (Log, Profiles, Descriptions)
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

echo ""
echo "✅ Deinstallation abgeschlossen!"
