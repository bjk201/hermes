#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Install-Script für Thermaltake Riing Plus Control
# Pop!_OS 24.04 / Ubuntu 24.04
# ─────────────────────────────────────────────
set -euo pipefail

echo "🛠  Installiere System-Abhängigkeiten..."
sudo apt update
sudo apt install -y \
    python3-pyqt5 \
    python3-pyqt5.qtwidgets \
    libusb-1.0-0-dev \
    python3-pip

echo "🐍  Installiere Python-Pakete..."
pip3 install pyusb

echo "📋  Schreibe udev-Regel für non-root USB-Zugriff..."
sudo tee /etc/udev/rules.d/99-thermaltake.rules << 'UDEVEOF'
# Thermaltake RGB Controllers (alle PIDs)
# Unterstützt: Riing Plus, Riing Trio, Riing Quad, Flo 360, TOUGHRGB
SUBSYSTEM=="usb", ATTR{idVendor}=="264a", MODE="0666"
UDEVEOF

sudo udevadm control --reload
sudo udevadm trigger

echo ""
echo "✅ Fertig! Starte mit:"
echo "   cd $(dirname "$0")"
echo "   python3 tt_riing_plus.py"
echo ""
echo "🔌 Stecke den Thermaltake Controller per USB ein falls noch nicht geschehen."
