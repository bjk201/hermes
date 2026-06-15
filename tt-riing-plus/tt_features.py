"""
TT Riing Plus — Extended Features
  ProfileManager  : JSON save/load/apply
  AutoMode        : psutil temperature → fan curve
  HistoryGraph    : pyqtgraph live graph (1h ring buffer)

Separate file to keep tt_riing_plus.py untouched.
"""

import json
import os
import time
import collections
import datetime

# ── Profile Manager ──────────────────────────────────

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".config", "tt-riing-plus")
PROFILE_FILE = os.path.join(PROFILE_DIR, "profiles.json")

DEFAULT_PROFILES = {
    "Silent": {
        "channels": {
            "0": {"fan_speed": 20, "mode": 0x19, "speed": 0x02, "color": [255, 100, 0]},
            "1": {"fan_speed": 20, "mode": 0x19, "speed": 0x02, "color": [255, 100, 0]},
            "2": {"fan_speed": 20, "mode": 0x19, "speed": 0x02, "color": [255, 100, 0]},
            "3": {"fan_speed": 20, "mode": 0x19, "speed": 0x02, "color": [255, 100, 0]},
            "4": {"fan_speed": 20, "mode": 0x19, "speed": 0x02, "color": [255, 100, 0]},
        }
    },
    "Balanced": {
        "channels": {
            "0": {"fan_speed": 50, "mode": 0x19, "speed": 0x02, "color": [255, 100, 0]},
            "1": {"fan_speed": 50, "mode": 0x19, "speed": 0x02, "color": [255, 100, 0]},
            "2": {"fan_speed": 50, "mode": 0x19, "speed": 0x02, "color": [255, 100, 0]},
            "3": {"fan_speed": 50, "mode": 0x19, "speed": 0x02, "color": [255, 100, 0]},
            "4": {"fan_speed": 50, "mode": 0x19, "speed": 0x02, "color": [255, 100, 0]},
        }
    },
    "Gaming": {
        "channels": {
            "0": {"fan_speed": 75, "mode": 0x00, "speed": 0x01, "color": [255, 0, 0]},
            "1": {"fan_speed": 75, "mode": 0x00, "speed": 0x01, "color": [255, 0, 0]},
            "2": {"fan_speed": 75, "mode": 0x00, "speed": 0x01, "color": [255, 0, 0]},
            "3": {"fan_speed": 75, "mode": 0x00, "speed": 0x01, "color": [255, 0, 0]},
            "4": {"fan_speed": 75, "mode": 0x00, "speed": 0x01, "color": [255, 0, 0]},
        }
    },
}


class ProfileManager:
    """Save/load/apply fan+RGB profiles as JSON."""

    def __init__(self, profile_file: str = PROFILE_FILE):
        self.profile_file = profile_file
        self.profiles = {}
        self._load()

    def _load(self):
        """Load profiles from JSON, fall back to defaults."""
        try:
            if os.path.exists(self.profile_file):
                with open(self.profile_file, "r") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and raw:
                    self.profiles = raw
                    return
        except Exception:
            pass
        # No file or invalid — use defaults
        self.profiles = dict(DEFAULT_PROFILES)
        self._save()

    def _save(self):
        """Write profiles to JSON."""
        try:
            os.makedirs(os.path.dirname(self.profile_file), exist_ok=True)
            with open(self.profile_file, "w") as f:
                json.dump(self.profiles, f, indent=2)
        except Exception:
            pass

    def list_profiles(self):
        """Return list of profile names."""
        return list(self.profiles.keys())

    def get_profile(self, name: str) -> dict | None:
        """Return profile dict or None."""
        return self.profiles.get(name)

    def save_profile(self, name: str, channels_data: dict):
        """
        Save a profile from current channel state.
        channels_data: { "0": {"fan_speed": 50, "mode": 0x19, ...}, ... }
        """
        self.profiles[name] = {"channels": channels_data}
        self._save()

    def delete_profile(self, name: str) -> bool:
        """Delete a profile. Returns False if it was a built-in."""
        if name in DEFAULT_PROFILES:
            return False
        self.profiles.pop(name, None)
        self._save()
        return True

    def apply_profile(self, name: str, controller, tt_log) -> list:
        """
        Apply a profile to the controller.
        Returns list of (channel, fan_speed, mode) tuples for channels that were set.
        NOTE: Does NOT touch channel descriptions — those are independent of profiles.
        """
        profile = self.profiles.get(name)
        if not profile:
            tt_log("WARNING", f"Profile '{name}' not found")
            return []

        channels = profile.get("channels", {})
        applied = []

        for ch_str, data in channels.items():
            ch = int(ch_str)
            fan_speed = data.get("fan_speed", 50)
            mode = data.get("mode", 0x19)
            speed = data.get("speed", 0x02)
            color = data.get("color", [255, 100, 0])

            # Fan speed
            controller.set_speed(ch, fan_speed)

            # RGB mode
            controller.set_mode(ch, mode, speed)

            # Color (for Static mode)
            if mode == 0x19:
                colors = [tuple(color)] * 12
                controller.set_color(ch, colors)

            applied.append((ch, fan_speed, mode))

        tt_log("INFO", f"Profile '{name}' applied: {len(applied)} channels")
        return applied


# ── Channel Descriptions ──────────────────────────────

DESC_FILE = os.path.join(PROFILE_DIR, "channel_descriptions.json")


def load_channel_descriptions() -> dict:
    """Load channel descriptions from JSON. Returns {channel_idx_str: "description"}."""
    try:
        if os.path.exists(DESC_FILE):
            with open(DESC_FILE, "r") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return {}


def save_channel_descriptions(descriptions: dict):
    """Save channel descriptions to JSON. descriptions: {channel_idx_str: "text"}."""
    try:
        os.makedirs(os.path.dirname(DESC_FILE), exist_ok=True)
        with open(DESC_FILE, "w") as f:
            json.dump(descriptions, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Fan curve: temp(C) -> fan_percent
FAN_CURVE = [
    (40, 50),
    (50, 60),
    (60, 75),
    (70, 90),
    (80, 100),
]

# Minimum fan speed (hardware limit)
FAN_MIN = 50

# Hysteresis: only send if change >= this value
FAN_HYSTERESIS = 3

# Update interval in ms
AUTO_UPDATE_MS = 3000


class AutoMode:
    """
    Temperature-based automatic fan control.
    Reads CPU temperature via psutil and applies fan curve.
    """

    def __init__(self, controller, tt_log):
        self.controller = controller
        self.tt_log = tt_log
        self.active = False
        self._last_fan_speed = None
        self._sensor_name = None
        self._available_sensors = {}
        self._detect_sensors()

    def _detect_sensors(self):
        """Detect available temperature sensors via psutil. Does NOT auto-pick one."""
        if not HAS_PSUTIL:
            return

        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return

            # Collect all available sensors
            for label, entries in temps.items():
                for entry in entries:
                    key = f"{label}_{entry.label}" if entry.label else label
                    self._available_sensors[key] = (label, entry.label or key)

            self.tt_log("INFO", f"Auto-Modus: {len(self._available_sensors)} Sensoren gefunden")

        except Exception as e:
            self.tt_log("WARNING", f"Auto-Modus: Sensor-Erkennung fehlgeschlagen: {e}")

    @property
    def available_sensors(self) -> list:
        """Return list of available sensor names."""
        return list(self._available_sensors.keys())

    @property
    def current_sensor(self) -> str | None:
        return self._sensor_name

    def set_sensor(self, sensor_name: str):
        """Set temperature sensor by name."""
        if sensor_name in self._available_sensors:
            self._sensor_name = sensor_name
            self.tt_log("INFO", f"Auto-Modus: Sensor gewechselt — {sensor_name}")

    def get_all_sensor_readings(self) -> list:
        """
        Read ALL temperature sensors and return list of dicts.
        Each dict: {"key": "k10temp_Tctl", "label": "Tctl", "temp": 45.0}
        """
        if not HAS_PSUTIL:
            return []
        readings = []
        try:
            temps = psutil.sensors_temperatures()
            for key, (label, entry_label) in self._available_sensors.items():
                if label in temps:
                    for entry in temps[label]:
                        if (entry.label or label) == entry_label:
                            readings.append({
                                "key": key,
                                "label": entry.label or label,
                                "temp": entry.current,
                            })
                            break
        except Exception:
            pass
        return readings

    def get_temperature(self) -> float | None:
        """Read current temperature from selected sensor."""
        if not HAS_PSUTIL or not self._sensor_name:
            return None
        try:
            temps = psutil.sensors_temperatures()
            label, entry_label = self._available_sensors[self._sensor_name]
            if label in temps:
                for entry in temps[label]:
                    if (entry.label or label) == entry_label:
                        return entry.current
        except Exception:
            pass
        return None

    def calc_fan_speed(self, temp: float) -> int:
        """
        Calculate fan speed from temperature using linear interpolation.
        Respects FAN_MIN clamp.
        """
        if temp <= FAN_CURVE[0][0]:
            return FAN_CURVE[0][1]
        if temp >= FAN_CURVE[-1][0]:
            return FAN_CURVE[-1][1]

        for i in range(len(FAN_CURVE) - 1):
            t1, f1 = FAN_CURVE[i]
            t2, f2 = FAN_CURVE[i + 1]
            if t1 <= temp <= t2:
                ratio = (temp - t1) / (t2 - t1)
                speed = f1 + ratio * (f2 - f1)
                return max(FAN_MIN, int(speed))

        return FAN_CURVE[-1][1]

    def tick(self) -> dict | None:
        """
        One update cycle: read temp, calc speed, send if changed.
        Returns dict with temp/speed info or None on error.
        """
        if not self.active:
            return None

        temp = self.get_temperature()
        if temp is None:
            return None

        target_speed = self.calc_fan_speed(temp)

        # Hysteresis: only send if change is significant
        if (self._last_fan_speed is None or
                abs(target_speed - self._last_fan_speed) >= FAN_HYSTERESIS):
            for ch in range(self.controller.num_channels):
                self.controller.set_speed(ch, target_speed)
            self._last_fan_speed = target_speed
            self.tt_log("INFO",
                        f"Auto-Modus: {temp:.1f}°C → {target_speed}% (Δ{FAN_HYSTERESIS}%)")

        return {
            "temp": temp,
            "fan_speed": target_speed,
            "sensor": self._sensor_name,
        }

    def start(self):
        """Enable auto mode. Only works if a sensor is explicitly selected."""
        if not HAS_PSUTIL:
            self.tt_log("WARNING", "Auto-Modus: psutil nicht verfügbar — pip3 install psutil")
            return False
        if not self._sensor_name:
            self.tt_log("WARNING", "Auto-Modus: Kein Sensor ausgewählt — bitte Temperaturquelle wählen")
            return False
        self.active = True
        self._last_fan_speed = None
        self.tt_log("INFO", f"Auto-Modus AKTIV — Sensor: {self._sensor_name}")
        return True

    def stop(self):
        """Disable auto mode."""
        self.active = False
        self.tt_log("INFO", "Auto-Modus DEAKTIVIERT")


# ── History / Graph ──────────────────────────────────

# Ring buffer: 1 hour at 1 sample/3s = 1200 points
HISTORY_SECONDS = 3600
HISTORY_MAX_POINTS = HISTORY_SECONDS // 3

HistoryPoint = collections.namedtuple("HistoryPoint", ["ts", "temp", "fan_speed"])


class HistoryBuffer:
    """Ring buffer for temperature and fan speed history."""

    def __init__(self, max_points=HISTORY_MAX_POINTS):
        self.max_points = max_points
        self.temp_data = collections.deque(maxlen=max_points)
        self.fan_data = collections.deque(maxlen=max_points)

    def add(self, temp: float | None, fan_speed: int | None):
        """Add a data point with current timestamp."""
        ts = time.time()
        self.temp_data.append((ts, temp if temp is not None else 0))
        self.fan_data.append((ts, fan_speed if fan_speed is not None else 0))

    def get_data(self):
        """Return (temp_timestamps, temp_values, fan_timestamps, fan_values)."""
        if not self.temp_data:
            return [], [], [], []
        t_ts, t_val = zip(*self.temp_data)
        f_ts, f_val = zip(*self.fan_data)
        return list(t_ts), list(t_val), list(f_ts), list(f_val)

    def clear(self):
        self.temp_data.clear()
        self.fan_data.clear()


# ── pyqtgraph Graph Widget (only if pyqtgraph available) ──

try:
    import pyqtgraph as pg
    from pyqtgraph import PlotWidget, PlotDataItem
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False
