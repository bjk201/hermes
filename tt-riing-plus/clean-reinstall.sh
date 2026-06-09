#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Clean Reinstall — löscht alles und neu installiert
# Thermaltake Riing Plus Control
# ─────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
UDEV_RULE="/etc/udev/rules.d/99-thermaltake.rules"

echo "=== TT Riing Plus — Clean Reinstall ==="
echo ""

# ── 1. Altes venv löschen ──
echo "[1/5] Entferne altes Virtual Environment..."
if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    echo "  ✅ venv gelöscht"
else
    echo "  — Kein venv vorhanden"
fi

# ── 2. System-Pakete prüfen ──
echo "[2/5] Prüfe System-Abhängigkeiten..."
for pkg in python3-venv python3-pip libusb-1.0-0-dev; do
    if dpkg -s "$pkg" &>/dev/null; then
        echo "  ✅ $pkg"
    else
        echo "  ⬇  Installiere $pkg..."
        sudo apt install -y -qq "$pkg" 2>&1 | tail -2
    fi
done

# ── 3. Neues venv erstellen + Pakete installieren ──
echo "[3/5] Erstelle frisches Virtual Environment..."
python3 -m venv "$VENV_DIR"
echo "  Installiere PyQt5 + pyusb..."
"$VENV_DIR/bin/pip" install -q --upgrade pip 2>&1 | tail -1
"$VENV_DIR/bin/pip" install -q PyQt5 pyusb 2>&1 | tail -2

# ── 4. Verifikation ──
echo "[4/5] Verifikation..."
HAS_QT=$("$VENV_DIR/bin/python3" \
    -c "from PyQt5.QtWidgets import QApplication; print('OK')")
HAS_USB=$("$VENV_DIR/bin/python3" \
    -c "import usb.core; print('OK')")
echo "  PyQt5: $HAS_QT"
echo "  pyusb:  $HAS_USB"

if [ "$HAS_QT" != "OK" ] || [ "$HAS_USB" != "OK" ]; then
    echo ""
    echo "❌ Fehler bei der Installation — siehe oben"
    exit 1
fi

# ── 5. udev-Regel (einmalig) ──
echo "[5/5] Prüfe udev-Regel..."
if [ ! -f "$UDEV_RULE" ]; then
    echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="264a", MODE="0666"' \
        | sudo tee "$UDEV_RULE" > /dev/null
    sudo udevadm control --reload-rules 2>/dev/null
    sudo udevadm trigger 2>/dev/null
    echo "  ✅ udev-Regel erstellt"
else
    echo "  ✅ udev-Regel existiert bereits"
fi

# ── chmod ──
chmod +x "$SCRIPT_DIR/tt-riing-plus.sh" 2>/dev/null || true

echo ""
echo "✅ Clean Reinstall abgeschlossen!"
echo ""
echo "App starten mit:"
echo "  cd $SCRIPT_DIR && ./tt-riing-plus.sh"
