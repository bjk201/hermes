#!/usr/bin/env python3
"""
Thermaltake Riing Plus Linux Control Software
=============================================
Vollständige Lüftersteuerung inkl. PWM & RGB für Pop!_OS/Linux.

Features:
  - Bis zu 5 Kanäle (max 5 Lüfter pro Kanal)
  - PWM-Geschwindigkeit 0-100 %
  - RGB-Farben pro Kanal
  - RGB-Effekte: Static, Breathing, Wave, Ripple, Pulse, Spectrum Cycle
  - Echtzeit-Vorschau der Ring-LEDs

Hardware: Thermaltake Riing Plus RGB Controller (USB HID)
USB VID:PID = 0x264a:0x1fa5

Author: OWL für Bjk201
License: MIT
Version: 1.0.0
"""

import sys
import os
import struct
import time
import math
import threading
from functools import partial

try:
    import usb.core
    import usb.util
    HAS_USB = True
except ImportError:
    HAS_USB = False

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QSlider, QPushButton, QComboBox, QColorDialog,
        QGroupBox, QGridLayout, QSpinBox, QTabWidget, QStatusBar,
        QCheckBox, QFrame, QScrollArea, QMessageBox, QFileDialog
    )
    from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
    from PyQt5.QtGui import QColor, QPainter, QBrush, QPen, QFont, QPixmap, QIcon
    HAS_QT = True
except ImportError:
    HAS_QT = False

# ─────────────────────────────────────────────
#  USB Protocol Constants
# ─────────────────────────────────────────────
TT_VID = 0x264a
TT_PID = 0x1fa5    # Riing Plus Digital Controller

CMD_INIT1    = 0x28   # Init step 1 (magic init msg)
CMD_INIT2    = 0x29   # Init step 2 (get config: fan count etc.)
CMD_SETCOLOR = 0x22   # Set RGB frame
CMD_SETSPEED = 0x21   # Set PWM fan speed
CMD_SETMODE  = 0x23   # Set lighting mode/effect
CMD_APPLY    = 0x2b   # Push config to controller (apply)

MAX_CHANNELS = 5
LEDS_PER_FAN = 12    # Riing Plus = 12 LEDs ring

# ─────────────────────────────────────────────
#  Fan Speed Presets (PWM %)
# ─────────────────────────────────────────────
FAN_SPEED_PRESETS = {
    "Silent":     25,
    "Normal":     50,
    "Performance": 75,
    "Full":       100,
}

# ─────────────────────────────────────────────
#  RGB Effects
# ─────────────────────────────────────────────
RGB_EFFECTS = {
    0x00: "Static",
    0x01: "Breathing",
    0x02: "Wave",
    0x03: "Ripple",
    0x04: "Pulse",
    0x05: "Spectrum Cycle",
    0x06: "Rainbow Wave",
    0x07: "Reactive",
}

# Effect modes for CMD_SETMODE byte values
# Per-TT-docs: byte[5]=mode, byte[6]=direction, byte[7]=speed
MODE_STATIC   = 0x00
MODE_BREATH   = 0x01
MODE_WAVE     = 0x02
MODE_RIPPLE   = 0x03
MODE_PULSE    = 0x04
MODE_SPECTRUM = 0x05
MODE_RAINBOW  = 0x06
MODE_REACTIVE = 0x07

EFFECT_SPEED_MAP = {
    "Slow":     0x00,
    "Normal":   0x01,
    "Fast":     0x02,
}

# ─────────────────────────────────────────────
#  Utility
# ─────────────────────────────────────────────
def _checksum(data: bytes) -> int:
    """Thermaltake checksum = sum of all bytes & 0xFF."""
    return sum(data) & 0xFF


def build_packet(cmd: int, channel: int, payload: bytes) -> bytes:
    """
    Build a 64-byte Thermaltake USB packet.
    Layout: [0x33] [CMD] [CH] [LEN] [DATA...] [CHK] [padding 0x00 to 64]
    """
    header = bytes([0x33, cmd, channel & 0x0F, len(payload)])
    packet = header + payload
    chk    = _checksum(packet)
    packet += bytes([chk])
    packet += bytes(64 - len(packet))
    return packet


# ─────────────────────────────────────────────
#  TT Controller (USB backend)
# ─────────────────────────────────────────────
class TTController:
    """
    Low-level USB communication with the Thermaltake Riing Plus controller.
    Manages device discovery, initialisation, and command dispatch.
    """

    def __init__(self, test_mode=False):
        self.dev = None
        self.cfg = None
        self.iface = None
        self.ready = False
        self.test_mode = test_mode
        self._fan_count = [1] * MAX_CHANNELS   # default 1 fan per channel

        if not test_mode:
            self.connect()

    # ── device plumbing ──
    def connect(self) -> bool:
        if not HAS_USB:
            self.test_mode = True
            return False
        self.dev = usb.core.find(idVendor=TT_VID, idProduct=TT_PID)
        if self.dev is None:
            self.test_mode = True
            return False

        try:
            if self.dev.is_kernel_driver_active(0):
                self.dev.detach_kernel_driver(0)
        except (usb.core.USBError, NotImplementedError):
            pass

        try:
            self.dev.set_configuration()
        except usb.core.USBError:
            pass

        self.cfg  = self.dev.get_active_configuration()
        self.iface = self.cfg[(0, 0)]
        # claim interface
        try:
            usb.util.claim_interface(self.dev, self.iface)
        except usb.core.USBError:
            pass

        self.ready = True
        self._init_controller()
        return True

    def _init_controller(self):
        """Send initialisation byte-stream to detect fan count per channel."""
        if self.test_mode:
            return
        # init step 1
        self._send_raw(b'\x28\x01' + b'\x00' * 62)  # bytes 0-63, len=64
        # init step 2
        self._send_raw(b'\x29\x02' + b'\x00' * 62)
        time.sleep(0.3)
        try:
            resp = self.dev.read(0x81, 64, timeout=1000)
            if len(resp) >= 33:
                # bytes [16..20] encode fan count per channel
                self._fan_count = [min(max(resp[16 + i], 1), 5) for i in range(MAX_CHANNELS)]
        except usb.core.USBError:
            pass  # fall back to defaults

    def _send_raw(self, data: bytes):
        raw = data[:64].ljust(64, b'\x00')
        if self.test_mode:
            return
        try:
            self.dev.write(0x02, raw, timeout=1000)
        except usb.core.USBError as e:
            if os.getenv("TT_DEBUG"):
                print(f"[TT USB-Err] {e}")

    def _read_resp(self, timeout=1000) -> list:
        if self.test_mode:
            return []
        try:
            return self.dev.read(0x81, 64, timeout=timeout)
        except usb.core.USBError:
            return []

    # ── public API ──
    @property
    def num_fans(self) -> list:
        return self._fan_count

    def set_color(self, channel: int, colors: list):
        """
        Set per-LED colors on one channel.
        `colors` is a list of (R, G, B) tuples, length <= LEDS_PER_FAN.
        The controller expects packed GRB (not RGB!) per LED.
        """
        payload = bytearray()
        for r, g, b in colors[:LEDS_PER_FAN]:
            payload += bytes([b, g, r])   # Thermaltake uses BGR order
        # pad to exactly LEDS_PER_FAN * 3 bytes
        while len(payload) < LEDS_PER_FAN * 3:
            payload += b'\x00'
        pkt = build_packet(CMD_SETCOLOR, channel, bytes(payload))
        self._send_raw(pkt)

    def set_speed(self, channel: int, percent: int):
        """
        Set PWM fan speed for one channel (0-100 %).
        Thermaltake expects 0x00 (off) .. 0x64 (100 %).
        """
        val = max(0, min(100, percent))
        pkt = build_packet(CMD_SETSPEED, channel, bytes([val]))
        self._send_raw(pkt)

    def set_mode(self, channel: int, mode: int, speed: int = 0x01, direction: int = 0x00):
        """Set lighting effect mode for a channel."""
        pkt = build_packet(CMD_SETMODE, channel, bytes([mode, speed, direction]))
        self._send_raw(pkt)

    def apply(self):
        """Push pending config to the controller."""
        self._send_raw(build_packet(CMD_APPLY, 0x00, b''))

    def all_off(self):
        """Turn off all LEDs and stop all fans."""
        for ch in range(MAX_CHANNELS):
            self.set_speed(ch, 0)
            black = [(0, 0, 0)] * LEDS_PER_FAN
            self.set_color(ch, black)
        self.apply()

    def close(self):
        if self.dev and not self.test_mode:
            try:
                usb.util.release_interface(self.dev, self.iface)
                self.dev.attach_kernel_driver(0)
            except Exception:
                pass

    def __del__(self):
        self.close()


# ─────────────────────────────────────────────
#  Ring LED Preview (custom widget)
# ─────────────────────────────────────────────
class RingWidget(QWidget):
    """Circular LED ring preview mimicking the Riing Plus look."""

    def __init__(self, led_count=LEDS_PER_FAN, parent=None):
        super().__init__(parent)
        self.led_count = led_count
        self.led_colors = [(255, 100, 0)] * led_count
        self.setFixedSize(200, 200)
        self.setToolTip("Riing Plus LED-Vorschau")

    def set_colors(self, colors):
        self.led_colors = colors[:self.led_count]
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx = self.width() // 2
        cy = self.height() // 2
        outer_r = min(cx, cy) - 8
        inner_r = outer_r - 14
        led_a = 360 / self.led_count

        for i, (r, g, b) in enumerate(self.led_colors):
            angle = math.radians(i * led_a - 90)
            x = int(cx + math.cos(angle) * ((outer_r + inner_r) / 2))
            y = int(cy + math.sin(angle) * ((outer_r + inner_r) / 2))
            radius = max(4, (outer_r - inner_r) / 2 - 1)
            p.setBrush(QBrush(QColor(r, g, b)))
            p.setPen(Qt.NoPen)
            p.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)

        # centre circle (fan hub)
        p.setBrush(QBrush(QColor(30, 30, 30)))
        p.setPen(QPen(QColor(80, 80, 80), 1))
        p.drawEllipse(cx - inner_r + 2, cy - inner_r + 2,
                      (inner_r - 2) * 2, (inner_r - 2) * 2)
        p.end()


# ─────────────────────────────────────────────
#  Channel Control Widget (per channel)
# ─────────────────────────────────────────────
class ChannelControl(QWidget):
    """Full controls for one channel: speed, color, effect."""

    def __init__(self, channel_idx: int, num_fans: int, controller: TTController, parent=None):
        super().__init__(parent)
        self.ch  = channel_idx
        self.nf  = num_fans
        self.ctl = controller
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Ring preview
        preview_box = QHBoxLayout()
        preview_box.addWidget(QLabel(f"CH{self.ch + 1}:"))
        self.ring = RingWidget(LEDS_PER_FAN)
        preview_box.addWidget(self.ring)

        # fan count badge
        self.fan_label = QLabel(f"({self.nf} Lüfter)")
        preview_box.addWidget(self.fan_label)
        preview_box.addStretch()
        layout.addLayout(preview_box)

        # ── Fan Speed ──
        speed_group = QGroupBox("Lüftergeschwindigkeit (PWM)")
        sl = QHBoxLayout()

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(0, 100)
        self.speed_slider.setValue(50)
        self.speed_slider.setTickInterval(10)
        self.speed_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_slider.valueChanged.connect(self._on_speed_changed)

        self.speed_label = QLabel("50%")
        self.speed_label.setMinimumWidth(40)

        sl.addWidget(self.speed_slider)
        sl.addWidget(self.speed_label)
        speed_group.setLayout(sl)
        layout.addWidget(speed_group)

        # Speed preset buttons
        preset_row = QHBoxLayout()
        for name, val in FAN_SPEED_PRESETS.items():
            btn = QPushButton(name)
            btn.clicked.connect(partial(self._set_preset, val, name))
            preset_row.addWidget(btn)
        layout.addLayout(preset_row)

        # ── RGB Effect ──
        effect_group = QGroupBox("RGB-Effekt")
        ef = QVBoxLayout()

        effect_top = QHBoxLayout()
        effect_top.addWidget(QLabel("Modus:"))
        self.effect_combo = QComboBox()
        self.effect_combo.addItems(list(RGB_EFFECTS.values()))
        self.effect_combo.currentTextChanged.connect(self._on_effect_changed)
        effect_top.addWidget(self.effect_combo)
        effect_top.addStretch()
        ef.addLayout(effect_top)

        effect_bot = QHBoxLayout()
        effect_bot.addWidget(QLabel("Geschwindigkeit:"))
        self.efx_speed_combo = QComboBox()
        self.efx_speed_combo.addItems(list(EFFECT_SPEED_MAP.keys()))
        effect_bot.addWidget(self.efx_speed_combo)

        effect_bot.addWidget(QLabel("    Richtung:"))
        self.dir_combo = QComboBox()
        self.dir_combo.addItems(["Links → Rechts", "Rechts → Links"])
        effect_bot.addWidget(self.dir_combo)
        effect_bot.addStretch()
        ef.addLayout(effect_bot)

        effect_group.setLayout(ef)
        layout.addWidget(effect_group)

        # ── Color Picker ──
        color_group = QGroupBox("Farbe (funktioniert nur bei Static)")
        cl = QHBoxLayout()

        self.color_btn = QPushButton("Farbe wählen…")
        self.color_btn.clicked.connect(self._pick_color)

        self.color_preview = QFrame()
        self.color_preview.setFixedSize(36, 36)
        self.color_preview.setStyleSheet("background-color: rgb(255,100,0); border-radius: 4px;")

        self.current_color = QColor(255, 100, 0)

        cl.addWidget(self.color_btn)
        cl.addWidget(self.color_preview)
        cl.addStretch()
        color_group.setLayout(cl)
        layout.addWidget(color_group)

        # ── Apply ──
        apply_btn = QPushButton("⚠️ Auf Kanal anwenden")
        apply_btn.setStyleSheet(
            "QPushButton { background-color: #e67e22; color: white; font-weight: bold;"
            "padding: 8px; border-radius: 6px; }"
            "QPushButton:hover { background-color: #d35400; }"
        )
        apply_btn.clicked.connect(self._apply)
        layout.addWidget(apply_btn)

        layout.addStretch()

    # ── slots ──
    def _on_speed_changed(self, val):
        self.speed_label.setText(f"{val}%")

    def _on_effect_changed(self, effect_name: str):
        is_static = effect_name == "Static"
        self.color_btn.setEnabled(is_static)
        self.color_preview.setEnabled(is_static)

    def _set_preset(self, value: int, name: str):
        self.speed_slider.setValue(value)

    def _pick_color(self):
        c = QColorDialog.getColor(self.current_color, self, "RGB-Farbe wählen")
        if c.isValid():
            self.current_color = c
            self.color_preview.setStyleSheet(
                f"background-color: rgb({c.red()},{c.green()},{c.blue()}); border-radius: 4px;"
            )
            colors = [(c.red(), c.green(), c.blue())] * LEDS_PER_FAN
            self.ring.set_colors(colors)

    def _apply(self):
        """Send all settings for this channel to the controller."""
        if self.ctl.test_mode:
            QMessageBox.information(self, "Demo-Modus",
                "USB-Gerät nicht gefunden — Einstellungen würden gesendet werden.\n"
                "Füge udev-Regel hinzu & stecke Controller ein.")
            return

        try:
            # Speed
            self.ctl.set_speed(self.ch, self.speed_slider.value())

            # Effect mode (find key by value)
            eff_name = self.effect_combo.currentText()
            mode_key = [k for k, v in RGB_EFFECTS.items() if v == eff_name][0]
            efx_spd = EFFECT_SPEED_MAP.get(self.efx_speed_combo.currentText(), 0x01)
            direction = 0 if self.dir_combo.currentIndex() == 0 else 1
            self.ctl.set_mode(self.ch, mode_key, efx_spd, direction)

            # Color (only meaningful for Static)
            if eff_name == "Static":
                c = self.current_color
                colors = [(c.red(), c.green(), c.blue())] * LEDS_PER_FAN
                self.ctl.set_color(self.ch, colors)

            self.ctl.apply()
        except Exception as e:
            QMessageBox.warning(self, "Fehler", f"Konnte Befehl nicht senden:\n{e}")


# ─────────────────────────────────────────────
#  Main Window
# ─────────────────────────────────────────────
class MainWindow(QMainWindow):
    """Thermaltake Riing Plus — Linux Control Centre."""

    def __init__(self):
        super().__init__()
        self.controller = TTController(test_mode=False)

        if self.controller.test_mode:
            # retry without test_mode flag = user can still interact
            self.statusBar().showMessage("⚠️ Kein USB-Gerät gefunden — Demo-Modus aktiv")
        else:
            self.statusBar().showMessage("✅ Thermaltake Riing Plus verbunden")

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Thermaltake Riing Plus — Linux Control")
        self.setMinimumSize(900, 650)
        self.setStyleSheet("""
            QMainWindow { background: #2b2b2b; }
            QWidget   { color: #e0e0e0; font-size: 13px; }
            QGroupBox {
                border: 1px solid #555; border-radius: 6px; margin-top: 8px;
                font-weight: bold; padding: 12px 8px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton { background: #3a3a3a; border: 1px solid #555; padding: 6px 12px; border-radius: 4px; }
            QPushButton:hover { background: #4a4a4a; }
            QComboBox  { background: #3a3a3a; padding: 4px; border: 1px solid #555; border-radius: 4px; }
            QSlider::groove:horizontal { height: 8px; background: #444; border-radius: 4px; }
            QSlider::handle:horizontal { background: #e67e22; width: 16px; margin: -4px 0; border-radius: 3px; }
            QLabel  { color: #ccc; }
            QStatusBar { background: #1a1a1a; color: #aaa; }
        """)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # ── Header ──
        header = QHBoxLayout()
        title = QLabel("🌈 Thermaltake Riing Plus — Linux Fan & RGB Control")
        title.setFont(QFont("Sans", 18, QFont.Bold))
        title.setStyleSheet("color: #e67e22;")
        header.addWidget(title)
        header.addStretch()

        # USB status indicator
        self.usb_status = QLabel("🔌 USB: " +
            ("NICHT VERBUNDEN" if self.controller.test_mode else "VERBUNDEN"))
        self.usb_status.setStyleSheet(
            "color: #e74c3c;" if self.controller.test_mode else "color: #2ecc71;"
        )
        header.addWidget(self.usb_status)
        main_layout.addLayout(header)

        # ── Channel Tabs ──
        self.tabs = QTabWidget()
        self.tab_widgets = []
        for ch in range(MAX_CHANNELS):
            nc = ChannelControl(ch, self.controller.num_fans[ch], self.controller)
            self.tab_widgets.append(nc)
            self.tabs.addTab(nc, f"  CH {ch + 1}  ")
        main_layout.addWidget(self.tabs)

        # ── Global Actions ──
        global_group = QGroupBox("Globale Aktionen")
        gl = QHBoxLayout()

        self.all_apply = QPushButton("✅ Alle anwenden")
        self.all_apply.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        self.all_apply.clicked.connect(self._apply_all)
        gl.addWidget(self.all_apply)

        self.all_off = QPushButton("⏻ Alles AUS")
        self.all_off.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold;")
        self.all_off.clicked.connect(self._all_off)
        gl.addWidget(self.all_off)

        self.help_btn = QPushButton("❓ Hilfe")
        self.help_btn.clicked.connect(self._show_help)
        gl.addWidget(self.help_btn)

        gl.addStretch()
        global_group.setLayout(gl)
        main_layout.addWidget(global_group)

    def _apply_all(self):
        """Apply settings for all channels at once."""
        for w in self.tab_widgets:
            w._apply()

    def _all_off(self):
        if self.controller.test_mode:
            QMessageBox.information(self, "Demo-Modus",
                "USB-Gerät nicht verbunden — nichts zu tun.")
            return
        self.controller.all_off()
        self.statusBar().showMessage("Alle Lüfter & LEDs ausgeschaltet", 3000)

    def _show_help(self):
        QMessageBox.information(self, "Hilfe — Thermaltake Riing Plus",
            "<b>Erstmalige Nutzung:</b><br>"
            "1. Stecke den Thermaltake Controller per USB ein<br>"
            "2. Erstelle eine udev-Regel für USB-Zugriff ohne root:<br>"
            "<code>sudo tee /etc/udev/rules.d/99-thermaltake.rules << 'EOF'<br>"
            'SUBSYSTEM=="usb", ATTR{idVendor}=="264a", ATTR{idProduct}=="1fa5", MODE="0666"<br>'
            "EOF</code><br>"
            "3. Reload udev: <code>sudo udevadm control --reload && sudo udevadm trigger</code><br>"
            "4. App neu starten.<br><br>"
            "<b>Tipp:</b> Farbe funktioniert nur im 'Static'-Effekt. "
            "Andere Effekte (Breathing, Wave etc.) benutzen ihre eigenen Farben.<br><br>"
            "<b>PWM-Bereich:</b> Werte unter ~20% können Lüfter stoppen lassen."
        )

    def closeEvent(self, event):
        self.controller.close()
        event.accept()


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Thermaltake Riing Plus Control")
    app.setApplicationVersion("1.0.0")

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
