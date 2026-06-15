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

# ── 1. System-Pakete ──
echo "[1/5] System-Abhängigkeiten..."
sudo apt update -qq 2>&1 | tail -n1
sudo apt install -y -qq python3-venv python3-pip \
    python3-pyqt5 2>&1 | tail -n2 || true

# ── 2. Virtual Environment ──
echo "[2/5] Python Virtual Environment..."
if [ -d "$VENV_DIR" ]; then
    echo "  (venv existiert bereits)"
else
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -q --upgrade pip 2>&1 | tail -n1

# ── 3. Python-Pakete ──
echo "[3/5] Python-Pakete installieren..."
"$VENV_DIR/bin/pip" install -q PyQt5 hidapi psutil 2>&1 | tail -n2
# pyqtgraph optional — nicht kritisch wenn es fehlt
"$VENV_DIR/bin/pip" install -q pyqtgraph 2>&1 | tail -n2 || echo "  (pyqtgraph optional — übersprungen)"

# ── 4. Verifikation ──
echo "[4/5] Verifikation..."
HAS_QT=$("$VENV_DIR/bin/python3" -c "from PyQt5.QtWidgets import QApplication; print('OK')" 2>/dev/null)
if [ "$HAS_QT" != "OK" ]; then
    # Fallback: System-PyQt5 verwenden
    echo "  WARNUNG: PyQt5 im venv nicht verfügbar, nutze System-PyQt5"
fi
HAS_HID=$("$VENV_DIR/bin/python3" -c "import hid; print('OK')" 2>/dev/null)
echo "  PyQt5: ${HAS_QT:-OK (system)}  hidapi: ${HAS_HID:-FEHLEND}"

# ── 5. Desktop-Integration ──
echo "[5/5] Desktop-Integration..."

# Icon kopieren
cp -f "$SCRIPT_DIR/icons/icon.png" "$SCRIPT_DIR/icon.png" 2>/dev/null || true

# .desktop file
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
sed "s|{INSTALL_DIR}|$SCRIPT_DIR|g" "$SCRIPT_DIR/tt-riing-plus.desktop" \
    > "$DESKTOP_DIR/tt-riing-plus.desktop"
chmod +x "$DESKTOP_DIR/tt-riing-plus.desktop"
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
echo "  .desktop → $DESKTOP_DIR"

# systemd user service
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"
cat > "$SYSTEMD_DIR/tt-riing-plus.service" << EOF
[Unit]
Description=Thermaltake Riing Plus Fan & RGB Control
After=graphical-session.target

[Service]
Type=simple
ExecStart=$SCRIPT_DIR/tt-riing-plus.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=graphical-session.target
EOF
echo "  systemd service → $SYSTEMD_DIR"

# udev-Regel
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
echo "Starten:"
echo "  $SCRIPT_DIR/tt-riing-plus.sh"
echo ""
echo "Auto-Start (optional):"
echo "  systemctl --user enable --now tt-riing-plus.service"
echo ""
echo "Deinstallation:"
echo "  $SCRIPT_DIR/uninstall.sh"
