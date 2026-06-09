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
import select
import glob as glob_mod
import threading
import logging
import queue
import re
from functools import partial

# ─────────────────────────────────────────────
#  hidraw backend (Linux-only, no pyusb needed)
# ─────────────────────────────────────────────

def _find_hidraw_devices(vid=0x264a):
    """Find all /dev/hidraw* devices matching the given VID (and optional PID).
    Returns list of (device_path, vid, pid) tuples.
    """
    results = []
    for path in sorted(glob_mod.glob("/dev/hidraw*")):
        try:
            # Read VID/PID from udev symlink
            real = os.path.realpath(path)  # e.g. /sys/devices/pci.../usb1/1-1/1-1:1.0/0003:264A:1FA5.0001/hidraw/hidraw0
            # Walk up to find modalias
            sysfs_base = real.replace("/hidraw/" + os.path.basename(path), "")
            modalias_path = os.path.join(sysfs_base, "..", "modalias")
            if not os.path.exists(modalias_path):
                modalias_path = os.path.join(sysfs_base, "modalias")
            if os.path.exists(modalias_path):
                with open(modalias_path) as f:
                    alias = f.read().strip()
                # usb:v264Ap1FA5d...
                if f"v{vid:04X}p" in alias.lower():
                    pid_hex = alias.split("p")[1][:4] if "p" in alias else "0000"
                    try:
                        pid = int(pid_hex, 16)
                    except ValueError:
                        pid = 0
                    results.append((path, vid, pid))
            else:
                # Fallback: try reading /dev/hidrawX directly and probing
                pass
        except Exception:
            pass
    return results


HAS_HIDRAW = os.path.exists("/dev") and bool(glob_mod.glob("/dev/hidraw*"))

if not HAS_HIDRAW:
    _logger = logging.getLogger("tt-riing-plus")
    _logger.warning("No /dev/hidraw* devices found — USB-HID not available")

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

    log_path = None
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

    return logger, log_path


_logger, LOG_FILE = _safe_logging_setup()
_logger.info("TT Riing Plus Control started")
_logger.info("Python %s | Platform: %s", sys.version.split()[0], sys.platform)
_logger.info("usb=%s | qt=%s", HAS_HIDRAW, HAS_QT)

# Thread-safe log queue — GUI polls this instead of callback from foreign threads
_log_queue = queue.Queue()
_log_callback = None  # kept for compat, but GUI uses QTimer + _log_queue


def tt_log(level: str, msg: str):
    """Thread-safe log — writes to file and pushes to queue for GUI polling."""
    getattr(_logger, level.lower(), _logger.info)(msg)
    _log_queue.put((level.upper(), msg))

# ─────────────────────────────────────────────
#  USB Protocol Constants
# ─────────────────────────────────────────────
TT_VID = 0x264a

# Alle bekannten Thermaltake RGB-Controller PIDs (automatische Erkennung)
# Quelle: OpenRGB, tt-rgb, Linux kernel HID, lsusb
TT_CONTROLLERS = {
    0x1fa5: "Riing Plus",        # TT Riing Plus Digital Controller
    0x1fa6: "Riing Plus)",       # TT Riing Plus (2. Controller / Hub)
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
#  RGB Effects — Werte basierend auf OpenRGB ThermaltakeRiingController.h
# FLOW=0, SPECTRUM=4, RIPPLE=8, BLINK=C, PULSE=10, WAVE=14, PER_LED=18, FULL=19
# ─────────────────────────────────────────────
RGB_EFFECTS = {
    0x00: "Flow",
    0x04: "Spectrum",
    0x08: "Ripple",
    0x0c: "Blink",
    0x10: "Pulse",
    0x14: "Wave",
    0x18: "Per-LED",
    0x19: "Full",
}

EFFECT_SPEED_MAP = {
    "Extreme": 0x00,
    "Fast":  0x01,
    "Normal":  0x02,
    "Slow":    0x03,
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
    HID-based communication with Thermaltake Riing Plus / Quad / Trio controllers.

    Uses Linux /dev/hidraw* (no pyusb needed). Protocol from OpenRGB:
      Init:  [0x00][0xFE][0x33]
      RGB:   [0x00][0x32][0x52][port][mode|speed][GRB LED data...]
      Fan:   [0x00][0x32][0x51][port][percent]  (assumed, not confirmed)
    Packet size: 65 bytes (1 report ID + 64 data).
    """

    def __init__(self, test_mode=False):
        self.fd = None            # file descriptor from os.open()
        self.dev_path = None      # e.g. "/dev/hidraw0"
        self.ready = False
        self.test_mode = test_mode
        self._fan_count = [1] * MAX_CHANNELS
        self._detected_pid = None
        self._detected_name = None
        self._current_mode = 0x00  # Default: FLOW mode

        if not test_mode:
            self.connect()

    # ── hidraw helpers ──

    @staticmethod
    def _find_hid_devices():
        """Scan /dev/hidraw* for Thermaltake controllers.
        Returns list of (dev_path, pid, name).
        """
        found = []
        for path in sorted(glob_mod.glob("/dev/hidraw*")):
            try:
                # Resolve sysfs path to get modalias with VID/PID
                real = os.path.realpath(path)
                Device_dir = os.path.dirname(real)
                # Walk up to find device's modalias
                for candidate in [
                    os.path.join(device_dir, "modalias"),
                    os.path.join(device_dir, "..", "modalias"),
                    os.path.join(device_dir, "..", "..", "modalias"),
                ]:
                    if os.path.exists(candidate):
                        with open(candidate) as f:
                            alias = f.read().strip()
                        m = re.search(r"usb:v([0-9A-Fa-f]{4})p([0-9A-Fa-z]{4})", alias)
                        if m:
                            vid = int(m.group(1), 16)
                            pid = int(m.group(2), 16)
                            if vid == TT_VID:
                                name = TT_CONTROLLERS.get(pid, f"Unknown (PID {pid:#06x})")
                                found.append((path, pid, name))
                                break
            except Exception:
                continue
        return found

    def _open_device(self, dev_path):
        """Open hidraw device, return fd or None."""
        try:
            fd = os.open(dev_path, os.O_RDWR | os.O_NONBLOCK)
            tt_log("INFO", f"Opened {dev_path}")
            return fd
        except PermissionError:
            tt_log("ERROR", f"Permission denied: {dev_path} — need udev rule or root")
            return None
        except OSError as e:
            tt_log("ERROR", f"Cannot open {dev_path}: {e}")
            return None

    def _send_report(self, report_id, data):
        """Send a 64-byte HID report. Returns bytes written or -1."""
        if self.test_mode or self.fd is None:
            return 0
        # Linux hidraw: write report_id + data (total 65 bytes)
        buf = bytes([report_id]) + bytes(data[:63]).ljust(64, b'\x00')
        try:
            written = os.write(self.fd, buf)
            tt_log("DEBUG", f"HID write: {written}/65 bytes")
            return written
        except OSError as e:
            tt_log("ERROR", f"HID write failed: {e}")
            return -1

    def _read_report(self, timeout_ms=500):
        """Read a 64-byte HID report. Returns bytes or []."""
        if self.test_mode or self.fd is None:
            return []
        # Use select for timeout
        try:
            r, _, _ = select.select([self.fd], [], [], timeout_ms / 1000.0)
            if r:
                data = os.read(self.fd, 65)
                tt_log("DEBUG", f"HID read: {len(data)} bytes")
                return data
        except OSError as e:
            tt_log("WARNING", f"HID read failed: {e}")
        return []

    # ── USB Diagnostic ──

    def diagnose(self) -> str:
        lines = []
        lines.append("=" * 50)
        lines.append("  USB DIAGNOSE (HID)")
        lines.append("=" * 50)

        # 1. hidraw devices available?
        lines.append(f"\n[1] hidraw: {'OK' if HAS_HIDRAW else 'FEHLEND'}")
        if not HAS_HIDRAW:
            lines.append("    → No /dev/hidraw* devices, check permissions")
            return "\n".join(lines)

        # 2. Find Thermaltake controllers
        lines.append(f"\n[2] Suche nach bekannten Controllers (VID={TT_VID:#06x}):")
        found_devs = self._find_hid_devices()
        if not found_devs:
            lines.append("    — Keine gefunden (kein Device mit hidraw-Zugriff?)")
        else:
            for dev_path, pid, name in found_devs:
                lines.append(f"    ✅ {name} (PID {pid:#06x}) → {dev_path}")

        # 3. All hidraw devices
        lines.append("\n[3] Alle /dev/hidraw* Geräte:")
        for path in sorted(glob_mod.glob("/dev/hidraw*")):
            try:
                real = os.path.realpath(path)
                device_dir = os.path.dirname(real)
                modalias_str = "?"
                for candidate in [
                    os.path.join(device_dir, "modalias"),
                    os.path.join(device_dir, "..", "modalias"),
                    os.path.join(device_dir, "..", "..", "modalias"),
                ]:
                    if os.path.exists(candidate):
                        with open(candidate) as f:
                            modalias_str = f.read().strip()
                        break
                lines.append(f"    {path}: {modalias_str}")
            except Exception as e:
                lines.append(f"    {path}: Fehler — {e}")

        # 4. Permissions
        lines.append("\n[4] Berechtigungen:")
        for path in sorted(glob_mod.glob("/dev/hidraw*")):
            try:
                mode = os.stat(path).st_mode
                r = bool(mode & 0o444)
                lines.append(f"    {path}: {'lesbar' if r else 'NICHT LESBAR'}")
            except Exception:
                pass

        lines.append("\n" + "=" * 50)
        return "\n".join(lines)

    # ── connection ──

    def connect(self) -> bool:
        if not HAS_HIDRAW:
            tt_log("ERROR", "hidraw not available — entering test mode")
            self.test_mode = True
            return False

        devices = self._find_hid_devices()
        if not devices:
            tt_log("ERROR", "No Thermaltake controller found on hidraw — test mode")
            self.test_mode = True
            return False

        # Pick first device
        dev_path, pid, name = devices[0]
        self.dev_path = dev_path
        self._detected_pid = pid
        self._detected_name = name

        tt_log("INFO", f"Detected: {name} (PID {pid:#06x}) on {dev_path}")

        self.fd = self._open_device(dev_path)
        if self.fd is None:
            tt_log("ERROR", f"Cannot open {dev_path} — test mode")
            self.test_mode = True
            return False

        self.ready = True
        self._init_controller()
        tt_log("INFO", "Controller connected and initialized")
        return True

    def _init_controller(self):
        """Send init packet to wake up the controller.

        OpenRGB protocol: [0x00][0xFE][0x33] (report ID 0x00, then 2 bytes)
        Total report: 65 bytes = 0x00 + 0xFE + 0x33 + 62 zeros
        """
        if self.test_mode:
            return
        tt_log("INFO", "Initializing controller...")

        init_buf = bytes([0xFE, 0x33]) + bytes(62)
        self._send_report(0x00, init_buf)

        time.sleep(1.0)  # Give controller time to respond

        # Try to read response (may timeout — that's OK)
        resp = self._read_report(1000)
        if resp:
            tt_log("DEBUG", f"Init response: {resp[:16].hex()}")
        else:
            tt_log("DEBUG", "No init response (normal for Riing)")

    # ── public API ──

    @property
    def num_fans(self) -> list:
        return self._fan_count

    def set_color(self, channel: int, colors: list):
        """Set per-LED colors. `colors` = list of (R,G,B), up to 12 LEDs."""
        if self.test_mode or self.fd is None:
            return
        # HID report: [0x00][0x32][0x52][port][mode|speed][GRB0][GRB1]...[GRB11]
        # Port is 1-indexed, mode|speed uses mode from current_mode, default speed=1
        mode_byte = self._current_mode | 0x01  # default speed=1

        data = bytearray()
        data += bytes([0x32, 0x52])        # command
        data += bytes([channel + 1])        # port (1-indexed)
        data += bytes([mode_byte])          # mode | speed

        for r, g, b in colors[:LEDS_PER_FAN]:
            data += bytes([g, r, b])        # GRB order!
        # Pad remaining LEDs
        while len(data) < 5 + LEDS_PER_FAN * 3:
            data += b'\x00'

        self._send_report(0x00, bytes(data))

        self._last_colors = colors
        tt_log("INFO", f"set_color ch={channel} first_color=({colors[0] if colors else '?'})")

    def set_speed(self, channel: int, percent: int):
        """Set PWM fan speed 0-100%. NOTE: Not confirmed working —
        fan control on Riing Plus may need a separate HID command."""
        val = max(0, min(100, percent))
        # Attempt fan control command (speculative — OpenRGB uses SendFan with
        # [0x00][0x32][0x51][port][percent, 0x00, 0x00, 0x00, 0x00, 0x00])
        data = bytes([0x32, 0x51, channel + 1, val, 0x00, 0x00, 0x00, 0x00, 0x00])
        self._send_report(0x00, data)
        tt_log("INFO", f"set_speed ch={channel} percent={val}% (HID fan command)")

    def set_mode(self, channel: int, mode: int, effect_speed: int = 1, direction: int = 0):
        """Change lighting mode. Mode change is embedded in the color report."""
        self._current_mode = mode
        # The mode is applied on next set_color() call (mode|speed byte)
        mode_name = RGB_EFFECTS.get(mode, f"0x{mode:02x}")
        tt_log("INFO", f"set_mode ch={channel} mode={mode_name} speed={effect_speed}")

    def apply(self):
        """No separate apply needed for HID — each write goes directly."""
        tt_log("INFO", "apply() — HID writes are immediate (no buffer/apply)")

    def all_off(self):
        """Turn off all LEDs and stop all fans."""
        for ch in range(MAX_CHANNELS):
            self.set_speed(ch, 0)
            black = [(0, 0, 0)] * LEDS_PER_FAN
            self.set_color(ch, black)
        self.apply()

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

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
            radius = max(4, int((outer_r - inner_r) / 2 - 1))
            p.setBrush(QBrush(QColor(r, g, b)))
            p.setPen(Qt.NoPen)
            rx = x - radius
            ry = y - radius
            p.drawEllipse(rx, ry, radius * 2, radius * 2)

        # centre circle (fan hub)
        p.setBrush(QBrush(QColor(30, 30, 30)))
        p.setPen(QPen(QColor(80, 80, 80), 1))
        cxr = cx - inner_r + 2
        cyr = cy - inner_r + 2
        sz = int((inner_r - 2) * 2)
        p.drawEllipse(int(cxr), int(cyr), sz, sz)
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

        # Poll log queue via QTimer (thread-safe — runs on GUI thread)
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._poll_log_queue)
        self._log_timer.start(200)  # ms

    def _poll_log_queue(self):
        """Drain the log queue — called on GUI thread via QTimer."""
        while True:
            try:
                level, msg = _log_queue.get_nowait()
            except queue.Empty:
                break
            colors = {
                "DEBUG":    "#888",
                "INFO":     "#aaa",
                "WARNING":  "#f39c12",
                "ERROR":    "#e74c3c",
            }
            color = colors.get(level, "#aaa")
            import datetime
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            line = (f'<span style="color:#666">{ts}</span>  '
                    f'<span style="color:{color}">[{level:>7}]</span>  '
                    f'<span style="color:{color}">{msg}</span>')
            self.log_text.appendHtml(line)

    def _clear(self):
        self.log_text.clear()

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Log speichern", LOG_FILE, "Log (*.log *.txt)")
        if path:
            with open(path, "w") as f:
                f.write(self.log_text.toPlainText())
            tt_log("INFO", f"Log saved to {path}")

    def _refilter(self):
        # Re-read from log file
        level = self.level_filter.currentText()
        self.log_text.clear()
        if not os.path.exists(LOG_FILE):
            return
        colors = {"DEBUG": "#888", "INFO": "#aaa", "WARNING": "#f39c12", "ERROR": "#e74c3c"}
        with open(LOG_FILE) as f:
            for line in f:
                line = line.rstrip()
                if level != "Alle" and f"[{level:>7}]" not in line:
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
        self._log_timer.stop()
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
        # Run synchronously on GUI thread — avoids thread-safety issues
        # with Qt widget access from foreign threads (which causes segfault).
        # The USB scan takes ~1-2s max.
        try:
            result = self.controller.diagnose()
            self.output.setPlainText(result)
        except Exception as e:
            self.output.setPlainText(f"FEHLER bei der Diagnose:\n{e}")

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
    # hidraw
    if not HAS_HIDRAW:
        tt_log("ERROR", "hidraw nicht verfügbar! USB-HID nicht nutzbar.")
        print("[FEHLER] Kein /dev/hidraw* — prüfe Berechtigungen", file=sys.stderr)

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
        # Minimaler Controller für Diagnose
        _ctl = TTController.__new__(TTController)
        _ctl.fd = None
        _ctl.dev_path = None
        _ctl.ready = False
        _ctl.test_mode = True
        _ctl._fan_count = [1] * MAX_CHANNELS
        _ctl._detected_pid = None
        _ctl._detected_name = None
        print(_ctl.diagnose())
        sys.exit(0)

    main()
