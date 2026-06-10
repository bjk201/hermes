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
        QDialog, QTextEdit, QPlainTextEdit
    )
    from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
    from PyQt5.QtGui import QColor, QPainter, QBrush, QPen, QFont, QPixmap, QIcon
    HAS_QT = True
except ImportError:
    HAS_QT = False

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
#  Protocol Constants
# ─────────────────────────────────────────────
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

# ── Effect modes (byte 4: mode | speed) ──────────
MODE_STATIC   = 0x00
MODE_BREATH   = 0x01
MODE_WAVE     = 0x02
MODE_RIPPLE   = 0x03
MODE_PULSE    = 0x04
MODE_SPECTRUM = 0x05
MODE_RAINBOW  = 0x06
MODE_REACTIVE = 0x07

# Speed is lower 2 bits of byte 4
SPEED_EXTREME = 0x00
SPEED_FAST    = 0x01
SPEED_NORMAL  = 0x02
SPEED_SLOW    = 0x03

RGB_EFFECTS = {
    MODE_STATIC:   "Static",
    MODE_BREATH:   "Breathing",
    MODE_WAVE:     "Wave",
    MODE_RIPPLE:   "Ripple",
    MODE_PULSE:    "Pulse",
    MODE_SPECTRUM: "Spectrum Cycle",
    MODE_RAINBOW:  "Rainbow Wave",
    MODE_REACTIVE: "Reactive",
}

EFFECT_SPEED_MAP = {
    "Extreme": SPEED_EXTREME,
    "Fast":    SPEED_FAST,
    "Normal":  SPEED_NORMAL,
    "Slow":    SPEED_SLOW,
}

FAN_SPEED_PRESETS = {
    "Silent":      25,
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
      Byte 0:    Report ID (0x00) — prepended by hidapi
      Byte 1:    Command
      Byte 2:    Sub-command
      Byte 3:    Port (1-indexed)
      Byte 4:    Mode | Speed
      Byte 5-40: GRB color data (12 LEDs * 3 bytes)

    Internal state per channel:
      _current_mode[ch]   — effect mode (0x00-0x07)
      _current_speed[ch]  — effect speed (0x00-0x03)
      _current_colors[ch] — list of (R,G,B) tuples
    """

    def __init__(self, test_mode=False):
        self.dev = None
        self.ready = False
        self.test_mode = test_mode
        self._fan_count = [1] * MAX_CHANNELS
        self._detected_pid = None
        self._detected_name = None
        self._detected_path = None
        # Per-channel state
        self._current_mode = [MODE_STATIC] * MAX_CHANNELS
        self._current_speed = [SPEED_NORMAL] * MAX_CHANNELS
        self._current_colors = [[(255, 100, 0)] * LEDS_PER_FAN for _ in range(MAX_CHANNELS)]
        if not test_mode:
            self.connect()

    # ── device discovery ──
    @staticmethod
    def _find_controller():
        """
        Scan for Thermaltake controller using hidapi enumeration.
        Prefers primary PID (0x1fa5). Returns (path_bytes, pid, name) or (None, None, None).
        """
        if not HAS_HIDAPI:
            return None, None, None
        try:
            devices = hid.enumerate()
            # First pass: primary PID
            for d in devices:
                if d.get('vendor_id') == TT_VID and d.get('product_id') == TT_PID_PRIMARY:
                    return d['path'], TT_PID_PRIMARY, TT_CONTROLLERS[TT_PID_PRIMARY]
            # Second pass: any known PID
            for d in devices:
                pid = d.get('product_id')
                if d.get('vendor_id') == TT_VID and pid in TT_CONTROLLERS:
                    return d['path'], pid, TT_CONTROLLERS[pid]
            # Third pass: any TT device
            for d in devices:
                if d.get('vendor_id') == TT_VID:
                    pid = d.get('product_id')
                    return d['path'], pid, f"Unknown TT (PID {pid:#06x})"
        except Exception:
            pass
        return None, None, None

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
        lines.append("\n" + "=" * 50)
        return "\n".join(lines)

    # ── connection ──
    def connect(self) -> bool:
        if not HAS_HIDAPI:
            tt_log("ERROR", "hidapi nicht verfügbar — USB disabled")
            self.test_mode = True
            return False

        path, pid, name = self._find_controller()
        if path is None:
            tt_log("ERROR", "Controller not found — entering test mode")
            self.test_mode = True
            return False

        self._detected_pid = pid
        self._detected_name = name
        self._detected_path = path
        tt_log("INFO", f"Found controller: {name} (PID {pid:#06x})")

        try:
            self.dev = hid.Device(path=path)
            tt_log("INFO", f"Device opened: {self.dev.manufacturer} {self.dev.product}")
        except Exception as e:
            tt_log("ERROR", f"Cannot open device: {e}")
            self.test_mode = True
            return False

        self.ready = True
        self._init_controller()
        tt_log("INFO", "Controller connected and initialized")
        return True

    def _init_controller(self):
        """Send initialization packet."""
        if self.test_mode:
            return
        tt_log("INFO", "Initializing controller ...")
        buf = bytearray(REPORT_PAYLOAD)  # 64 bytes, all zero
        buf[0] = CMD_INIT    # 0xFE
        buf[1] = SUB_INIT    # 0x33
        self._send_payload(bytes(buf))
        time.sleep(0.3)
        try:
            resp = self.dev.read(REPORT_SIZE, timeout=1000)
            tt_log("DEBUG", f"Init response: {len(resp)} bytes — {bytes(resp)[:16].hex()}")
        except Exception as e:
            tt_log("DEBUG", f"Init read: {e}")

    # ── low-level send/receive ──
    def _send_payload(self, payload: bytes):
        """
        Send a 64-byte payload to the controller.
        hidapi prepends Report ID 0x00 automatically.
        """
        if self.test_mode or self.dev is None:
            return
        try:
            # Ensure exactly 64 bytes
            data = payload[:REPORT_PAYLOAD].ljust(REPORT_PAYLOAD, b'\x00')
            self.dev.write(data)
            tt_log("DEBUG", f"hidapi write: {len(data)} bytes")
        except Exception as e:
            tt_log("ERROR", f"hidapi write failed: {e}")

    def _read_response(self, timeout=1000) -> bytes:
        """Read response from controller. Returns raw bytes (without Report ID)."""
        if self.test_mode or self.dev is None:
            return b''
        try:
            resp = self.dev.read(REPORT_SIZE, timeout=timeout)
            return bytes(resp)
        except Exception as e:
            tt_log("DEBUG", f"hidapi read: {e}")
            return b''

    # ── packet builders ──
    def _build_init_packet(self) -> bytes:
        """Build 64-byte init payload."""
        buf = bytearray(REPORT_PAYLOAD)
        buf[0] = CMD_INIT   # 0xFE
        buf[1] = SUB_INIT   # 0x33
        return bytes(buf)

    def _build_rgb_packet(self, port: int, mode: int, speed: int, colors: list) -> bytes:
        """
        Build 64-byte RGB color payload.
        port: 1-indexed channel number
        mode: effect mode (0x00-0x07)
        speed: effect speed (0x00-0x03)
        colors: list of (R, G, B) tuples, up to LEDS_PER_FAN
        """
        buf = bytearray(REPORT_PAYLOAD)
        buf[0] = CMD_RGB                    # 0x32
        buf[1] = SUB_RGB                     # 0x52
        buf[2] = port                        # 1-indexed
        buf[3] = mode | (speed & 0x03)      # mode + speed combined
        # Fill GRB color data starting at byte 4
        for i, (r, g, b) in enumerate(colors[:LEDS_PER_FAN]):
            idx = 4 + i * 3
            buf[idx + 0] = g  # G first (GRB order)
            buf[idx + 1] = r
            buf[idx + 2] = b
        return bytes(buf)

    def _build_fan_packet(self, port: int, percent: int) -> bytes:
        """
        Build 64-byte fan speed payload.
        port: 1-indexed channel number
        percent: 0-100
        """
        buf = bytearray(REPORT_PAYLOAD)
        buf[0] = CMD_FAN                     # 0x33
        buf[1] = SUB_FAN_PWM                 # 0x56
        buf[2] = port                        # 1-indexed
        buf[3] = int(percent * 255 / 100)   # PWM 0-255
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
        payload = self._build_rgb_packet(channel + 1, mode, speed, colors)
        self._send_payload(payload)
        tt_log("INFO", f"set_color ch={channel} mode={mode} speed={speed} first=({colors[0] if colors else '?'})")

    def set_mode(self, channel: int, mode: int, speed: int = SPEED_NORMAL):
        """Set lighting effect mode and speed for a channel. Does NOT change colors."""
        self._current_mode[channel] = mode
        self._current_speed[channel] = speed & 0x03
        # Re-send current colors with new mode
        colors = self._current_colors[channel]
        payload = self._build_rgb_packet(channel + 1, mode, self._current_speed[channel], colors)
        self._send_payload(payload)
        mode_name = RGB_EFFECTS.get(mode, f"0x{mode:02x}")
        tt_log("INFO", f"set_mode ch={channel} mode={mode_name} speed={speed}")

    def set_speed(self, channel: int, percent: int):
        """Set PWM fan speed for one channel (0-100%)."""
        val = max(0, min(100, percent))
        payload = self._build_fan_packet(channel + 1, val)
        self._send_payload(payload)
        tt_log("INFO", f"set_speed ch={channel} percent={val}%")

    def apply(self):
        """No-op in this protocol — each command is immediate."""
        pass

    def all_off(self):
        """Turn off all LEDs and stop all fans."""
        for ch in range(MAX_CHANNELS):
            self.set_speed(ch, 0)
            black = [(0, 0, 0)] * LEDS_PER_FAN
            self.set_color(ch, black)

    def close(self):
        if self.dev and not self.test_mode:
            try:
                self.dev.close()
            except Exception:
                pass

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
        """Full controls for one channel: speed, color, effect."""

        def __init__(self, channel_idx: int, num_fans: int, controller: TTController, parent=None):
            super().__init__(parent)
            self.ch = channel_idx
            self.nf = num_fans
            self.ctl = controller
            self._setup_ui()

        def _setup_ui(self):
            layout = QVBoxLayout(self)
            layout.setSpacing(8)

            # Ring preview
            preview_box = QHBoxLayout()
            preview_box.addWidget(QLabel(f"CH{self.ch + 1}:"))
            self.ring = RingWidget(LEDS_PER_FAN)
            preview_box.addWidget(self.ring)
            self.fan_label = QLabel(f"({self.nf} Lüfter)")
            preview_box.addWidget(self.fan_label)
            preview_box.addStretch()
            layout.addLayout(preview_box)

            # Fan Speed
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

            # Speed presets
            preset_row = QHBoxLayout()
            for name, val in FAN_SPEED_PRESETS.items():
                btn = QPushButton(name)
                btn.clicked.connect(partial(self._set_preset, val, name))
                preset_row.addWidget(btn)
            layout.addLayout(preset_row)

            # RGB Effect
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
            effect_bot.addStretch()
            ef.addLayout(effect_bot)
            effect_group.setLayout(ef)
            layout.addWidget(effect_group)

            # Color Picker
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

            # Apply
            apply_btn = QPushButton("⚠️ Auf Kanal anwenden")
            apply_btn.setStyleSheet(
                "QPushButton { background-color: #e67e22; color: white; font-weight: bold;"
                "padding: 8px; border-radius: 6px; }"
                "QPushButton:hover { background-color: #d35400; }"
            )
            apply_btn.clicked.connect(self._apply)
            layout.addWidget(apply_btn)
            layout.addStretch()

        def _on_speed_changed(self, val):
            self.speed_label.setText(f"{val}%")

        def _on_effect_changed(self, effect_name):
            is_static = effect_name == "Static"
            self.color_btn.setEnabled(is_static)
            self.color_preview.setEnabled(is_static)

        def _set_preset(self, value, name):
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
            if self.ctl.test_mode:
                QMessageBox.information(self, "Demo-Modus",
                    "USB-Gerät nicht gefunden — Einstellungen würden gesendet werden.")
                return
            try:
                self.ctl.set_speed(self.ch, self.speed_slider.value())
                eff_name = self.effect_combo.currentText()
                mode_key = [k for k, v in RGB_EFFECTS.items() if v == eff_name][0]
                efx_spd = EFFECT_SPEED_MAP.get(self.efx_speed_combo.currentText(), SPEED_NORMAL)
                self.ctl.set_mode(self.ch, mode_key, efx_spd)
                if eff_name == "Static":
                    c = self.current_color
                    colors = [(c.red(), c.green(), c.blue())] * LEDS_PER_FAN
                    self.ctl.set_color(self.ch, colors)
            except Exception as e:
                QMessageBox.warning(self, "Fehler", f"Konnte Befehl nicht senden:\n{e}")


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
            self.log_text = QPlainTextEdit()
            self.log_text.setReadOnly(True)
            self.log_text.setLineWrapMode(QPlainTextEdit.NoWrap)
            self.log_text.setMaximumBlockCount(2000)
            lay.addWidget(self.log_text)
            self._log_timer = QTimer(self)
            self._log_timer.timeout.connect(self._poll_log_queue)
            self._log_timer.start(200)

        def _poll_log_queue(self):
            """Drain the log queue — called on GUI thread via QTimer."""
            while True:
                try:
                    level, msg = _log_queue.get_nowait()
                except queue.Empty:
                    break
                colors = {"DEBUG": "#888", "INFO": "#aaa", "WARNING": "#f39c12", "ERROR": "#e74c3c"}
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


    class DiagnosticDialog(QDialog):
        """Shows output of controller.diagnose()."""

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
            info = QLabel("USB-Hilfsdiagnose — zeigt alle gefundenen USB-Geräte.")
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
            def _do():
                result = self.controller.diagnose()
                self.output.setPlainText(result)
            threading.Thread(target=_do, daemon=True).start()

        def _save(self):
            path, _ = QFileDialog.getSaveFileName(self, "Diagnose speichern", "tt-diagnose.txt", "Text (*.txt)")
            if path:
                with open(path, "w") as f:
                    f.write(self.output.toPlainText())


    class MainWindow(QMainWindow):
        """Thermaltake Riing Plus — Linux Control Centre."""

        def __init__(self):
            super().__init__()
            self.controller = TTController(test_mode=False)
            if self.controller.test_mode:
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
                QGroupBox { border: 1px solid #555; border-radius: 6px; margin-top: 8px; font-weight: bold; padding: 12px 8px; }
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

            # Header
            header = QHBoxLayout()
            title = QLabel("🌈 Thermaltake Riing Plus — Linux Fan & RGB Control")
            title.setFont(QFont("Sans", 18, QFont.Bold))
            title.setStyleSheet("color: #e67e22;")
            header.addWidget(title)
            header.addStretch()
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

            # Channel Tabs
            self.tabs = QTabWidget()
            self.tab_widgets = []
            for ch in range(MAX_CHANNELS):
                nc = ChannelControl(ch, self.controller.num_fans[ch], self.controller)
                self.tab_widgets.append(nc)
                self.tabs.addTab(nc, f"  CH {ch + 1}  ")
            main_layout.addWidget(self.tabs)

            # Global Actions
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
            for w in self.tab_widgets:
                w._apply()

        def _all_off(self):
            if self.controller.test_mode:
                QMessageBox.information(self, "Demo-Modus", "USB-Gerät nicht verbunden — nichts zu tun.")
                return
            self.controller.all_off()
            self.statusBar().showMessage("Alle Lüfter & LEDs ausgeschaltet", 3000)

        def _show_help(self):
            pid_list = ", ".join(f"<code>{p:#06x}</code>" for p in TT_CONTROLLERS)
            QMessageBox.information(self, "Hilfe — Thermaltake RGB Control",
                "<b>Erstmalige Nutzung:</b><br>"
                "1. Stecke den Thermaltake Controller per USB ein<br>"
                "2. Erstelle eine udev-Regel:<br>"
                "<code>sudo tee /etc/udev/rules.d/99-thermaltake.rules << 'EOF'<br>"
                'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="264a", MODE="0666"<br>'
                "EOF</code><br>"
                "3. Reload udev: <code>sudo udevadm control --reload && sudo udevadm trigger</code><br>"
                "4. App neu starten.<br><br>"
                f"<b>Unterstützte Controller:</b> {pid_list}<br><br>"
                "<b>Tipp:</b> Farbe funktioniert nur im 'Static'-Effekt."
            )

        def _show_log(self):
            dlg = LogWindow(self)
            dlg.exec_()

        def _show_diagnose(self):
            dlg = DiagnosticDialog(self.controller, self)
            dlg.exec_()

        def closeEvent(self, event):
            self.controller.close()
            event.accept()


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────
def main():
    _system_check()
    if not HAS_QT:
        _print_startup_diag("PyQt5 nicht verfügbar — GUI kann nicht starten.\n"
                            "Installieren: sudo apt install python3-pyqt5")
        sys.exit(1)
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Thermaltake Riing Plus Control")
        app.setApplicationVersion("2.0.0")
    except Exception as e:
        _print_startup_diag(f"Qt-Init fehlgeschlagen: {e}")
        sys.exit(1)
    try:
        window = MainWindow()
        window.show()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        tt_log("ERROR", f"Startup crash: {e}\n{tb}")
        try:
            QMessageBox.critical(None, "Startup Error",
                f"App konnte nicht starten:\n\n{e}\n\nLog: {LOG_FILE or '(kein Log)'}")
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
        tt_log("ERROR", "hidapi nicht verfügbar — pip3 install hidapi")
    if not HAS_QT:
        tt_log("ERROR", "PyQt5 fehlt! GUI nicht verfügbar.")
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
    except Exception as e:
        tt_log("WARNING", f"Log nicht beschreibbar: {e}")

if __name__ == "__main__":
    if "--diag" in sys.argv:
        print("🔍 Starte USB-Diagnose (headless) ...\n")
        _ctl = TTController.__new__(TTController)
        _ctl.dev = None
        _ctl.ready = False
        _ctl.test_mode = True
        _ctl._fan_count = [1] * MAX_CHANNELS
        _ctl._detected_pid = None
        _ctl._detected_name = None
        _ctl._detected_path = None
        _ctl._current_mode = [MODE_STATIC] * MAX_CHANNELS
        _ctl._current_speed = [SPEED_NORMAL] * MAX_CHANNELS
        _ctl._current_colors = [[(255, 100, 0)] * LEDS_PER_FAN for _ in range(MAX_CHANNELS)]
        print(_ctl.diagnose())
        sys.exit(0)
    main()
