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
  - Automatische Controller-Erkennung (5 bekannte PIDs)

Hardware: Thermaltake RGB Controller (USB HID) — VID 0x264a
Automatisch erkannt: Riing Plus, Riing Trio, Riing Quad, Flo 360, TOUGHRGB

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
import logging
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
        QCheckBox, QFrame, QScrollArea, QMessageBox, QFileDialog,
        QDialog, QTextEdit, QPlainTextEdit
    )
    from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
    from PyQt5.QtGui import QColor, QPainter, QBrush, QPen, QFont, QPixmap, QIcon
    HAS_QT = True
except ImportError:
    HAS_QT = False

def _safe_logging_setup():
    """Create logging handlers safely — never crash the app over a broken log dir."""
    logger = logging.getLogger("tt-riing-plus")
    logger.setLevel(logging.DEBUG)

    # Console handler — always works
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(ch)

    # File handler — may fail if ~/.config is missing / unwritable / disk full
    try:
        log_dir = os.path.join(os.path.expanduser("~"), ".config", "tt-riing-plus")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "tt-riing-plus.log")
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(fh)
        logger.info("%s", "=" * 40)
        logger.info("Log file: %s", log_path)
    except Exception as e:
        logger.warning("File logging disabled: %s", e)

    return logger, log_path if 'log_path' in dir() else None


_logger, LOG_FILE = _safe_logging_setup()
_logger.info("TT Riing Plus Control started")
_logger.info("Python %s | Platform: %s", sys.version.split()[0], sys.platform)
_logger.info("usb=%s | qt=%s", HAS_USB, HAS_QT)

_log_callback = None  # GUI sets this for live log forwarding


def tt_log(level: str, msg: str):
    """Thread-safe log — writes to file and notifies GUI log window."""
    getattr(_logger, level.lower(), _logger.info)(msg)
    if _log_callback:
        try:
            _log_callback(level.upper(), msg)
        except Exception:
            pass

# ─────────────────────────────────────────────
#  USB Protocol Constants
# ─────────────────────────────────────────────
TT_VID = 0x264a

# Alle bekannten Thermaltake RGB-Controller PIDs (automatische Erkennung)
# Quelle: OpenRGB, tt-rgb, Linux kernel HID
TT_CONTROLLERS = {
    0x1fa5: "Riing Plus",        # TT Riing Plus Digital Controller
    0x206e: "Flo 360",           # Thermaltake Flo 360 (AIO)
    0x206c: "TOUGHRGB",          # ToughRAM RGB Controller
    0x206b: "Riing Trio",        # Riing Trio Controller
    0x2070: "Riing Quad",        # Riing Quad Controller
}

# Fallback PID wenn kein bekanntes Gerät gefunden wurde
TT_PID_DEFAULT = 0x1fa5

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
        self._fan_count = [1] * MAX_CHANNELS
        self._detected_pid = None
        self._detected_name = None

        if not test_mode:
            self.connect()

    # ── USB Diagnostic ──
    def diagnose(self) -> str:
        """
        Umfassende USB-Diagnose — prüft pyusb, udev, Berechtigungen,
        und listet alle USB-Geräte auf.
        """
        lines = []
        lines.append("=" * 50)
        lines.append("  USB DIAGNOSE")
        lines.append("=" * 50)

        # 1. pyusb verfügbar?
        lines.append(f"\n[1] pyusb: {'OK' if HAS_USB else 'FEHLEND'}")
        if not HAS_USB:
            lines.append("    → pip3 install pyusb")
            return "\n".join(lines)

        # 2. Alle bekannten PIDs durchprobieren
        lines.append(f"\n[2] Suche nach bekannten Controllers (VID={TT_VID:#06x}):")
        found_any = False
        for pid, name in TT_CONTROLLERS.items():
            dev = usb.core.find(idVendor=TT_VID, idProduct=pid)
            status = "✅ GEFUNDEN" if dev else "—"
            lines.append(f"    PID {pid:#06x} ({name}): {status}")
            if dev is not None:
                found_any = True
                try:
                    manufacturer = usb.util.get_string(dev, dev.iManufacturer)
                    product      = usb.util.get_string(dev, dev.iProduct)
                    serial       = usb.util.get_string(dev, dev.iSerialNumber)
                    lines.append(f"      Manufacturer: {manufacturer}")
                    lines.append(f"      Product:      {product}")
                    lines.append(f"      Serial:       {serial}")
                    lines.append(f"      Bus:          {dev.bus}")
                    lines.append(f"      Address:      {dev.address}")
                except Exception as e:
                    lines.append(f"      Info-Lesefehler: {e}")

        if not found_any:
            lines.append("\n    ⚠️ Kein bekannter Controller gefunden!")

        # 3. Liste alle USB-Geräte auf mit VID 0x264a
        lines.append(f"\n[3] Alle USB-Geräte mit VID {TT_VID:#06x}:")
        try:
            tt_devices = list(usb.core.find(find_all=True, idVendor=TT_VID))
            if not tt_devices:
                lines.append("    Keine gefunden!")
            for d in tt_devices:
                pid = d.idProduct
                name = TT_CONTROLLERS.get(pid, "UNBEKANNT")
                lines.append(f"    PID {pid:#06x} ({name}) Bus={d.bus} Addr={d.address}")
        except Exception as e:
            lines.append(f"    Fehler beim Scannen: {e}")

        # 4. Liste ALL-USB devices (falls der Controller eine andere VID hat)
        lines.append("\n[4] Alle angeschlossenen USB-Geräte (VID:PID):")
        try:
            all_devs = list(usb.core.find(find_all=True))
            if not all_devs:
                lines.append("    Keine USB-Geräte sichtbar!")
            for d in all_devs[:20]:
                vid = d.idVendor
                pid = d.idProduct
                mfg = ""
                try:
                    mfg = usb.util.get_string(d, d.iManufacturer)
                except Exception:
                    mfg = "?"
                lines.append(f"    {vid:#06x}:{pid:#06x}  {mfg}")
            if len(all_devs) > 20:
                lines.append(f"    ... und {len(all_devs)-20} weitere")
        except usb.core.USBError as e:
            lines.append(f"    Fehler beim Scannen: {e}")
            if "Access denied" in str(e):
                lines.append("    ⚠️ BERECHTIGUNG PROBLEM!")
                lines.append("    → sudo erforderlich oder udev-Regel fehlt")

        # 5. Kernel-Driver Check
        lines.append("\n[5] Kernel-Driver-Status:")
        try:
            dev_test = usb.core.find(idVendor=TT_VID)
            if dev_test:
                for cfg in dev_test:
                    for intf in cfg:
                        active = dev_test.is_kernel_driver_active(intf.bInterfaceNumber)
                        lines.append(f"    Interface {intf.bInterfaceNumber}: "
                                     f"{'kernel driver ACTIVE' if active else 'frei'}")
            else:
                lines.append("    Nicht prüfbar — kein Gerät gefunden")
        except Exception as e:
            lines.append(f"    Fehler beim Prüfen: {e}")

        lines.append("\n" + "=" * 50)
        return "\n".join(lines)

    # ── device plumbing ──
    def _find_device(self):
        """
        Automatische Erkennung: durchsucht alle bekannten TT PIDs.
        Gibt (device, pid, name) oder (None, None, None) zurück.
        Logs each attempt.
        """
        for pid, name in TT_CONTROLLERS.items():
            tt_log("DEBUG", f"Trying PID {pid:#06x} ({name}) ...")
            dev = usb.core.find(idVendor=TT_VID, idProduct=pid)
            if dev is not None:
                tt_log("INFO", f"Found controller: {name} (PID {pid:#06x}) "
                        f"Bus={dev.bus} Addr={dev.address}")
                return dev, pid, name
        tt_log("WARNING", "No known Thermaltake controller found on USB bus")
        return None, None, None

    def connect(self) -> bool:
        if not HAS_USB:
            tt_log("ERROR", "pyusb not available — USB disabled")
            self.test_mode = True
            return False

        # Automatisch alle bekannten Controller-PIDs durchprobieren
        self.dev, detected_pid, detected_name = self._find_device()
        if self.dev is None:
            tt_log("ERROR", "Controller not found — entering test mode")
            self.test_mode = True
            return False

        # Für Kompatibilität: aktuelle PID merken
        self._detected_pid = detected_pid
        self._detected_name = detected_name

        tt_log("INFO", f"Detected: {detected_name} (PID {detected_pid:#06x})")

        try:
            if self.dev.is_kernel_driver_active(0):
                tt_log("INFO", "Kernel driver active — detaching")
                self.dev.detach_kernel_driver(0)
        except (usb.core.USBError, NotImplementedError) as e:
            tt_log("DEBUG", f"Kernel driver detach: {e}")

        try:
            self.dev.set_configuration()
            tt_log("INFO", "USB configuration set")
        except usb.core.USBError as e:
            tt_log("WARNING", f"set_configuration failed: {e}")

        self.cfg  = self.dev.get_active_configuration()
        self.iface = self.cfg[(0, 0)]
        # claim interface
        try:
            usb.util.claim_interface(self.dev, self.iface)
            tt_log("INFO", f"USB interface claimed (iface={self.iface.bInterfaceNumber})")
        except usb.core.USBError as e:
            tt_log("WARNING", f"claim_interface failed: {e}")

        self.ready = True
        self._init_controller()
        tt_log("INFO", "Controller connected and initialized")
        return True

    def _init_controller(self):
        """Send initialisation byte-stream to detect fan count per channel."""
        if self.test_mode:
            return
        tt_log("INFO", "Initializing controller (init1 + init2) ...")
        # init step 1
        self._send_raw(b'\x28\x01' + b'\x00' * 62)
        # init step 2
        self._send_raw(b'\x29\x02' + b'\x00' * 62)
        time.sleep(0.3)
        try:
            resp = self.dev.read(0x81, 64, timeout=1000)
            tt_log("DEBUG", f"Init response: {len(resp)} bytes — {resp[:32].hex()}")
            if len(resp) >= 33:
                self._fan_count = [min(max(resp[16 + i], 1), 5) for i in range(MAX_CHANNELS)]
                tt_log("INFO", f"Fan counts per channel: {self._fan_count}")
            else:
                tt_log("WARNING", "Init response too short — using defaults")
        except usb.core.USBError as e:
            tt_log("WARNING", f"Init read failed: {e} — using defaults")

    def _send_raw(self, data: bytes):
        raw = data[:64].ljust(64, b'\x00')
        if self.test_mode:
            return
        try:
            written = self.dev.write(0x02, raw, timeout=1000)
            tt_log("DEBUG", f"USB write: {written}/64 bytes")
        except usb.core.USBError as e:
            tt_log("ERROR", f"USB write failed: {e}")

    def _read_resp(self, timeout=1000) -> list:
        if self.test_mode:
            return []
        try:
            resp = self.dev.read(0x81, 64, timeout=timeout)
            tt_log("DEBUG", f"USB read: {len(resp)} bytes")
            return resp
        except usb.core.USBError as e:
            tt_log("WARNING", f"USB read failed: {e}")
            return []

    # ── public API ──
    @property
    def num_fans(self) -> list:
        return self._fan_count

    def set_color(self, channel: int, colors: list):
        """
        Set per-LED colors on one channel.
        `colors` is a list of (R, G, B) tuples, length <= LEDS_PER_FAN.
        The controller expects packed BGR per LED.
        """
        payload = bytearray()
        for r, g, b in colors[:LEDS_PER_FAN]:
            payload += bytes([b, g, r])
        while len(payload) < LEDS_PER_FAN * 3:
            payload += b'\x00'
        pkt = build_packet(CMD_SETCOLOR, channel, bytes(payload))
        tt_log("INFO", f"set_color ch={channel} first_color=({colors[0] if colors else '?'})")
        self._send_raw(pkt)

    def set_speed(self, channel: int, percent: int):
        """Set PWM fan speed for one channel (0-100 %)."""
        val = max(0, min(100, percent))
        pkt = build_packet(CMD_SETSPEED, channel, bytes([val]))
        tt_log("INFO", f"set_speed ch={channel} percent={val}%")
        self._send_raw(pkt)

    def set_mode(self, channel: int, mode: int, speed: int = 0x01, direction: int = 0x00):
        """Set lighting effect mode for a channel."""
        pkt = build_packet(CMD_SETMODE, channel, bytes([mode, speed, direction]))
        mode_name = RGB_EFFECTS.get(mode, f"0x{mode:02x}")
        tt_log("INFO", f"set_mode ch={channel} mode={mode_name} speed={speed} dir={direction}")
        self._send_raw(pkt)

    def apply(self):
        """Push pending config to the controller."""
        tt_log("INFO", "apply() — pushing config to controller")
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
#  Log Window (live log viewer)
# ─────────────────────────────────────────────
class LogWindow(QDialog):
    """Floating log window — shows live tt_log output."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔍 TT Riing Plus — Log")
        self.setMinimumSize(750, 500)
        self.setStyleSheet("""
            QDialog { background: #1e1e1e; }
            QTextEdit { background: #0d0d0d; color: #aaa; font-family: monospace; font-size: 12px; border: none; }
        """)

        lay = QVBoxLayout(self)

        # Controls
        ctrl = QHBoxLayout()
        clear_btn = QPushButton("🗑 Leeren")
        clear_btn.clicked.connect(self._clear)
        ctrl.addWidget(clear_btn)

        save_btn = QPushButton("💾 Speichern")
        save_btn.clicked.connect(self._save_log)
        ctrl.addWidget(save_btn)

        self.level_filter = QComboBox()
        self.level_filter.addItems(["Alle", "INFO", "WARNING", "ERROR", "DEBUG"])
        self.level_filter.currentTextChanged.connect(self._refilter)
        ctrl.addWidget(QLabel("Filter:"))
        ctrl.addWidget(self.level_filter)

        ctrl.addStretch()
        close_btn = QPushButton("✕ Schließen")
        close_btn.clicked.connect(self.close)
        ctrl.addWidget(close_btn)
        lay.addLayout(ctrl)

        # Log text
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log_text.setMaximumBlockCount(2000)
        lay.addWidget(self.log_text)

        # Register as callback
        global _log_callback
        _log_callback = self._add_line

    def _add_line(self, level: str, msg: str):
        colors = {
            "DEBUG":    "#888",
            "INFO":     "#aaa",
            "WARNING":  "#f39c12",
            "ERROR":    "#e74c3c",
        }
        color = colors.get(level, "#aaa")
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f'<span style="color:#666">{ts}</span>  ' \
               f'<span style="color:{color}">[{level:>7}]</span>  ' \
               f'<span style="color:{color}">{msg}</span>'
        # Must append from GUI thread — use metaObject invoke if needed
        try:
            self.log_text.appendHtml(line)
        except Exception:
            pass

    def _clear(self):
        self.log_text.clear()

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Log speichern", LOG_FILE, "Log (*.log *.txt)")
        if path:
            with open(path, "w") as f:
                f.write(self.log_text.toPlainText())
            tt_log("INFO", f"Log saved to {path}")

    def _refilter(self):
        # Re-read from log file (simple approach)
        level = self.level_filter.currentText()
        self.log_text.clear()
        if not os.path.exists(LOG_FILE):
            return
        colors = {"DEBUG": "#888", "INFO": "#aaa", "WARNING": "#f39c12", "ERROR": "#e74c3c"}
        with open(LOG_FILE) as f:
            for line in f:
                line = line.rstrip()
                if level != "Alle" and f"[{level:>7}]" not in line:
                    if level == "ERROR" and "[WARNING]" in line:
                        pass  # keep warnings when filtering for errors? no.
                    else:
                        continue
                color = "#aaa"
                for lvl, c in colors.items():
                    if f"[{lvl:>7}]" in line:
                        color = c
                        break
                ts_end = line.find("]") + 1 if "]" in line else 0
                ts = line[:ts_end]
                rest = line[ts_end:]
                self.log_text.appendHtml(
                    f'<span style="color:#666">{ts}</span>'
                    f'<span style="color:{color}">{rest}</span>'
                )

    def closeEvent(self, event):
        global _log_callback
        _log_callback = None
        event.accept()


# ─────────────────────────────────────────────
#  Diagnostic Dialog
# ─────────────────────────────────────────────
class DiagnosticDialog(QDialog):
    """Shows output of controller.diagnose() for USB troubleshooting."""

    def __init__(self, controller: TTController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("🔍 USB Diagnose")
        self.setMinimumSize(700, 500)
        self.setStyleSheet("""
            QDialog { background: #2b2b2b; color: #e0e0e0; }
            QTextEdit { background: #0d0d0d; color: #2ecc71; font-family: monospace; font-size: 11px; }
            QPushButton { background: #3a3a3a; padding: 6px 12px; border-radius: 4px; }
        """)

        lay = QVBoxLayout(self)

        info = QLabel(
            "USB-Hilfsdiagnose — zeigt alle gefundenen USB-Geräte, "
            "Berechtigungen und Kernel-Driver-Status."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        lay.addWidget(self.output)

        btn_row = QHBoxLayout()
        run_btn = QPushButton("🔄 Neu scannen")
        run_btn.clicked.connect(self._run)
        btn_row.addWidget(run_btn)

        save_btn = QPushButton("💾 Als Datei speichern")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        btn_row.addStretch()
        close_btn = QPushButton("✕ Schließen")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._run()

    def _run(self):
        self.output.setPlainText("Scanne USB-Bus ...\n")
        QApplication.processEvents()
        # Run in thread to avoid blocking GUI
        def _do():
            result = self.controller.diagnose()
            # Use signal to update GUI
            self.output.setPlainText(result)
        threading.Thread(target=_do, daemon=True).start()

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Diagnose speichern", "tt-diagnose.txt", "Text (*.txt)")
        if path:
            with open(path, "w") as f:
                f.write(self.output.toPlainText())


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

        # USB status indicator — zeigt erkannten Controller-Namen + PID
        if self.controller.test_mode:
            usb_text = "🔌 USB: NICHT VERBUNDEN"
            usb_color = "#e74c3c"
        else:
            name = getattr(self.controller, '_detected_name', 'Unknown')
            pid  = getattr(self.controller, '_detected_pid', 0)
            usb_text = f"🔌 USB: {name} (PID {pid:#06x})"
            usb_color = "#2ecc71"
        self.usb_status = QLabel(usb_text)
        self.usb_status.setStyleSheet(f"color: {usb_color};")
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

        self.log_btn = QPushButton("📋 Log")
        self.log_btn.clicked.connect(self._show_log)
        gl.addWidget(self.log_btn)

        self.diag_btn = QPushButton("🔍 Diagnose")
        self.diag_btn.clicked.connect(self._show_diagnose)
        gl.addWidget(self.diag_btn)

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
        # Dynamisch alle unterstützten PIDs auflisten
        pid_list = ", ".join(f"<code>{p:#06x}</code>" for p in TT_CONTROLLERS)
        udev_line = (
            'SUBSYSTEM=="usb", ATTR{idVendor}=="264a", '
            'ATTR{idProduct}=="*", MODE="0666"'
        )

        QMessageBox.information(self, "Hilfe — Thermaltake RGB Control",
            "<b>Erstmalige Nutzung:</b><br>"
            "1. Stecke den Thermaltake Controller per USB ein<br>"
            "2. Erstelle eine udev-Regel für USB-Zugriff ohne root:<br>"
            f"<code>sudo tee /etc/udev/rules.d/99-thermaltake.rules << 'EOF'<br>"
            f"{udev_line}<br>"
            "EOF</code><br>"
            "3. Reload udev: <code>sudo udevadm control --reload && sudo udevadm trigger</code><br>"
            "4. App neu starten.<br><br>"
            f"<b>Unterstützte Controller:</b> {pid_list}<br><br>"
            "<b>Tipp:</b> Farbe funktioniert nur im 'Static'-Effekt. "
            "Andere Effekte (Breathing, Wave etc.) benutzen ihre eigenen Farben.<br><br>"
            "<b>PWM-Bereich:</b> Werte unter ~20% können Lüfter stoppen lassen.<br><br>"
            "<b>Automatische Erkennung:</b> Die App probiert alle bekannten PIDs durch "
            "und zeigt den gefundenen Controller im Header an."
        )

    def _show_log(self):
        """Open the live log viewer dialog."""
        dlg = LogWindow(self)
        dlg.exec_()

    def _show_diagnose(self):
        """Open the USB diagnostic dialog."""
        dlg = DiagnosticDialog(self.controller, self)
        dlg.exec_()

    def closeEvent(self, event):
        self.controller.close()
        event.accept()


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────
def main():
    # ── Pre-flight: catch import/startup errors and show them ──
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Thermaltake Riing Plus Control")
        app.setApplicationVersion("1.0.0")
    except Exception as e:
        _print_startup_diag(f"Qt-Init fehlgeschlagen: {e}")
        sys.exit(1)

    # ── System-Check — warnt vor Problemen bevor sie crashen ──
    _system_check()

    try:
        window = MainWindow()
        window.show()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        tt_log("ERROR", f"Startup crash: {e}\n{tb}")
        try:
            QMessageBox.critical(None, "Startup Error",
                f"App konnte nicht starten:\n\n{e}\n\n"
                f"Log: {LOG_FILE or '(kein Log)'}\n\n"
                f"Diagnose starten mit: python3 {__file__} --diag")
        except Exception:
            _print_startup_diag(tb)
        sys.exit(1)

    sys.exit(app.exec_())


def _print_startup_diag(msg: str):
    """Print diagnostic text that is also visible when Qt is broken."""
    print(f"\n{'='*50}", file=sys.stderr)
    print("  TT Riing Plus — Startup Fehler", file=sys.stderr)
    print(f"{'='*50}\n", file=sys.stderr)
    print(msg, file=sys.stderr)


def _system_check():
    """Lightweight pre-startup check — logs problems before the GUI loads."""
    # pyusb
    if not HAS_USB:
        tt_log("ERROR", "pyusb fehlt! USB nicht verfügbar.")
        print("[FEHLER] pyusb nicht installiert: pip3 install pyusb", file=sys.stderr)

    # PyQt5 / X11
    if not HAS_QT:
        tt_log("ERROR", "PyQt5 fehlt! GUI nicht verfügbar.")
        print("[FEHLER] PyQt5 nicht installiert: sudo apt install python3-pyqt5", file=sys.stderr)

    # DISPLAY variable (headless?)
    display = os.environ.get("DISPLAY", "")
    wayland = os.environ.get("WAYLAND_DISPLAY", "")
    if not display and not wayland:
        tt_log("WARNING", "Kein DISPLAY/WAYLAND_DISPLAY — GUI vermutlich nicht sichtbar")
        print("[WARNUNG] Kein X11/Wayland display. GUI nur mit DISPLAY=:0 ... starten", file=sys.stderr)

    # Log-Pfad prüfen
    try:
        if LOG_FILE:
            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            with open(LOG_FILE, "a"):
                pass
            tt_log("DEBUG", f"Log writable: {LOG_FILE}")
        else:
            tt_log("WARNING", "Kein Log-File — File Logging deaktiviert")
    except Exception as e:
        tt_log("WARNING", f"Log nicht beschreibbar: {e}")


if __name__ == "__main__":
    # Quick headless diagnostic:  python3 tt_riing_plus.py --diag
    if "--diag" in sys.argv:
        print("🔍 Starte USB-Diagnose (headless) ...\n")
        try:
            import usb.core  # noqa: F401
        except ImportError:
            print("❌ pyusb fehlschlägt: pip3 install pyusb\n")
            sys.exit(1)
        # Minimaler Controller für Diagnose
        _ctl = TTController.__new__(TTController)
        _ctl.dev = None; _ctl.cfg = None; _ctl.iface = None
        _ctl.ready = False; _ctl.test_mode = True
        _ctl._fan_count = [1] * MAX_CHANNELS
        _ctl._detected_pid = None; _ctl._detected_name = None
        print(_ctl.diagnose())
        sys.exit(0)

    main()
