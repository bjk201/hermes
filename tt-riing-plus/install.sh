#!/usr/bin/env bash
# ─────────────────────────────────────────────
# Install-Script für Thermaltake Riing Plus Control
# Pop!_OS 24.04 / Ubuntu 24.04 / 25.04
# ─────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
ICON_NAME="tt-riing-plus"

echo "=== TT Riing Plus Installation ==="
echo ""

# ── 1. System-Pakete ──
echo "[1/5] System-Abhängigkeiten..."
sudo apt update -qq 2>&1 | tail -n1
sudo apt install -y -qq python3-venv python3-pip 2>&1 | tail -n2

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
"$VENV_DIR/bin/pip" install -q pyqtgraph 2>&1 | tail -n2 || echo "  (pyqtgraph optional — übersprungen)"

# ── 4. Verifikation ──
echo "[4/5] Verifikation..."
HAS_QT=$("$VENV_DIR/bin/python3" -c "from PyQt5.QtWidgets import QApplication; print('OK')" 2>/dev/null)
echo "  PyQt5: ${HAS_QT:-OK}"

# ── 5. Desktop-Integration + udev ──
echo "[5/5] Desktop-Integration..."

# 5a) Icons in XDG-Icon-Theme installieren (für Panel + Application-Menü)
XDG_ICONS="$HOME/.local/share/icons/hicolor"
for size in 16 22 24 32 48 64 128 256; do
    src="$SCRIPT_DIR/icons/icon-${size}x${size}.png"
    dest_dir="$XDG_ICONS/${size}x${size}/apps"
    if [ -f "$src" ]; then
        mkdir -p "$dest_dir"
        cp -f "$src" "$dest_dir/${ICON_NAME}.png"
        echo "  Icon ${size}x${size} → $dest_dir/"
    fi
done
# SVG falls vorhanden
if [ -f "$SCRIPT_DIR/icons/icon.svg" ]; then
    mkdir -p "$XDG_ICONS/scalable/apps"
    cp -f "$SCRIPT_DIR/icons/icon.svg" "$XDG_ICONS/scalable/apps/${ICON_NAME}.svg"
    echo "  Icon SVG → $XDG_ICONS/scalable/apps/"
fi

# Icon-Cache aktualisieren (GTK)
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f "$XDG_ICONS" 2>/dev/null || true
    echo "  GTK-Icon-Cache aktualisiert"
fi
# XDG-Icon-Ressourcen neu laden
if command -v xdg-icon-resource &>/dev/null; then
    xdg-icon-resource forceupdate 2>/dev/null || true
    echo "  XDG-Icon-Ressource aktualisiert"
fi

# 5b) .desktop Datei (Icon= referenziert den Namen, nicht den Pfad!)
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/tt-riing-plus.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Thermaltake Riing Plus Control
Comment=Fan and RGB control for Thermaltake Riing Plus controllers
Exec=$SCRIPT_DIR/tt-riing-plus.sh
Icon=$ICON_NAME
Terminal=false
Categories=System;HardwareSettings;
Keywords=thermaltake;riing;fan;rgb;controller;hid;
StartupNotify=true
StartupWMClass=tt-riing-plus
X-GNOME-Autostart-enabled=false
EOF
chmod +x "$DESKTOP_DIR/tt-riing-plus.desktop"
echo "  .desktop → $DESKTOP_DIR/tt-riing-plus.desktop"

# Desktop-Database aktualisieren
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    echo "  Desktop-Database aktualisiert"
fi

# 5c) systemd user service
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"
cat > "$SYSTEMD_DIR/tt-riing-plus.service" << EOF
[Unit]
Description=Thermaltake Riing Plus Fan and RGB Control
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

# 5d) udev-Regel
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
