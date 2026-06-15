#!/usr/bin/env python3
"""
Thermaltake Riing Plus Linux Control Software
=============================================
Vollständige Lüftersteuerung inkl. PWM & RGB für Pop!_OS/Linux.

Features:
  - Bis zu 5 Kanäle (max 5 Lüfter pro Kanal)
  - PWM-Geschwindigkeit 0-100 %
  - RGB-Farben pro Kanal
  - RGB-Effekte: Flow, Spectrum, Ripple, Blink, Pulse, Wave, Per-LED, Full
  - Echtzeit-Vorschau der Ring-LEDs
  - Automatische Controller-Erkennung (5 bekannte PIDs)

Hardware: Thermaltake RGB Controller (USB HID) — VID 0x264a
Automatisch erkannt: Riing Plus, Riing Trio, Riing Quad, Flo 360, TOUGHRGB

Protokoll-Quelle: OpenRGB ThermaltakeRiingController.cpp/.h
  https://github.com/CalcProgrammer1/OpenRGB/blob/master/Controllers/
  ThermaltakeRiingController/ThermaltakeRiingController/

Author: OWL für Bjk201
License: MIT
Version: 2.0.0
"""

import sys
import os
import time
import math
import threading
import logging
import queue
from functools import partial
import json as _json_mod
import collections as _coll_mod

# ─────────────────────────────────────────────
#  Backend imports
# ─────────────────────────────────────────────
try:
    import hid
    HAS_HIDAPI = True
except ImportError:
    HAS_HIDAPI = False

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QSlider, QPushButton, QComboBox, QColorDialog,
        QGroupBox, QGridLayout, QSpinBox, QTabWidget, QStatusBar,
        QCheckBox, QFrame, QScrollArea, QMessageBox, QFileDialog,
        QDialog, QTextEdit, QPlainTextEdit, QLineEdit,
    )
    from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
    from PyQt5.QtGui import QColor, QPainter, QBrush, QPen, QFont, QPixmap, QIcon
    HAS_QT = True
except ImportError:
    HAS_QT = False

# ── pyqtgraph (optional, for live graph) ──
try:
    import pyqtgraph as pg
    from pyqtgraph import PlotWidget, PlotDataItem, DateAxisItem
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False

# ─────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────
def _safe_logging_setup():
    logger = logging.getLogger("tt-riing-plus")
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(ch)
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
        return logger, log_path
    except Exception as e:
        logger.warning("File logging disabled: %s", e)
        return logger, None

_logger, LOG_FILE = _safe_logging_setup()
_logger.info("TT Riing Plus Control started")
_logger.info("Python %s | Platform: %s", sys.version.split()[0], sys.platform)
_logger.info("hidapi=%s | qt=%s", HAS_HIDAPI, HAS_QT)

# Thread-safe log queue — GUI polls this via QTimer
_log_queue = queue.Queue()

def tt_log(level: str, msg: str):
    """Thread-safe log — writes to file and pushes to queue for GUI polling."""
    getattr(_logger, level.lower(), _logger.info)(msg)
    _log_queue.put((level.upper(), msg))

# ─────────────────────────────────────────────
#  Feature Imports (Profile, Auto, Graph)
# ─────────────────────────────────────────────
try:
    from tt_features import (
        ProfileManager, AutoMode, HistoryBuffer, HistoryPoint,
        FAN_CURVE, FAN_MIN, AUTO_UPDATE_MS,
        HAS_PSUTIL, HAS_PYQTGRAPH,
        DEFAULT_PROFILES, PROFILE_FILE,
        load_channel_descriptions, save_channel_descriptions,
    )
    HAS_FEATURES = True
except ImportError:
    HAS_FEATURES = False

# ─────────────────────────────────────────────
#  Protocol Constants
# ─────────────────────────────────────────────
# Quelle: OpenRGB ThermaltakeRiingController.h
#   ThermaltakeRiingController.cpp

TT_VID = 0x264a

TT_CONTROLLERS = {
    0x1fa5: "Riing Plus",
    0x1fa6: "Riing Plus Hub",
    0x206e: "Flo 360",
    0x206c: "TOUGHRGB",
    0x206b: "Riing Trio",
    0x2070: "Riing Quad",
}

# Preferred PID for primary controller
TT_PID_PRIMARY = 0x1fa5

MAX_CHANNELS = 5
LEDS_PER_FAN = 12

# Report size: 1 byte Report ID + 64 bytes payload = 65 bytes
# Quelle: OpenRGB ThermaltakeRiingController.cpp — hid_write(dev, usb_buf, 65)
REPORT_SIZE = 65
REPORT_PAYLOAD = 64
REPORT_ID = 0x00

# ── Commands (byte 1 of report) ──────────────────
CMD_INIT   = 0xFE  # Initialization
CMD_RGB    = 0x32  # RGB color data
CMD_FAN    = 0x33  # Fan speed / firmware

# ── Sub-commands (byte 2 of report) ──────────────
SUB_INIT    = 0x33  # Init sub-command
SUB_RGB     = 0x52  # RGB data sub-command
SUB_FW      = 0x50  # Firmware version
SUB_FAN_PWM = 0x56  # Fan PWM speed

# ── Effect modes ──────────────────────────────────
# Quelle: OpenRGB ThermaltakeRiingController.h — THERMALTAKE_MODE_*
# WICHTIG: Diese Werte sind NICHT 0x00-0x07!
# Die Mode-Werte sind: 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x19
MODE_FLOW     = 0x00  # Flow (rainbow)
MODE_SPECTRUM = 0x04  # Spectrum Cycle
MODE_RIPPLE   = 0x08  # Ripple
MODE_BLINK    = 0x0C  # Blink
MODE_PULSE    = 0x10  # Pulse
MODE_WAVE     = 0x14  # Wave
MODE_PER_LED  = 0x18  # Per-LED (Direct)
MODE_FULL     = 0x19  # Full (Static single color)

# ── Effect speeds ─────────────────────────────────
# Quelle: OpenRGB ThermaltakeRiingController.h — THERMALTAKE_SPEED_*
SPEED_SLOW    = 0x03
SPEED_NORMAL  = 0x02
SPEED_FAST    = 0x01
SPEED_EXTREME = 0x00

# Mode name mapping for GUI
RGB_EFFECTS = {
    MODE_FLOW:     "Flow",
    MODE_SPECTRUM: "Spectrum",
    MODE_RIPPLE:   "Ripple",
    MODE_BLINK:    "Blink",
    MODE_PULSE:    "Pulse",
    MODE_WAVE:     "Wave",
    MODE_PER_LED:  "Per-LED",
    MODE_FULL:     "Static",
}

# Reverse mapping: name -> mode value
EFFECT_NAME_TO_MODE = {v: k for k, v in RGB_EFFECTS.items()}

EFFECT_SPEED_MAP = {
    "Extreme": SPEED_EXTREME,
    "Fast":    SPEED_FAST,
    "Normal":  SPEED_NORMAL,
    "Slow":    SPEED_SLOW,
}

FAN_SPEED_PRESETS = {
    "Silent":      20,
    "Normal":      50,
    "Performance": 75,
    "Full":        100,
}

# ─────────────────────────────────────────────
#  TT Controller (hidapi backend)
# ─────────────────────────────────────────────
class TTController:
    """
    Low-level USB communication with the Thermaltake Riing Plus controller.
    Uses hidapi library. Protocol based on OpenRGB ThermaltakeRiingController.

    Packet format (65 bytes, sent via hid_device.write):
      Byte 0:    Report ID (0x00) — MUST be first byte, hidapi does NOT prepend it
      Byte 1:    Command
      Byte 2:    Sub-command
      Byte 3:    Port (1-indexed)
      Byte 4:    Mode + Speed (addition, NOT bit-OR!)
      Byte 5-40: GRB color data (12 LEDs * 3 bytes)

    Quelle: OpenRGB ThermaltakeRiingController.cpp
      SendInit():  buf[0]=0x00, buf[1]=0xFE, buf[2]=0x33
      SendRGB():   buf[0]=0x00, buf[1]=0x32, buf[2]=0x52, buf[3]=port,
                   buf[4]=mode + (speed & 0x03), buf[5:]=GRB
      hid_write(dev, buf, 65) — always 65 bytes

    HIDAPI write() behavior (libusb/hidapi hid_write()):
      The first byte of the buffer MUST be the Report ID (0x00 for single-report
      devices). hidapi does NOT prepend the Report ID automatically.
      Quelle: hidapi documentation — "The first byte of data[] must contain the
      report ID. For devices which only contain a single report, this must be
      set to 0x00. The remaining bytes contain the report data."
    """

    def __init__(self, test_mode=False):
        self.devs = []        # list of (device, pid, name) — supports multiple controllers
        self.ready = False
        self.test_mode = test_mode
        self._fan_count = [1] * MAX_CHANNELS
        self._detected_pid = None
        self._detected_name = None
        self._detected_path = None
        self.num_channels = MAX_CHANNELS  # will be updated in connect()
        # Per-channel state
        self._current_mode = [MODE_FULL] * MAX_CHANNELS
        self._current_speed = [SPEED_NORMAL] * MAX_CHANNELS
        self._current_colors = [[(255, 100, 0)] * LEDS_PER_FAN for _ in range(MAX_CHANNELS)]
        if not test_mode:
            self.connect()

    # ── device discovery ──
    @staticmethod
    def _find_controllers():
        """
        Scan for ALL Thermaltake controllers using hidapi enumeration.
        Returns list of (path_bytes, pid, name) tuples for all matching devices.
        Priority: primary PID (0x1fa5) first, then secondary (0x1fa6), then others.
        """
        if not HAS_HIDAPI:
            return []
        try:
            devices = hid.enumerate()
            results = []
            # First pass: primary PID (0x1fa5 = RGB controller)
            for d in devices:
                if d.get('vendor_id') == TT_VID and d.get('product_id') == 0x1fa5:
                    results.append((d['path'], 0x1fa5, TT_CONTROLLERS[0x1fa5]))
            # Second pass: hub PID (0x1fa6 = Fan/Hub controller)
            for d in devices:
                if d.get('vendor_id') == TT_VID and d.get('product_id') == 0x1fa6:
                    results.append((d['path'], 0x1fa6, TT_CONTROLLERS[0x1fa6]))
            # Third pass: any other known PID
            for d in devices:
                pid = d.get('product_id')
                if d.get('vendor_id') == TT_VID and pid in TT_CONTROLLERS and pid not in (0x1fa5, 0x1fa6):
                    results.append((d['path'], pid, TT_CONTROLLERS[pid]))
            # Fourth pass: any unknown TT device
            for d in devices:
                pid = d.get('product_id')
                if d.get('vendor_id') == TT_VID and pid not in TT_CONTROLLERS:
                    results.append((d['path'], pid, f"Unknown TT (PID {pid:#06x})"))
            return results
        except Exception:
            pass
        return []

    @staticmethod
    def _count_channels_from_devices(devices):
        """
        Determine total number of channels from discovered devices.
        Each RGB controller (0x1fa5) provides 5 channels.
        Each Hub (0x1fa6) provides 5 channels.
        Total = sum of all controller channels.
        """
        total = 0
        for _, pid, _ in devices:
            if pid in (0x1fa5, 0x1fa6):
                total += 5
            else:
                total += 5  # default for unknown controllers
        return total if total > 0 else MAX_CHANNELS

    # ── diagnostic ──
    def diagnose(self) -> str:
        lines = []
        lines.append("=" * 50)
        lines.append("  USB DIAGNOSE (HID)")
        lines.append("=" * 50)
        lines.append(f"\n[1] hidapi: {'OK' if HAS_HIDAPI else 'FEHLEND'}")
        if not HAS_HIDAPI:
            lines.append("    → pip3 install hidapi")
        lines.append(f"\n[2] Controller-Suche (VID={TT_VID:#06x}):")
        found_any = False
        if HAS_HIDAPI:
            try:
                all_devices = self._find_controllers()
                for path, pid, name in all_devices:
                    lines.append(f"    ✅ {name} (PID {pid:#06x})")
                    found_any = True
                if not found_any:
                    # Fallback: show all TT devices
                    for d in hid.enumerate():
                        vid = d.get('vendor_id')
                        pid = d.get('product_id')
                        if vid == TT_VID:
                            name = TT_CONTROLLERS.get(pid, f"Unknown (PID {pid:#06x})")
                            lines.append(f"    ✅ {name} (PID {pid:#06x})")
                            found_any = True
            except Exception as e:
                lines.append(f"    Fehler: {e}")
        if not found_any:
            lines.append("    — Keine gefunden")
        lines.append(f"\n[3] Geöffnete Devices: {len(self.devs)}")
        for dev, pid, name in self.devs:
            lines.append(f"    ✅ {name} (PID {pid:#06x})")
        lines.append(f"[4] Kanäle: {self.num_channels}")
        if HAS_FEATURES:
            lines.append(f"[5] psutil: {'OK' if HAS_PSUTIL else 'FEHLEND — pip3 install psutil'}")
        lines.append("\n" + "=" * 50)
        return "\n".join(lines)

    # ── connection ──
    def connect(self) -> bool:
        if not HAS_HIDAPI:
            tt_log("ERROR", "hidapi nicht verfügbar — USB disabled")
            self.test_mode = True
            return False

        found = self._find_controllers()
        if not found:
            tt_log("ERROR", "Controller not found — entering test mode")
            self.test_mode = True
            return False

        # Determine total channels from all discovered controllers
        self.num_channels = self._count_channels_from_devices(found)
        # Expand per-channel state if needed
        if self.num_channels > MAX_CHANNELS:
            extra = self.num_channels - len(self._current_mode)
            self._current_mode.extend([MODE_FULL] * extra)
            self._current_speed.extend([SPEED_NORMAL] * extra)
            self._current_colors.extend([[(255, 100, 0)] * LEDS_PER_FAN for _ in range(extra)])
            self._fan_count.extend([1] * extra)

        tt_log("INFO", f"Gefundene Controller: {len(found)} — {self.num_channels} Kanäle")

        # Open all found devices
        for path, pid, name in found:
            try:
                dev = hid.device()
                dev.open_path(path)
                self.devs.append((dev, pid, name))
                tt_log("INFO", f"Device opened: {name} (PID {pid:#06x})")
            except Exception as e:
                tt_log("ERROR", f"Cannot open {name} (PID {pid:#06x}): {e}")

        if not self.devs:
            tt_log("ERROR", "No devices could be opened — entering test mode")
            self.test_mode = True
            return False

        self.ready = True
        self._detected_pid = found[0][1]
        self._detected_name = found[0][2]
        self._detected_path = found[0][0]
        self._init_controller()
        tt_log("INFO", "Controller connected and initialized")
        return True

    def _init_controller(self):
        """
        Send initialization packet.
        Quelle: OpenRGB ThermaltakeRiingController.cpp — SendInit():
          buf[0]=0x00, buf[1]=0xFE, buf[2]=0x33, rest zeros
          hid_write(dev, buf, 65)
          hid_read_timeout(dev, buf, 65, 100)
        """
        if self.test_mode:
            return
        tt_log("INFO", "Initializing controller ...")
        self._send_packet(self._build_init_packet())
        time.sleep(0.3)
        try:
            dev = self.devs[0][0]
            resp = dev.read(REPORT_SIZE, timeout_ms=1000)
            tt_log("DEBUG", f"Init response: {len(resp)} bytes — {bytes(resp)[:16].hex()}")
        except Exception as e:
            tt_log("DEBUG", f"Init read: {e}")

    # ── low-level send/receive ──
    def _send_packet(self, packet: bytes):
        """
        Send a 65-byte packet to the controller via hidapi.
        Sends to ALL opened devices (RGB + Hub).
        """
        if self.test_mode or not self.devs:
            return
        try:
            if len(packet) != REPORT_SIZE:
                tt_log("ERROR", f"Invalid packet size: {len(packet)} (expected {REPORT_SIZE})")
                return
            for dev, pid, name in self.devs:
                try:
                    dev.write(packet)
                except Exception as e:
                    tt_log("ERROR", f"hidapi write failed ({name}): {e}")
            tt_log("DEBUG", f"hidapi write: {REPORT_SIZE} bytes — {packet[:8].hex()}")
        except Exception as e:
            tt_log("ERROR", f"hidapi write failed: {e}")

    def _read_response(self, timeout_ms=1000) -> bytes:
        """Read response from first opened device. Returns raw bytes."""
        if self.test_mode or not self.devs:
            return b''
        try:
            dev = self.devs[0][0]
            resp = dev.read(REPORT_SIZE, timeout_ms=timeout_ms)
            return bytes(resp)
        except Exception as e:
            tt_log("DEBUG", f"hidapi read: {e}")
            return b''

    # ── packet builders (all return 65 bytes: Report ID + 64 payload) ──
    def _build_init_packet(self) -> bytes:
        """
        Build 65-byte init packet.
        Quelle: OpenRGB SendInit():
          buf[0x00] = 0x00;  buf[0x01] = 0xFE;  buf[0x02] = 0x33;
        """
        buf = bytearray(REPORT_SIZE)
        buf[0] = REPORT_ID    # 0x00
        buf[1] = CMD_INIT     # 0xFE
        buf[2] = SUB_INIT     # 0x33
        return bytes(buf)

    def _build_rgb_packet(self, port: int, mode: int, speed: int, colors: list) -> bytes:
        """
        Build 65-byte RGB color packet.

        Quelle: OpenRGB SendRGB():
          buf[0x00] = 0x00;
          buf[0x01] = 0x32;
          buf[0x02] = 0x52;
          buf[0x03] = port;               // 1-indexed
          buf[0x04] = mode + (speed & 0x03);  // Addition, NOT bit-OR!
          memcpy(&buf[0x05], color_data, num_colors * 3);  // GRB order

        port: 1-indexed channel number
        mode: effect mode (MODE_* constants: 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x19)
        speed: effect speed (SPEED_* constants: 0x00-0x03)
        colors: list of (R, G, B) tuples, up to LEDS_PER_FAN
        """
        buf = bytearray(REPORT_SIZE)
        buf[0] = REPORT_ID                   # 0x00
        buf[1] = CMD_RGB                     # 0x32
        buf[2] = SUB_RGB                      # 0x52
        buf[3] = port                         # 1-indexed
        # Quelle: OpenRGB — mode + (speed & 0x03), NICHT mode | (speed & 0x03)
        buf[4] = mode + (speed & 0x03)
        # Fill GRB color data starting at byte 5
        # Quelle: OpenRGB — color_data[color_idx+0]=G, [+1]=R, [+2]=B
        for i, (r, g, b) in enumerate(colors[:LEDS_PER_FAN]):
            idx = 5 + i * 3
            if idx + 2 < REPORT_SIZE:
                buf[idx + 0] = g  # G first (GRB order)
                buf[idx + 1] = r
                buf[idx + 2] = b
        return bytes(buf)

    def _build_fan_packet(self, port: int, percent: int) -> bytes:
        """
        Build 65-byte fan speed packet.
        Format: [0x00, 0x32, 0x51, port, 0x01, speed, 0x00...]

        Quelle: chestm007/linux_thermaltake_riing devices/__init__.py:
          ThermaltakeFanDevice.set_fan_speed():
            data = [PROTOCOL_SET, PROTOCOL_FAN, self.port, 0x01, int(speed)]
            # PROTOCOL_SET=0x32, PROTOCOL_FAN=0x51

        Speed: 0-100 (percentage, NOT 0-255 PWM)
        """
        buf = bytearray(REPORT_SIZE)
        buf[0] = REPORT_ID                   # 0x00
        buf[1] = CMD_RGB                     # 0x32 (PROTOCOL_SET)
        buf[2] = 0x51                        # PROTOCOL_FAN
        buf[3] = port                         # 1-indexed
        buf[4] = 0x01                        # fixed
        buf[5] = min(100, max(0, percent))   # speed 0-100
        return bytes(buf)

    # ── public API ──
    @property
    def num_fans(self) -> list:
        return self._fan_count

    def set_color(self, channel: int, colors: list):
        """
        Set per-LED colors on one channel.
        Does NOT change the current effect mode.
        `colors` is a list of (R, G, B) tuples.
        """
        self._current_colors[channel] = colors[:LEDS_PER_FAN]
        mode = self._current_mode[channel]
        speed = self._current_speed[channel]
        packet = self._build_rgb_packet(channel + 1, mode, speed, colors)
        self._send_packet(packet)
        tt_log("INFO", f"set_color ch={channel} mode={mode:#04x} speed={speed} first=({colors[0] if colors else '?'})")

    def set_mode(self, channel: int, mode: int, speed: int = SPEED_NORMAL):
        """
        Set lighting effect mode and speed for a channel. Does NOT change colors.
        Re-sends current colors with new mode.
        """
        self._current_mode[channel] = mode
        self._current_speed[channel] = speed & 0x03
        colors = self._current_colors[channel]
        packet = self._build_rgb_packet(channel + 1, mode, self._current_speed[channel], colors)
        self._send_packet(packet)
        mode_name = RGB_EFFECTS.get(mode, f"0x{mode:02x}")
        tt_log("INFO", f"set_mode ch={channel} mode={mode_name}({mode:#04x}) speed={speed}")

    def _commit_fan(self):
        """
        Send save_profile commit after fan speed change.
        Quelle: chestm007/linux_thermaltake_riing drivers.py:
          save_profile(): write_out([0x32, 0x53])
        """
        buf = bytearray(REPORT_SIZE)
        buf[0] = REPORT_ID   # 0x00
        buf[1] = CMD_RGB     # 0x32
        buf[2] = 0x53        # save_profile sub-command
        self._send_packet(bytes(buf))

    def set_speed(self, channel: int, percent: int):
        """
        Set PWM fan speed for one channel (0-100%).
        Quelle: chestm007/linux_thermaltake_riing devices/__init__.py:
          ThermaltakeFanDevice.set_fan_speed():
            data = [PROTOCOL_SET, PROTOCOL_FAN, self.port, 0x01, int(speed)]
        """
        val = max(0, min(100, percent))
        packet = self._build_fan_packet(channel + 1, val)
        self._send_packet(packet)
        self._commit_fan()
        tt_log("INFO", f"set_speed ch={channel} percent={val}%")

    def try_read_status(self) -> dict:
        """
        Attempt to read current status from the controller.
        The Thermaltake Riing Plus protocol is mostly write-only; this is a
        best-effort attempt. Returns a dict with whatever could be read.
        Unreadable fields are set to None and should be shown as "?"
        in the UI.
        """
        status = {
            "modes": [None] * self.num_channels,
            "speeds": [None] * self.num_channels,
            "fan_speeds": [None] * self.num_channels,
            "readable": False,
        }
        if self.test_mode or not self.devs:
            tt_log("DEBUG", "try_read_status: test_mode or no devices — returning empty")
            return status

        # Try reading from the init response
        try:
            # Send a status request (some controllers respond to init with status)
            buf = bytearray(REPORT_SIZE)
            buf[0] = REPORT_ID
            buf[1] = CMD_INIT
            buf[2] = SUB_INIT
            self._send_packet(bytes(buf))
            time.sleep(0.1)
            resp = self._read_response(timeout_ms=500)
            if resp and len(resp) >= REPORT_SIZE:
                status["readable"] = True
                tt_log("DEBUG", f"try_read_status: got {len(resp)} bytes response")
                # NOTE: The Thermaltake protocol does not provide per-channel
                # status readout. The response (if any) is just an ACK.
                # We log it for debugging but cannot extract actual values.
                tt_log("WARNING",
                    "Controller-Status auslesen wird vom Thermaltake-Protokoll "
                    "nicht unterstützt (write-only). Manuelle Eingabe erforderlich.")
        except Exception as e:
            tt_log("DEBUG", f"try_read_status: read failed: {e}")

        return status

    def all_off(self):
        """Turn off all LEDs and stop all fans."""
        for ch in range(self.num_channels):
            self.set_speed(ch, 0)
            black = [(0, 0, 0)] * LEDS_PER_FAN
            self.set_color(ch, black)

    def close(self):
        if self.devs and not self.test_mode:
            for dev, pid, name in self.devs:
                try:
                    dev.close()
                except Exception:
                    pass
        self.devs = []

    def __del__(self):
        self.close()


# ─────────────────────────────────────────────
#  GUI Classes (only when PyQt5 is available)
# ─────────────────────────────────────────────
if HAS_QT:

    class RingWidget(QWidget):
        """Circular LED ring preview."""

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
                p.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)
            p.setBrush(QBrush(QColor(30, 30, 30)))
            p.setPen(QPen(QColor(80, 80, 80), 1))
            cxr = cx - inner_r + 2
            cyr = cy - inner_r + 2
            sz = int((inner_r - 2) * 2)
            p.drawEllipse(int(cxr), int(cyr), sz, sz)
            p.end()


    class ChannelControl(QWidget):
        """Full controls for one channel: description, speed, color, effect."""

        def __init__(self, channel_idx: int, num_fans: int, controller: TTController, parent=None):
            super().__init__(parent)
            self.ch = channel_idx
            self.nf = num_fans
            self.ctl = controller
            self._description = ""
            self._setup_ui()

        def _setup_ui(self):
            layout = QVBoxLayout(self)
            layout.setSpacing(6)

            # ── Channel header: icon + description + fan count ──
            header = QHBoxLayout()
            header.setSpacing(6)
            ch_icon = QLabel("💨")
            ch_icon.setFont(QFont("Sans", 16))
            header.addWidget(ch_icon)

            # Editable channel description
            desc_layout = QVBoxLayout()
            desc_layout.setSpacing(2)
            desc_label = QLabel("Kanalbezeichnung:")
            desc_label.setStyleSheet("color: #999; font-size: 11px;")
            self.desc_edit = QLineEdit()
            self.desc_edit.setPlaceholderText("z.B. CPU Radiator, Front Fans, Top LED Stripe …")
            self.desc_edit.setMaxLength(64)
            self.desc_edit.setStyleSheet(
                "QLineEdit { background: #3a3a3a; color: #e0e0e0; padding: 4px 8px; "
                "border: 1px solid #555; border-radius: 4px; font-size: 13px; }"
                "QLineEdit:focus { border-color: #e67e22; }"
            )
            self.desc_edit.editingFinished.connect(self._on_desc_changed)
            desc_layout.addWidget(desc_label)
            desc_layout.addWidget(self.desc_edit)
            header.addLayout(desc_layout, 1)

            self.fan_label = QLabel(f"({self.nf} Lüfter)")
            self.fan_label.setStyleSheet("color: #777; font-size: 11px;")
            header.addWidget(self.fan_label)
            layout.addLayout(header)

            # ── Ring preview (compact) ──
            preview_box = QHBoxLayout()
            self.ring = RingWidget(LEDS_PER_FAN)
            self.ring.setFixedSize(120, 120)
            preview_box.addWidget(self.ring)
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
            self.speed_label.setStyleSheet("color: #e67e22; font-weight: bold; font-size: 14px;")
            # Tooltip: controller state is write-only, values are not readable
            self.speed_slider.setToolTip(
                "Hinweis: Der aktuelle Lüfterstand kann nicht vom Controller ausgelesen werden. "
                "Der Wert wird beim Start auf 50% gesetzt.")
            sl.addWidget(self.speed_slider)
            sl.addWidget(self.speed_label)
            speed_group.setLayout(sl)
            layout.addWidget(speed_group)

            # Speed preset buttons
            preset_row = QHBoxLayout()
            for name, val in FAN_SPEED_PRESETS.items():
                btn = QPushButton(name)
                btn.setStyleSheet(
                    "QPushButton { padding: 4px 10px; font-size: 11px; }"
                )
                btn.clicked.connect(partial(self._set_preset, val, name))
                preset_row.addWidget(btn)
            layout.addLayout(preset_row)

            # ── RGB Effect ──
            effect_group = QGroupBox("RGB-Effekt")
            ef = QGridLayout()
            ef.setColumnStretch(1, 1)

            ef.addWidget(QLabel("Modus:"), 0, 0)
            self.effect_combo = QComboBox()
            effect_names = [RGB_EFFECTS[m] for m in [MODE_FLOW, MODE_SPECTRUM, MODE_RIPPLE,
                                                      MODE_BLINK, MODE_PULSE, MODE_WAVE,
                                                      MODE_PER_LED, MODE_FULL]]
            self.effect_combo.addItems(effect_names)
            self.effect_combo.setMinimumWidth(120)
            self.effect_combo.currentTextChanged.connect(self._on_effect_changed)
            ef.addWidget(self.effect_combo, 0, 1)

            ef.addWidget(QLabel("Geschwindigkeit:"), 1, 0)
            self.efx_speed_combo = QComboBox()
            self.efx_speed_combo.addItems(list(EFFECT_SPEED_MAP.keys()))
            self.efx_speed_combo.setMinimumWidth(100)
            ef.addWidget(self.efx_speed_combo, 1, 1)

            effect_group.setLayout(ef)
            layout.addWidget(effect_group)

            # ── Color Picker ──
            color_group = QGroupBox("Farbe (funktioniert nur bei Static)")
            cl = QHBoxLayout()
            self.color_btn = QPushButton("🎨 Farbe wählen…")
            self.color_btn.setMinimumWidth(140)
            self.color_btn.clicked.connect(self._pick_color)
            self.color_preview = QFrame()
            self.color_preview.setFixedSize(36, 36)
            self.color_preview.setStyleSheet(
                "background-color: rgb(255,100,0); border-radius: 6px; border: 2px solid #555;"
            )
            self.current_color = QColor(255, 100, 0)
            cl.addWidget(self.color_btn)
            cl.addWidget(self.color_preview)
            cl.addStretch()
            color_group.setLayout(cl)
            layout.addWidget(color_group)

            # ── Brightness ──
            bright_group = QGroupBox("LED-Helligkeit")
            bl = QHBoxLayout()
            self.bright_slider = QSlider(Qt.Horizontal)
            self.bright_slider.setRange(0, 100)
            self.bright_slider.setValue(100)
            self.bright_slider.setTickInterval(10)
            self.bright_slider.setTickPosition(QSlider.TicksBelow)
            self.bright_slider.valueChanged.connect(self._on_brightness_changed)
            self.bright_label = QLabel("100%")
            self.bright_label.setMinimumWidth(40)
            self.bright_label.setStyleSheet("color: #e67e22; font-weight: bold;")
            bl.addWidget(self.bright_slider)
            bl.addWidget(self.bright_label)
            bright_group.setLayout(bl)
            layout.addWidget(bright_group)

            # ── Apply ──
            apply_btn = QPushButton("📤 Auf Kanal anwenden")
            apply_btn.setStyleSheet(
                "QPushButton { background-color: #e67e22; color: white; font-weight: bold; "
                "padding: 8px; border-radius: 6px; font-size: 13px; }"
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

        def _on_brightness_changed(self, val):
            self.bright_label.setText(f"{val}%")

        def _on_desc_changed(self):
            self._description = self.desc_edit.text().strip()

        def _pick_color(self):
            c = QColorDialog.getColor(self.current_color, self, "RGB-Farbe wählen")
            if c.isValid():
                self.current_color = c
                self.color_preview.setStyleSheet(
                    f"background-color: rgb({c.red()},{c.green()},{c.blue()}); "
                    f"border-radius: 6px; border: 2px solid #555;"
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

                # Effect mode — map combo text to mode constant
                eff_name = self.effect_combo.currentText()
                mode_key = EFFECT_NAME_TO_MODE.get(eff_name, MODE_FULL)
                efx_spd = EFFECT_SPEED_MAP.get(self.efx_speed_combo.currentText(), SPEED_NORMAL)
                self.ctl.set_mode(self.ch, mode_key, efx_spd)

                # Color (only meaningful for Static) with brightness scaling
                if eff_name == "Static":
                    c = self.current_color
                    brightness = self.bright_slider.value() / 100.0
                    r = int(c.red() * brightness)
                    g = int(c.green() * brightness)
                    b = int(c.blue() * brightness)
                    colors = [(r, g, b)] * LEDS_PER_FAN
                    self.ctl.set_color(self.ch, colors)
            except Exception as e:
                QMessageBox.warning(self, "Fehler", f"Konnte Befehl nicht senden:\n{e}")

        def set_description(self, text: str):
            """Set the channel description text."""
            self._description = text
            self.desc_edit.setText(text)

        def get_description(self) -> str:
            """Get the current channel description."""
            return self.desc_edit.text().strip()


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
            level = self.level_filter.currentText()
            self.log_text.clear()
            if not LOG_FILE or not os.path.exists(LOG_FILE):
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


    class DiagnosticWorker(QThread):
        """Background worker for HID diagnosis — runs diagnose() off GUI thread."""
        finished = pyqtSignal(str)

        def __init__(self, controller):
            super().__init__()
            self.controller = controller

        def run(self):
            try:
                result = self.controller.diagnose()
            except Exception as e:
                result = f"Fehler bei Diagnose: {e}"
            self.finished.emit(result)


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
                "USB-Hilfsdiagnose — zeigt alle gefundenen HID-Geräte."
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
            self.output.setPlainText("Scanne HID-Geräte ...\n")
            self._worker = DiagnosticWorker(self.controller)
            self._worker.finished.connect(self._on_result)
            self._worker.start()

        def _on_result(self, result):
            self.output.setPlainText(result)

        def _save(self):
            path, _ = QFileDialog.getSaveFileName(self, "Diagnose speichern", "tt-diagnose.txt", "Text (*.txt)")
            if path:
                with open(path, "w") as f:
                    f.write(self.output.toPlainText())


    # ── Graph Widget (pyqtgraph) ─────────────────────
    if HAS_PYQTGRAPH:
        class GraphWidget(QWidget):
            """Live graph: temperature + fan speed over last hour."""

            def __init__(self, history, auto_mode, controller, tt_log, parent=None):
                super().__init__(parent)
                self.history = history
                self.auto_mode = auto_mode
                self.controller = controller
                self.tt_log = tt_log
                self._setup_ui()
                # Update graph every 3s
                self._timer = QTimer(self)
                self._timer.timeout.connect(self._update)
                self._timer.start(3000)

            def _setup_ui(self):
                layout = QVBoxLayout(self)

                # Info bar
                info = QHBoxLayout()
                self.temp_label = QLabel("Temp: --°C")
                self.fan_label = QLabel("Fan: --%")
                self.sensor_label = QLabel("Sensor: --")
                info.addWidget(self.temp_label)
                info.addWidget(self.fan_label)
                info.addWidget(self.sensor_label)
                info.addStretch()
                layout.addLayout(info)

                # Plot
                self.plot_widget = PlotWidget()
                self.plot_widget.setBackground('#1e1e1e')
                self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
                self.plot_widget.setLabel('left', 'Value')
                self.plot_widget.setLabel('bottom', 'Time')
                self.plot_widget.addLegend()

                # Temperature curve (red)
                self.temp_curve = self.plot_widget.plot(
                    pen=pg.mkPen(color='#e74c3c', width=2),
                    name='Temperatur (°C)'
                )
                # Fan speed curve (blue)
                self.fan_curve = self.plot_widget.plot(
                    pen=pg.mkPen(color='#3498db', width=2),
                    name='Lüfter (%)'
                )

                # Axis: show time as HH:MM
                axis = pg.DateAxisItem(orientation='bottom')
                self.plot_widget.setAxisItems({'bottom': axis})

                layout.addWidget(self.plot_widget)

                # Curve info
                curve_info = QLabel(
                    f"Fan-Kurve: ≤{FAN_CURVE[0][0]}°C→{FAN_CURVE[0][1]}% … "
                    f"≥{FAN_CURVE[-1][0]}°C→{FAN_CURVE[-1][1]}% | Min: {FAN_MIN}%"
                )
                curve_info.setStyleSheet("color: #888; font-size: 11px;")
                layout.addWidget(curve_info)

            def _update(self):
                """Update graph with latest history data."""
                if not self.history:
                    return
                t_ts, t_val, f_ts, f_val = self.history.get_data()
                if not t_ts:
                    return

                # Convert timestamps to seconds ago for display
                now = time.time()
                t_x = [ts - now for ts in t_ts]
                f_x = [ts - now for ts in f_ts]

                self.temp_curve.setData(t_x, t_val)
                self.fan_curve.setData(f_x, f_val)

                # Update labels
                if t_val:
                    self.temp_label.setText(f"Temp: {t_val[-1]:.1f}°C")
                if f_val:
                    self.fan_label.setText(f"Fan: {f_val[-1]:.0f}%")
                if self.auto_mode and self.auto_mode.current_sensor:
                    self.sensor_label.setText(f"Sensor: {self.auto_mode.current_sensor}")


    class MainWindow(QMainWindow):
        """Thermaltake Riing Plus — Linux Control Centre."""

        def __init__(self):
            super().__init__()
            self.controller = TTController(test_mode=False)

            if self.controller.test_mode:
                self.statusBar().showMessage("⚠️ Kein USB-Gerät gefunden — Demo-Modus aktiv")
            else:
                dev_count = len(self.controller.devs)
                ch_count = self.controller.num_channels
                # Try to read actual controller status (best-effort)
                ctrl_status = self.controller.try_read_status()
                if ctrl_status and ctrl_status.get("readable"):
                    status_text = " (Controller-Auslesung versucht)"
                else:
                    status_text = " — manuelle Konfiguration"
                if dev_count > 1:
                    self.statusBar().showMessage(
                        f"✅ {dev_count} Controller verbunden — {ch_count} Kanäle — bereit{status_text}")
                else:
                    self.statusBar().showMessage(
                        f"✅ Controller verbunden — {ch_count} Kanäle — bereit{status_text}")

            # ── Feature instances ──
            self.profile_mgr = ProfileManager() if HAS_FEATURES else None
            self.auto_mode = AutoMode(self.controller, tt_log) if HAS_FEATURES else None
            self.history = HistoryBuffer() if HAS_FEATURES else None

            self._setup_ui()

        def _setup_ui(self):
            self.setWindowTitle("Thermaltake Riing Plus — Linux Control")
            self.setMinimumSize(780, 600)
            self.setStyleSheet("""
                QMainWindow { background: #2b2b2b; }
                QWidget   { color: #e0e0e0; font-size: 13px; }
                QGroupBox {
                    border: 1px solid #555; border-radius: 6px; margin-top: 8px;
                    font-weight: bold; padding: 12px 8px;
                }
                QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
                QPushButton { background: #3a3a3a; border: 1px solid #555; padding: 6px 12px; border-radius: 4px; color: #e0e0e0; }
                QPushButton:hover { background: #4a4a4a; }
                QPushButton:disabled { background: #2a2a2a; color: #666; }
                QComboBox  { background: #3a3a3a; color: #e0e0e0; padding: 4px 8px; border: 1px solid #555; border-radius: 4px; min-height: 24px; }
                QComboBox::drop-down { border: none; width: 24px; }
                QComboBox QAbstractItemView { background: #3a3a3a; color: #e0e0e0; selection-background-color: #e67e22; }
                QSlider::groove:horizontal { height: 8px; background: #444; border-radius: 4px; }
                QSlider::handle:horizontal { background: #e67e22; width: 16px; margin: -4px 0; border-radius: 3px; }
                QSlider::handle:horizontal:disabled { background: #555; }
                QLabel  { color: #e0e0e0; }
                QStatusBar { background: #1a1a1a; color: #aaa; }
                QTabWidget::pane { border: 1px solid #555; }
                QTabBar::tab { background: #3a3a3a; color: #ccc; padding: 8px 16px; border: 1px solid #555; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }
                QTabBar::tab:selected { background: #4a4a4a; color: #e67e22; font-weight: bold; }
                QTabBar::tab:hover { background: #4a4a4a; }
                QCheckBox { color: #e0e0e0; }
                QCheckBox::indicator { width: 16px; height: 16px; }
            """)

            central = QWidget()
            self.setCentralWidget(central)
            main_layout = QVBoxLayout(central)

            # ── Header ──
            header = QHBoxLayout()
            # App icon (emoji fallback — later replaceable with QIcon)
            app_icon = QLabel("🌈")
            app_icon.setFont(QFont("Sans", 22))
            header.addWidget(app_icon)

            title = QLabel("Thermaltake Riing Plus — Linux Fan & RGB Control")
            title.setFont(QFont("Sans", 18, QFont.Bold))
            title.setStyleSheet("color: #e67e22;")
            header.addWidget(title)
            header.addStretch()

            # ── USB Status: show ALL connected controllers ──
            if self.controller.test_mode:
                usb_text = "🔌 USB: NICHT VERBUNDEN"
                usb_color = "#e74c3c"
            else:
                # Show ALL connected controllers, not just the first one
                controller_names = []
                for _, pid, name in self.controller.devs:
                    controller_names.append(f"{name}")
                if len(controller_names) > 1:
                    usb_text = f"🔌 USB: {' + '.join(controller_names)}"
                else:
                    usb_text = f"🔌 USB: {controller_names[0]}"
                usb_color = "#2ecc71"
            self.usb_status = QLabel(usb_text)
            self.usb_status.setStyleSheet(f"color: {usb_color}; font-size: 12px;")
            header.addWidget(self.usb_status)
            main_layout.addLayout(header)

            # ── Channel Tabs + Graph Tab ──
            self.tabs = QTabWidget()
            self.tab_widgets = []
            # Load channel descriptions
            self._channel_descriptions = {}
            if HAS_FEATURES:
                self._channel_descriptions = load_channel_descriptions()

            for ch in range(self.controller.num_channels):
                nc = ChannelControl(ch, self.controller.num_fans[ch], self.controller)
                # Restore channel description
                desc = self._channel_descriptions.get(str(ch), "")
                if desc:
                    nc.set_description(desc)
                # Connect description change → update tab label
                nc.desc_edit.editingFinished.connect(
                    partial(self._update_tab_label, ch))
                self.tab_widgets.append(nc)
                # Tab label: show description if available
                tab_label = self._make_tab_label(ch, desc)
                self.tabs.addTab(nc, tab_label)

            # Graph tab
            if HAS_FEATURES and HAS_PYQTGRAPH:
                self.graph_widget = GraphWidget(self.history, self.auto_mode, self.controller, tt_log)
                self.tabs.addTab(self.graph_widget, "  📈 Graph  ")
            elif HAS_FEATURES:
                gp = QWidget()
                gp_l = QVBoxLayout(gp)
                gp_l.addWidget(QLabel("📈 Live-Graph — Temperatur & Lüfter über Zeit"))
                gp_l.addSpacing(8)
                info = QLabel(
                    "pyqtgraph ist nicht installiert.\n\n"
                    "Zum Installieren:\n"
                    "  .venv/bin/pip install pyqtgraph\n\n"
                    "Oder im Terminal:\n"
                    "  pip3 install --user pyqtgraph"
                )
                info.setStyleSheet("color: #999; font-size: 12px;")
                info.setWordWrap(True)
                gp_l.addWidget(info)
                install_btn = QPushButton("📦 pyqtgraph installieren")
                install_btn.setStyleSheet(
                    "QPushButton { background: #2980b9; color: white; padding: 8px 16px; "
                    "border-radius: 4px; max-width: 250px; }"
                )
                install_btn.clicked.connect(self._install_pyqtgraph)
                gp_l.addWidget(install_btn)
                gp_l.addStretch()
                self.tabs.addTab(gp, "  📈 Graph  ")
                self.graph_widget = None
            else:
                self.graph_widget = None

            main_layout.addWidget(self.tabs)

            # ── Global Actions ──
            global_group = QGroupBox("Globale Aktionen")
            global_vbox = QVBoxLayout()       # two rows: profiles/buttons + auto-mode

            # ── Row 1: Profiles + Action buttons ──
            gl = QHBoxLayout()

            self.all_apply = QPushButton("✅ Alle anwenden")
            self.all_apply.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
            self.all_apply.clicked.connect(self._apply_all)
            gl.addWidget(self.all_apply)

            self.all_off = QPushButton("⏻ Alles AUS")
            self.all_off.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold;")
            self.all_off.clicked.connect(self._all_off)
            gl.addWidget(self.all_off)

            # ── Profile buttons ──
            if HAS_FEATURES and self.profile_mgr:
                gl.addWidget(QLabel("  |  "))
                self.profile_combo = QComboBox()
                self.profile_combo.addItems(self.profile_mgr.list_profiles())
                self.profile_combo.setMinimumWidth(140)
                gl.addWidget(QLabel("Profil:"))
                gl.addWidget(self.profile_combo)

                load_btn = QPushButton("📂 Profil laden")
                load_btn.setToolTip("Gespeichertes Profil laden und anwenden")
                load_btn.clicked.connect(self._load_profile)
                gl.addWidget(load_btn)

                save_btn = QPushButton("💾 Profil speichern")
                save_btn.setToolTip("Aktuelle Einstellungen als Profil speichern")
                save_btn.clicked.connect(self._save_profile)
                gl.addWidget(save_btn)

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
            global_vbox.addLayout(gl)

            # ── Row 2: Auto mode (own row, no overlap with profiles) ──
            if HAS_FEATURES and self.auto_mode and self.auto_mode.available_sensors:
                auto_group = QGroupBox("🌡 Auto-Modus")
                auto_layout = QVBoxLayout()
                auto_layout.setSpacing(4)
                auto_layout.setContentsMargins(6, 4, 6, 4)

                # Row 1: Checkbox + Sensor dropdown + current temp
                ctrl_row = QHBoxLayout()
                self.auto_cb = QCheckBox("Auto-Modus aktiv")
                self.auto_cb.stateChanged.connect(self._toggle_auto_mode)
                ctrl_row.addWidget(self.auto_cb)

                ctrl_row.addWidget(QLabel("Sensor:"))
                self.auto_sensor_combo = QComboBox()
                self.auto_sensor_combo.setMinimumWidth(180)
                self.auto_sensor_combo.setMaximumWidth(280)
                self._update_sensor_combo()
                self.auto_sensor_combo.currentTextChanged.connect(self._change_auto_sensor)
                ctrl_row.addWidget(self.auto_sensor_combo)

                # Current temperature display
                self.auto_temp_label = QLabel("—°C")
                self.auto_temp_label.setStyleSheet(
                    "color: #e74c3c; font-weight: bold; font-size: 14px; min-width: 50px;")
                ctrl_row.addWidget(self.auto_temp_label)

                # Current auto fan speed display
                self.auto_fan_label = QLabel("Fan: —%")
                self.auto_fan_label.setStyleSheet(
                    "color: #3498db; font-weight: bold; font-size: 14px; min-width: 50px;")
                ctrl_row.addWidget(self.auto_fan_label)

                # Auto-Start switch
                self.autostart_btn = QPushButton("🔄 Aus")
                self.autostart_btn.setToolTip("Auto-Start beim Systemstart aktivieren/deaktivieren")
                self.autostart_btn.setStyleSheet(
                    "QPushButton { background: #555; color: #ccc; padding: 4px 10px; "
                    "border-radius: 4px; font-size: 11px; max-width: 60px; }"
                )
                self.autostart_btn.clicked.connect(self._toggle_autostart)
                ctrl_row.addWidget(self.autostart_btn)

                ctrl_row.addStretch()
                auto_layout.addLayout(ctrl_row)

                # Row 2: All sensor readings (compact, single line)
                self.sensor_table_label = QLabel("Sensoren: …")
                self.sensor_table_label.setStyleSheet("color: #888; font-size: 10px;")
                self.sensor_table_label.setWordWrap(False)
                auto_layout.addWidget(self.sensor_table_label)

                auto_group.setLayout(auto_layout)
                auto_group.setMaximumHeight(85)
                global_vbox.addWidget(auto_group)

                # Sensor live update timer (every 2s)
                self._sensor_timer = QTimer(self)
                self._sensor_timer.timeout.connect(self._update_sensor_display)
                self._sensor_timer.start(2000)
                self._update_sensor_display()

                # Initialize auto-start button state
                self._update_autostart_button()

            global_group.setLayout(global_vbox)
            main_layout.addWidget(global_group)

            # ── Auto-mode & History timer ──
            if HAS_FEATURES and self.auto_mode:
                self._auto_timer = QTimer(self)
                self._auto_timer.timeout.connect(self._auto_tick)
                # Don't start timer here — only when auto mode is enabled

            if HAS_FEATURES and self.history:
                self._history_timer = QTimer(self)
                self._history_timer.timeout.connect(self._history_tick)
                self._history_timer.start(3000)  # 3s interval

        def _make_tab_label(self, ch: int, desc: str) -> str:
            """Generate tab label: 'CH 1 — CPU Radiator' or 'CH 1' if no description."""
            base = f"  CH {ch + 1}  "
            if desc:
                # Truncate long descriptions for tab label
                short = desc[:20] + "…" if len(desc) > 20 else desc
                return f"  CH {ch + 1} — {short}"
            return base

        def _update_tab_label(self, ch: int):
            """Called when channel description changes — update tab text."""
            if ch < len(self.tab_widgets):
                desc = self.tab_widgets[ch].get_description()
                self.tabs.setTabText(ch, self._make_tab_label(ch, desc))

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
            pid_list = ", ".join(f"<code>{p:#06x}</code>" for p in TT_CONTROLLERS)
            udev_line = (
                'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="264a", MODE="0666"'
            )
            QMessageBox.information(self, "Hilfe — Thermaltake RGB Control",
                "<b>Erstmalige Nutzung:</b><br>"
                "1. Stecke den Thermaltake Controller per USB ein<br>"
                "2. Erstelle eine udev-Regel für HID-Zugriff ohne root:<br>"
                f"<code>sudo tee /etc/udev/rules.d/99-thermaltake.rules << 'EOF'<br>"
                f"{udev_line}<br>"
                "EOF</code><br>"
                "3. Reload udev: <code>sudo udevadm control --reload && sudo udevadm trigger</code><br>"
                "4. App neu starten.<br><br>"
                f"<b>Unterstützte Controller:</b> {pid_list}<br><br>"
                "<b>Tipp:</b> Farbe funktioniert nur im 'Static'-Effekt. "
                "Andere Effekte (Flow, Spectrum etc.) benutzen ihre eigenen Farben.<br><br>"
                "<b>Hinweis:</b> PWM-Lüftersteuerung ist experimentell — "
                "OpenRGB unterstützt sie nicht offiziell."
            )

        def _show_log(self):
            dlg = LogWindow(self)
            dlg.exec_()

        def _show_diagnose(self):
            dlg = DiagnosticDialog(self.controller, self)
            dlg.exec_()

        # ── Profile slots ──
        def _load_profile(self):
            name = self.profile_combo.currentText()
            if not name or not self.profile_mgr:
                return
            applied = self.profile_mgr.apply_profile(name, self.controller, tt_log)
            if applied:
                self.statusBar().showMessage(f"Profil '{name}' angewendet ({len(applied)} Kanäle)", 5000)
                # Update GUI sliders to reflect loaded profile
                profile = self.profile_mgr.get_profile(name)
                if profile:
                    for ch_str, data in profile.get("channels", {}).items():
                        ch = int(ch_str)
                        if ch < len(self.tab_widgets):
                            w = self.tab_widgets[ch]
                            w.speed_slider.setValue(data.get("fan_speed", 50))
                            # Update effect combo
                            mode = data.get("mode", 0x19)
                            mode_name = RGB_EFFECTS.get(mode, "Static")
                            idx = w.effect_combo.findText(mode_name)
                            if idx >= 0:
                                w.effect_combo.setCurrentIndex(idx)
                            # Update color for Static mode
                            if mode == 0x19 and "color" in data:
                                r, g, b = data["color"]
                                w.current_color = QColor(r, g, b)
                                w.color_preview.setStyleSheet(
                                    f"background-color: rgb({r},{g},{b}); "
                                    f"border-radius: 6px; border: 2px solid #555;"
                                )
                                colors = [(r, g, b)] * LEDS_PER_FAN
                                w.ring.set_colors(colors)
                            # Update effect speed
                            speed_val = data.get("speed", 0x02)
                            for sp_name, sp_val in EFFECT_SPEED_MAP.items():
                                if sp_val == speed_val:
                                    idx = w.efx_speed_combo.findText(sp_name)
                                    if idx >= 0:
                                        w.efx_speed_combo.setCurrentIndex(idx)
                                    break
                            # Update brightness
                            brightness = data.get("brightness", 100)
                            w.bright_slider.setValue(brightness)
                # NOTE: Channel descriptions are NOT touched by profile load
                # They are stored separately in channel_descriptions.json
            else:
                self.statusBar().showMessage(f"Profil '{name}' konnte nicht angewendet werden", 5000)

        def _save_profile(self):
            name = self.profile_combo.currentText()
            if not name or not self.profile_mgr:
                return
            # Collect current state from all channel widgets
            channels_data = {}
            for ch, w in enumerate(self.tab_widgets):
                eff_name = w.effect_combo.currentText()
                mode_key = EFFECT_NAME_TO_MODE.get(eff_name, MODE_FULL)
                efx_spd = EFFECT_SPEED_MAP.get(w.efx_speed_combo.currentText(), SPEED_NORMAL)
                c = w.current_color
                channels_data[str(ch)] = {
                    "fan_speed": w.speed_slider.value(),
                    "mode": mode_key,
                    "speed": efx_spd,
                    "color": [c.red(), c.green(), c.blue()],
                    "brightness": w.bright_slider.value(),
                }
            self.profile_mgr.save_profile(name, channels_data)
            self.statusBar().showMessage(f"Profil '{name}' gespeichert", 5000)

        # ── Auto mode slots ──
        def _toggle_auto_mode(self, state):
            if not self.auto_mode:
                return
            if state == Qt.Checked:
                # Refresh sensor list before trying to start
                self._update_sensor_combo()
                if self.auto_mode.start():
                    self._auto_timer.start(AUTO_UPDATE_MS)
                    # Disable manual sliders
                    for w in self.tab_widgets:
                        w.speed_slider.setEnabled(False)
                    self.statusBar().showMessage(
                        f"✅ Auto-Modus aktiv — Sensor: {self.auto_mode.current_sensor}", 5000)
                else:
                    self.auto_cb.setChecked(False)
                    QMessageBox.warning(self, "Auto-Modus",
                        "Kein Sensor ausgewählt!\n\n"
                        "Bitte wähle zuerst einen Temperatursensor aus der Liste.")
            else:
                self.auto_mode.stop()
                self._auto_timer.stop()
                for w in self.tab_widgets:
                    w.speed_slider.setEnabled(True)
                # Reset labels
                self.auto_temp_label.setText("—°C")
                self.auto_fan_label.setText("Fan: —%")
                self.statusBar().showMessage("Auto-Modus deaktiviert", 3000)

        def _change_auto_sensor(self, sensor_name):
            if self.auto_mode:
                self.auto_mode.set_sensor(sensor_name)

        def _update_sensor_combo(self):
            """Refresh the sensor dropdown with current readings."""
            if not self.auto_mode:
                return
            self.auto_sensor_combo.blockSignals(True)
            self.auto_sensor_combo.clear()
            readings = self.auto_mode.get_all_sensor_readings()
            for r in readings:
                temp_str = f"{r['temp']:.1f}°C" if r['temp'] is not None else "N/A"
                self.auto_sensor_combo.addItem(f"{r['key']} — {temp_str}", r['key'])
            # Restore selection if possible
            if self.auto_mode.current_sensor:
                idx = self.auto_sensor_combo.findData(self.auto_mode.current_sensor)
                if idx >= 0:
                    self.auto_sensor_combo.setCurrentIndex(idx)
            self.auto_sensor_combo.blockSignals(False)

        def _update_sensor_display(self):
            """Update the live sensor readings + current temp/fan labels."""
            if not self.auto_mode or not self.sensor_table_label:
                return
            readings = self.auto_mode.get_all_sensor_readings()
            if not readings:
                self.sensor_table_label.setText("Sensoren: Keine verfügbar")
                return

            # Compact single-line display of all sensors
            parts = []
            for r in readings:
                temp_str = f"{r['temp']:.1f}°C" if r['temp'] is not None else "N/A"
                marker = " ◀" if r['key'] == self.auto_mode.current_sensor else ""
                parts.append(f"{r['key']}: {temp_str}{marker}")
            self.sensor_table_label.setText("Sensoren: " + " | ".join(parts))

            # Update current temp label
            if self.auto_mode.current_sensor:
                temp = self.auto_mode.get_temperature()
                if temp is not None:
                    self.auto_temp_label.setText(f"{temp:.1f}°C")
                else:
                    self.auto_temp_label.setText("—°C")

            # Update auto fan speed label
            if self.auto_mode.active and self.auto_mode._last_fan_speed is not None:
                self.auto_fan_label.setText(f"Fan: {self.auto_mode._last_fan_speed}%")
            else:
                self.auto_fan_label.setText("Fan: —%")

        def _auto_tick(self):
            """Called by QTimer — runs on GUI thread."""
            if self.auto_mode:
                result = self.auto_mode.tick()
                if result:
                    # Update history
                    if self.history:
                        fan_spd = result.get("fan_speed")
                        temp = result.get("temp")
                        self.history.add(temp, fan_spd)
                    # Update live labels
                    if hasattr(self, 'auto_temp_label'):
                        self.auto_temp_label.setText(f"{result['temp']:.1f}°C")
                    if hasattr(self, 'auto_fan_label'):
                        self.auto_fan_label.setText(f"Fan: {result['fan_speed']}%")

        def _update_autostart_button(self):
            """Check systemd status and update button label."""
            import subprocess
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-enabled", "tt-riing-plus.service"],
                    capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    self.autostart_btn.setText("🔄 An")
                    self.autostart_btn.setStyleSheet(
                        "QPushButton { background: #27ae60; color: white; padding: 4px 10px; "
                        "border-radius: 4px; font-size: 11px; max-width: 60px; }")
                else:
                    self.autostart_btn.setText("🔄 Aus")
                    self.autostart_btn.setStyleSheet(
                        "QPushButton { background: #555; color: #ccc; padding: 4px 10px; "
                        "border-radius: 4px; font-size: 11px; max-width: 60px; }")
            except Exception:
                pass

        def _install_pyqtgraph(self):
            """Install pyqtgraph from the Graph tab button."""
            import subprocess
            script_dir = os.path.dirname(os.path.abspath(__file__))
            venv_pip = os.path.join(script_dir, ".venv", "bin", "pip")
            if not os.path.exists(venv_pip):
                venv_pip = "pip3"
            try:
                subprocess.run([venv_pip, "install", "-q", "pyqtgraph"], check=True, timeout=60)
                QMessageBox.information(self, "pyqtgraph",
                    "pyqtgraph wurde installiert.\n\nBitte die App neu starten.")
            except Exception as e:
                QMessageBox.warning(self, "Fehler",
                    f"Installation fehlgeschlagen:\n{e}\n\n"
                    f"Manuell: {venv_pip} install pyqtgraph")

        def _toggle_autostart(self):
            """Toggle systemd user service for auto-start."""
            import subprocess
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-enabled", "tt-riing-plus.service"],
                    capture_output=True, text=True, timeout=5)
                is_enabled = result.returncode == 0
                if is_enabled:
                    subprocess.run(
                        ["systemctl", "--user", "disable", "--now", "tt-riing-plus.service"],
                        check=True, timeout=5)
                    self.autostart_btn.setText("🔄 Aus")
                    self.autostart_btn.setStyleSheet(
                        "QPushButton { background: #555; color: #ccc; padding: 4px 10px; "
                        "border-radius: 4px; font-size: 11px; max-width: 60px; }")
                    self.statusBar().showMessage("Auto-Start deaktiviert", 3000)
                else:
                    subprocess.run(
                        ["systemctl", "--user", "enable", "--now", "tt-riing-plus.service"],
                        check=True, timeout=5)
                    self.autostart_btn.setText("🔄 An")
                    self.autostart_btn.setStyleSheet(
                        "QPushButton { background: #27ae60; color: white; padding: 4px 10px; "
                        "border-radius: 4px; font-size: 11px; max-width: 60px; }")
                    self.statusBar().showMessage("Auto-Start aktiviert", 3000)
            except Exception as e:
                QMessageBox.warning(self, "Auto-Start",
                    f"Konnte systemd Service nicht ändern:\n{e}")

        def _history_tick(self):
            """Called every 3s — records current temp + fan speed for graph."""
            if not self.history:
                return
            # If auto mode is active, _auto_tick already recorded
            if self.auto_mode and self.auto_mode.active:
                return

            # Read temperature: use auto_mode sensor if available, else try first sensor
            temp = None
            if self.auto_mode:
                if self.auto_mode.current_sensor:
                    temp = self.auto_mode.get_temperature()
                elif self.auto_mode.available_sensors:
                    # Auto-detect: use first available sensor for graph
                    first_sensor = self.auto_mode.available_sensors[0]
                    self.auto_mode.set_sensor(first_sensor)
                    temp = self.auto_mode.get_temperature()

            # Get current fan speed from first channel widget (best-effort)
            fan_speed = 0
            if self.tab_widgets:
                fan_speed = self.tab_widgets[0].speed_slider.value()

            self.history.add(temp, fan_speed)

        def closeEvent(self, event):
            # Stop timers
            if hasattr(self, '_auto_timer'):
                self._auto_timer.stop()
            if hasattr(self, '_history_timer'):
                self._history_timer.stop()
            if hasattr(self, '_sensor_timer'):
                self._sensor_timer.stop()
            # Save channel descriptions
            if HAS_FEATURES:
                descs = {}
                for ch, w in enumerate(self.tab_widgets):
                    d = w.get_description()
                    if d:
                        descs[str(ch)] = d
                save_channel_descriptions(descs)
            self.controller.close()
            event.accept()


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────
def main():
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Thermaltake Riing Plus Control")
        app.setApplicationVersion("2.0.0")
    except Exception as e:
        _print_startup_diag(f"Qt-Init fehlgeschlagen: {e}")
        sys.exit(1)

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
    print(f"\n{'='*50}", file=sys.stderr)
    print("  TT Riing Plus — Startup Fehler", file=sys.stderr)
    print(f"{'='*50}\n", file=sys.stderr)
    print(msg, file=sys.stderr)


def _system_check():
    if not HAS_HIDAPI:
        tt_log("ERROR", "hidapi fehlt! USB nicht verfügbar.")
        print("[FEHLER] hidapi nicht installiert: pip3 install hidapi", file=sys.stderr)

    if not HAS_QT:
        tt_log("ERROR", "PyQt5 fehlt! GUI nicht verfügbar.")
        print("[FEHLER] PyQt5 nicht installiert: sudo apt install python3-pyqt5", file=sys.stderr)

    display = os.environ.get("DISPLAY", "")
    wayland = os.environ.get("WAYLAND_DISPLAY", "")
    if not display and not wayland:
        tt_log("WARNING", "Kein DISPLAY/WAYLAND_DISPLAY — GUI vermutlich nicht sichtbar")

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
    if "--diag" in sys.argv:
        print("🔍 Starte HID-Diagnose (headless) ...\n")
        if not HAS_HIDAPI:
            print("❌ hidapi fehlt: pip3 install hidapi\n")
            sys.exit(1)
        _ctl = TTController.__new__(TTController)
        _ctl.devs = []
        _ctl.ready = False
        _ctl.test_mode = True
        _ctl._fan_count = [1] * MAX_CHANNELS
        _ctl.num_channels = MAX_CHANNELS
        _ctl._detected_pid = None
        _ctl._detected_name = None
        print(_ctl.diagnose())
        sys.exit(0)

    main()
