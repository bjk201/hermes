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
echo "[1/5] Installiere System-Abhängigkeiten..."
sudo apt update -qq 2>&1 | tail -1
sudo apt install -y -qq python3-venv python3-pip 2>&1 | tail -2

# ── 2. Virtual Environment + Python-Pakete (KEIN sudo!) ──
echo "[2/5] Erstelle Virtual Environment..."
if [ -d "$VENV_DIR" ]; then
    echo "  (venv existiert bereits)"
else
    python3 -m venv "$VENV_DIR"
fi

echo "  Installiere PyQt5..."
"$VENV_DIR/bin/pip" install -q --upgrade pip 2>&1 | tail -1
"$VENV_DIR/bin/pip" install -q PyQt5 2>&1 | tail -2
"$VENV_DIR/bin/pip" install -q hidapi psutil 2>&1 | tail-2

# ── 3. Verifikation ──
echo "[3/5] Verifikation..."
HAS_QT=$("$VENV_DIR/bin/python3" \
    -c "from PyQt5.QtWidgets import QApplication; print('OK')")
echo "  PyQt5: $HAS_QT"

if [ "$HAS_QT" != "OK" ]; then
    echo ""
    echo "❌ Fehler bei der Installation — siehe oben"
    exit 1
fi

# ── 4. udev-Regel (braucht sudo) ──
echo "[4/5] udev-Regel..."
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

# ── 5. .desktop + systemd user service ──
echo "[5/5] Desktop-Integration..."

# .desktop file
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
sed "s|{INSTALL_DIR}|$SCRIPT_DIR|g" "$SCRIPT_DIR/tt-riing-plus.desktop" \
    > "$DESKTOP_DIR/tt-riing-plus.desktop"
chmod +x "$DESKTOP_DIR/tt-riing-plus.desktop"
echo "  .desktop → $DESKTOP_DIR"

# systemd user service (optional auto-start)
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
echo "  systemd user service → $SYSTEMD_DIR"

# Update desktop database
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

echo ""
echo "✅ Installation erfolgreich!"
echo ""
echo "Nächste Schritte:"
echo "  1. chmod +x $SCRIPT_DIR/tt-riing-plus.sh"
echo "  2. $SCRIPT_DIR/tt-riing-plus.sh"
echo ""
echo "Optional — Auto-Start aktivieren:"
echo "  systemctl --user enable tt-riing-plus.service"
echo "  systemctl --user start  tt-riing-plus.service"
echo ""
echo "Deinstallation:"
echo "  $SCRIPT_DIR/uninstall.sh"
