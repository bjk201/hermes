#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Install-Script für Thermaltake Riing Plus Control
# Pop!_OS 24.04 / Ubuntu 24.04
# ─────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "🛠  Installiere System-Abhängigkeiten..."
sudo apt update

# PyQt5 — Paketname je nach Distro unterschiedlich
PYQT_EXTRA=""
for pkg in python3-pyqt5.qtwidgets python3-pyqt5.qtquick; do
    if apt-cache show "$pkg" &>/dev/null 2>&1; then
        PYQT_EXTRA="$PYQT_EXTRA $pkg"
    fi
done

sudo apt install -y \
    python3-pyqt5 \
    libusb-1.0-0-dev \
    python3-pip \
    python3-venv \
    $PYQT_EXTRA

echo ""
echo "🐍  Erstelle Virtual Environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "   ✅ venv erstellt: $VENV_DIR"
else
    echo "   ✅ venv existiert bereits"
fi

echo "🐍  Installiere Python-Pakete im venv..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install pyusb
echo "   ✅ pyusb installiert"

echo ""
echo "📋  Schreibe udev-Regel für non-root USB-Zugriff..."
sudo tee /etc/udev/rules.d/99-thermaltake.rules << 'UDEVEOF'
# Thermaltake RGB Controllers (alle PIDs)
SUBSYSTEM=="usb", ATTR{idVendor}=="264a", MODE="0666"
UDEVEOF

sudo udevadm control --reload
sudo udevadm trigger

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Installation abgeschlossen!"
echo ""
echo "Starten mit:"
echo "   cd $SCRIPT_DIR"
echo "   ./tt-riing-plus.sh"
echo ""
echo "Oder manuell:"
echo "   source $VENV_DIR/bin/activate"
echo "   python3 tt_riing_plus.py"
echo ""
echo "Headless Diagnose:"
echo "   python3 tt_riing_plus.py --diag"
echo ""
echo "🔌 Stecke den Thermaltake Controller per USB ein falls noch nicht geschehen."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"