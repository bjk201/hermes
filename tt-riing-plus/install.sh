#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Install-Script für Thermaltake Riing Plus Control
# Pop!_OS 24.04 / Ubuntu 24.04 / 25.04
# ─────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "=== TT Riing Plus Installation ==="
echo ""

# ── 1. System-Pakete (braucht sudo) ──
echo "[1/3] Installiere System-Abhängigkeiten..."
sudo apt update -qq 2>&1 | tail -1
sudo apt install -y -qq python3-venv python3-pip 2>&1 | tail -2

# ── 2. Virtual Environment + Python-Pakete (KEIN sudo!) ──
echo "[2/3] Erstelle Virtual Environment..."
if [ -d "$VENV_DIR" ]; then
    echo "  (venv existiert bereits)"
else
    python3 -m venv "$VENV_DIR"
fi

echo "  Installiere PyQt5..."
"$VENV_DIR/bin/pip" install -q --upgrade pip 2>&1 | tail -1
"$VENV_DIR/bin/pip" install -q PyQt5 2>&1 | tail -2

# ── 3. Verifikation ──
echo "[3/3] Verifikation..."
HAS_QT=$("$VENV_DIR/bin/python3" \
    -c "from PyQt5.QtWidgets import QApplication; print('OK')")
echo "  PyQt5: $HAS_QT"

if [ "$HAS_QT" != "OK" ]; then
    echo ""
    echo "❌ Fehler bei der Installation — siehe oben"
    exit 1
fi

# ── 4. udev-Regel (braucht sudo) ──
UDEV_FILE="/etc/udev/rules.d/99-thermaltake.rules"
if [ ! -f "$UDEV_FILE" ]; then
    echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="264a", MODE="0666"' \
        | sudo tee "$UDEV_FILE" > /dev/null
    sudo udevadm control --reload-rules 2>/dev/null
    sudo udevadm trigger 2>/dev/null
    echo "  udev-Regel erstellt"
else
    echo "  udev-Regel existiert bereits"
fi

echo ""
echo "✅ Installation erfolgreich!"
echo ""
echo "Nächste Schritte:"
echo "  1. chmod +x tt-riing-plus.sh"
echo "  2. ./tt-riing-plus.sh"
