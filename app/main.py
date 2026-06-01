#!/usr/bin/env python3
"""PV-Amortisations-Rechner: Home Assistant Energy CSV Import + Amortisation"""

import os
import io
import csv
import logging
from datetime import datetime, date
from decimal import Decimal
from calendar import monthrange
from collections import defaultdict

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, extract, and_

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pvrechner")

# ── Sensor-Konfiguration ──────────────────────────────────────────────────────

PV_PRODUCTION_SENSORS = {
    "sma_wechselrichter_pv_gen_meter",
    "victronsolarcharger_yield_today229",
    "victronsolarcharger_yield_today239",
}

BATTERY_IN_IDS = {"battery_in", "speicher_basengreen_input"}
BATTERY_OUT_IDS = {"battery_out", "speicher_basengreen_output"}

# Berechnete Typen (type-Spalte bei leerem entity_id) → DB-Feld
CALCULATED_TYPE_TO_FIELD = {
    "calculated_consumed_solar":   "consumed_solar_kwh",
    "calculated_consumption":      "consumption_kwh",
    "calculated_solar_to_battery": "solar_to_battery_kwh",
    "calculated_solar_to_grid":    "solar_to_grid_kwh",
    "calculated_consumed_battery": "battery_usage_kwh",
    "calculated_consumed_grid":    "grid_to_house_kwh",
    "calculated_total_consumption":"consumption_kwh",
}

# ── App Config ───────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-2026")

DB_USER = os.environ.get("POSTGRES_USER", "pvuser")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "pvpass")
DB_HOST = os.environ.get("POSTGRES_HOST", "db")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_NAME = os.environ.get("POSTGRES_DB", "pvrechner")

app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"postgresql://{DB_USER}:***@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

_app_pw = os.environ.get("APP_PASSWORD", "pv2024")
_app_pw_hash = None


def get_pw_hash():
    global _app_pw_hash
    if _app_pw_hash is None:
        from werkzeug.security import generate_password_hash
        _app_pw_hash = generate_password_hash(_app_pw)
    return _app_pw_hash


db = SQLAlchemy(app)


# ── Models ────────────────────────────────────────────────────────────────────

class DailyEnergy(db.Model):
    __tablename__ = "daily_energy"
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, unique=True, index=True)
    pv_production_kwh = db.Column(db.Numeric(12, 4), default=0)
    battery_in_kwh = db.Column(db.Numeric(12, 4), default=0)
    battery_out_kwh = db.Column(db.Numeric(12, 4), default=0)
    grid_to_house_kwh = db.Column(db.Numeric(12, 4), default=0)
    consumed_solar_kwh = db.Column(db.Numeric(12, 4), default=0)
    consumption_kwh = db.Column(db.Numeric(12, 4), default=0)
    solar_to_battery_kwh = db.Column(db.Numeric(12, 4), default=0)
    solar_to_grid_kwh = db.Column(db.Numeric(12, 4), default=0)
    battery_usage_kwh = db.Column(db.Numeric(12, 4), default=0)


class ElectricityRate(db.Model):
    __tablename__ = "electricity_rates"
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    price_per_kwh = db.Column(db.Numeric(10, 4), nullable=False)
    monthly_base_fee = db.Column(db.Numeric(10, 2), default=0)
    label = db.Column(db.String(100))


class FeedInTariff(db.Model):
    __tablename__ = "feed_in_tariffs"
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    price_per_kwh = db.Column(db.Numeric(10, 4), nullable=False)
    label = db.Column(db.String(100))


class ManualCost(db.Model):
    __tablename__ = "manual_costs"
    id = db.Column(db.Integer, primary_key=True)
    cost_date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    category = db.Column(db.String(50), default="Sonstiges")
    description = db.Column(db.String(255))


class ImportLog(db.Model):
    __tablename__ = "import_logs"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255))
    year = db.Column(db.Integer)
    month = db.Column(db.Integer)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)
    records_count = db.Column(db.Integer)
    source = db.Column(db.String(50), default="csv")  # "csv" or "ha_api"


class Settings(db.Model):
    """Key-Value Store für HA-Konfiguration."""
    __tablename__ = "settings"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, default="")

    @staticmethod
    def get(key, default=""):
        rec = Settings.query.filter_by(key=key).first()
        return rec.value if rec else default

    @staticmethod
    def set(key, value):
        rec = Settings.query.filter_by(key=key).first()
        if rec:
            rec.value = value
        else:
            db.session.add(Settings(key=key, value=value))
        db.session.commit()


# ── HA Settings Helper ────────────────────────────────────────────────────────

def get_ha_settings() -> dict:
    """Gibt alle HA-relevanten Settings als dict zurück."""
    keys = [
        "ha_url", "ha_token",
        "ha_sensor_pv_production",
        "ha_sensor_battery_in", "ha_sensor_battery_out",
        "ha_sensor_grid_to_house", "ha_sensor_consumed_solar",
        "ha_sensor_consumption", "ha_sensor_solar_to_battery",
        "ha_sensor_solar_to_grid", "ha_sensor_battery_usage",
    ]
    return {k: Settings.get(k, "") for k in keys}


# ── HA API Client ─────────────────────────────────────────────────────────────

import urllib.request
import urllib.error
import json as _json

# HA History API: aggregiert einen Tag (Start → End, 00:00–23:59)
# Wir nutzen /api/history/period/{start} mit filter_entity_id + minimal_response

_HA_SENSOR_FIELDS: dict[str, str] = {
    "ha_sensor_pv_production":    "pv_production_kwh",
    "ha_sensor_battery_in":       "battery_in_kwh",
    "ha_sensor_battery_out":      "battery_out_kwh",
    "ha_sensor_grid_to_house":    "grid_to_house_kwh",
    "ha_sensor_consumed_solar":   "consumed_solar_kwh",
    "ha_sensor_consumption":      "consumption_kwh",
    "ha_sensor_solar_to_battery": "solar_to_battery_kwh",
    "ha_sensor_solar_to_grid":    "solar_to_grid_kwh",
    "ha_sensor_battery_usage":    "battery_usage_kwh",
}


def ha_api_fetch_day(target_date: date, settings: dict) -> dict | None:
    """
    Ruft die HA History API für einen Tag ab.
    Gibt dict[db_field, float] oder None bei Fehler zurück.
    """
    ha_url = settings.get("ha_url", "").rstrip("/")
    ha_token = settings.get("ha_token", "")
    if not ha_url or not ha_token:
        log.warning("HA URL oder Token nicht konfiguriert.")
        return None

    # Zeitraum: 00:00:00 → 23:59:59 des target_date (UTC ISO Format)
    start_dt = datetime.combine(target_date, datetime.min.time()).strftime("%Y-%m-%dT%H:%M:%S")
    end_dt   = datetime.combine(target_date, datetime.max.time()).strftime("%Y-%m-%dT%H:%M:%S")

    result: dict[str, float] = {}

    for setting_key, db_field in _HA_SENSOR_FIELDS.items():
        entity_id = settings.get(setting_key, "").strip()
        if not entity_id:
            continue

        url = (
            f"{ha_url}/api/history/period/{start_dt}"
            f"?filter_entity_id={entity_id}"
            f"&minimal_response"
            f"&significant_changes_only=0"
            f"&end_time={end_dt}"
        )
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {ha_token}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = _json.loads(resp.read().decode())
            # data = [[{state, last_changed, ...}, ...]]
            entries = data[0] if data else []
            if len(entries) >= 2:
                # Differenz letzter → erster Wert des Tages = Verbrauch/Produktion
                try:
                    first_val = float(entries[0].get("state", 0))
                except (ValueError, TypeError):
                    first_val = 0.0
                try:
                    last_val  = float(entries[-1].get("state", 0))
                except (ValueError, TypeError):
                    last_val = 0.0
                diff = last_val - first_val
                if diff < 0:
                    diff = 0.0  # Counter-Reset o.ä.
                result[db_field] = round(diff, 4)
        except Exception as exc:
            log.warning("HA API Fehler für %s: %s", entity_id, exc)

    return result if result else None


def ha_import_day(target_date: date) -> tuple[int, str]:
    """
    Importiert Daten eines Tages von HA → daily_energy.
    Gibt (0, error_msg) oder (1, success_msg) zurück.
    """
    settings = get_ha_settings()
    if not settings.get("ha_url"):
        return 0, "HA URL nicht konfiguriert."

    data = ha_api_fetch_day(target_date, settings)
    if not data:
        return 0, f"Keine Daten von HA für {target_date} erhalten."

    rec = DailyEnergy.query.filter_by(day=target_date).first()
    if rec:
        for k, v in data.items():
            setattr(rec, k, v)
    else:
        rec = DailyEnergy(day=target_date, **data)
        db.session.add(rec)

    db.session.add(ImportLog(
        filename=f"HA-API-{target_date}",
        year=target_date.year,
        month=target_date.month,
        records_count=1,
        source="ha_api",
    ))
    db.session.commit()
    return 1, f"✓ {target_date}: {len(data)} Werte importiert."

def _parse_date_header(header):
    """Erkennt Datumsspalten. Gibt [(col_idx, date, hour), ...] zurück."""
    cols = []
    for i, col in enumerate(header[3:], start=3):
        s = col.strip()
        if not s:
            continue
        try:
            dt = datetime.fromisoformat(s)
            cols.append((i, dt.date(), dt.hour))
        except (ValueError, AttributeError):
            try:
                d = date.fromisoformat(s[:10])
                cols.append((i, d, 0))
            except ValueError:
                pass
    return cols


def _strip_entity_prefix(entity_id):
    """Entfernt 'sensor.' / 'binary_sensor.' etc. Präfix."""
    if "." in entity_id:
        return entity_id.split(".", 1)[1]
    return entity_id


def parse_ha_csv(file_stream):
    """
    Parst HA Energy Export CSV.
    Gibt dict[date, dict[sensor_key, float]] zurück.
    """
    raw = file_stream.read().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(raw))

    header = None
    rows = []
    for row in reader:
        if not row:
            continue
        stripped = [c.strip() for c in row]
        if all(c == "" for c in stripped):
            continue
        if header is None:
            header = stripped
            continue
        rows.append(stripped)

    if not header:
        return {}

    date_cols = _parse_date_header(header)
    if not date_cols:
        log.warning("Keine Datumsspalten gefunden!")
        return {}

    daily: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for row in rows:
        entity_id = row[0].strip() if len(row) > 0 else ""
        type_col = row[1].strip() if len(row) > 1 else ""
        entity_short = _strip_entity_prefix(entity_id)

        sensor_key = None

        # 1. Berechnete Zeilen (leere entity_id, type = calculated_XXX)
        if entity_id == "" and type_col in CALCULATED_TYPE_TO_FIELD:
            sensor_key = f"__calc__{type_col}"
        # 2. PV-Produktion
        elif entity_short in PV_PRODUCTION_SENSORS:
            sensor_key = "__pv__"
        # 3. Batterie Input
        elif entity_short in BATTERY_IN_IDS:
            sensor_key = "__battery_in__"
        # 4. Batterie Output
        elif entity_short in BATTERY_OUT_IDS:
            sensor_key = "__battery_out__"
        else:
            continue

        for col_idx, d, _hour in date_cols:
            if col_idx < len(row):
                val = row[col_idx].strip()
                if val and val not in ("unknown", "unavailable"):
                    try:
                        daily[d][sensor_key] += float(val)
                    except ValueError:
                        pass

    return daily


def _sensors_to_fields(sensors: dict[str, float]) -> dict:
    """Wandelt interne Sensor-Keys in DB-Feldnamen um."""
    pv = sensors.get("__pv__", 0)
    bat_in = sensors.get("__battery_in__", 0)
    bat_out = sensors.get("__battery_out__", 0)

    # Berechnete Werte extrahieren
    calc = {}
    for k, v in sensors.items():
        if k.startswith("__calc__"):
            calc[k[8:]] = v

    return {
        "pv_production_kwh": pv,
        "battery_in_kwh": bat_in,
        "battery_out_kwh": bat_out,
        "grid_to_house_kwh": calc.get("calculated_consumed_grid", 0),
        "consumed_solar_kwh": calc.get("calculated_consumed_solar", 0),
        "consumption_kwh": calc.get("calculated_consumption",
                                    calc.get("calculated_total_consumption", 0)),
        "solar_to_battery_kwh": calc.get("calculated_solar_to_battery", 0),
        "solar_to_grid_kwh": calc.get("calculated_solar_to_grid", 0),
        "battery_usage_kwh": calc.get("calculated_consumed_battery", 0),
    }


def import_csv_to_db(file_stream, filename):
    """Importiert CSV → DB. Gibt (count, year, month) zurück."""
    daily = parse_ha_csv(file_stream)
    if not daily:
        return 0, None, None

    first = min(daily.keys())
    year, month = first.year, first.month
    count = 0

    for day, sensors in daily.items():
        field_vals = _sensors_to_fields(sensors)
        rec = DailyEnergy.query.filter_by(day=day).first()
        if rec:
            for k, v in field_vals.items():
                setattr(rec, k, v)
        else:
            db.session.add(DailyEnergy(day=day, **field_vals))
        count += 1

    db.session.add(ImportLog(
        filename=filename, year=year, month=month, records_count=count
    ))
    db.session.commit()
    return count, year, month


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_active_rate(d: date):
    return ElectricityRate.query.filter(
        ElectricityRate.start_date <= d, ElectricityRate.end_date >= d
    ).first()


def get_active_feedin(d: date):
    return FeedInTariff.query.filter(
        FeedInTariff.start_date <= d, FeedInTariff.end_date >= d
    ).first()


# ── Auth ─────────────────────────────────────────────────────────────────────

@app.before_request
def require_login():
    if request.endpoint in ("login", "static") or request.endpoint is None:
        return
    if not session.get("authenticated"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    from werkzeug.security import check_password_hash
    if request.method == "POST":
        if check_password_hash(get_pw_hash(), request.form.get("password", "")):
            session["authenticated"] = True
            session.permanent = True
            return redirect(url_for("index"))
        flash("Falsches Passwort!", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    monthly_q = db.session.query(
        extract("year", DailyEnergy.day).label("y"),
        extract("month", DailyEnergy.day).label("m"),
        func.sum(DailyEnergy.consumption_kwh).label("cons"),
        func.sum(DailyEnergy.solar_to_grid_kwh).label("gret"),
        func.sum(DailyEnergy.pv_production_kwh).label("pv"),
        func.sum(DailyEnergy.consumed_solar_kwh).label("own"),
        func.sum(DailyEnergy.solar_to_battery_kwh).label("s2b"),
        func.sum(DailyEnergy.grid_to_house_kwh).label("grid_h"),
        func.sum(DailyEnergy.battery_in_kwh).label("bin"),
        func.sum(DailyEnergy.battery_out_kwh).label("bout"),
        func.sum(DailyEnergy.battery_usage_kwh).label("buse"),
        func.count(DailyEnergy.id).label("days"),
    ).group_by("y", "m").order_by("y", "m").all()

    months = []
    cum_income = 0.0
    cum_cost = 0.0
    manual_total = float(db.session.query(
        func.coalesce(func.sum(ManualCost.amount), 0)
    ).scalar() or 0)

    labels, cum_inc, cum_cst, cum_net = [], [], [], []

    for row in monthly_q:
        y, m = int(row.y), int(row.m)
        rate = get_active_rate(date(y, m, 1))
        feedin = get_active_feedin(date(y, m, 1))

        price = float(rate.price_per_kwh) / 100 if rate else 0.0
        base_fee = float(rate.monthly_base_fee) if rate else 0.0
        feedin_price = float(feedin.price_per_kwh) / 100 if feedin else 0.0

        pv = float(row.pv or 0)
        gret = float(row.gret or 0)
        own = float(row.own or 0)
        cons = float(row.cons or 0)
        grid_h = float(row.grid_h or 0)
        bin_v = float(row.bin or 0)
        bout_v = float(row.bout or 0)
        buse = float(row.buse or 0)
        s2b = float(row.s2b or 0)
        days = int(row.days or 0)

        feedin_earned = gret * feedin_price
        savings = own * price
        income = feedin_earned + savings

        grid_cost = grid_h * price
        month_manual = float(db.session.query(
            func.coalesce(func.sum(ManualCost.amount), 0)
        ).filter(
            extract("year", ManualCost.cost_date) == y,
            extract("month", ManualCost.cost_date) == m
        ).scalar() or 0)

        total_month_cost = grid_cost + base_fee + month_manual
        cum_income += income
        cum_cost += total_month_cost

        lbl = f"{y}-{m:02d}"
        months.append({
            "year": y, "month": m, "label": lbl, "days": days,
            "pv_production_kwh": round(pv, 2),
            "consumption_kwh": round(cons, 2),
            "grid_return_kwh": round(gret, 2),
            "consumed_solar_kwh": round(own, 2),
            "grid_house_kwh": round(grid_h, 2),
            "battery_in": round(bin_v, 2),
            "battery_out": round(bout_v, 2),
            "battery_usage": round(buse, 2),
            "solar_to_battery": round(s2b, 2),
            "feedin_earned": round(feedin_earned, 2),
            "savings": round(savings, 2),
            "income": round(income, 2),
            "grid_cost": round(grid_cost, 2),
            "base_fee": round(base_fee, 2),
            "manual_costs": round(month_manual, 2),
            "total_costs": round(total_month_cost, 2),
            "net": round(income - total_month_cost, 2),
        })
        labels.append(lbl)
        cum_inc.append(round(cum_income, 2))
        cum_cst.append(round(cum_cost + manual_total, 2))
        cum_net.append(round(cum_income - cum_cost - manual_total, 2))

    break_even = next((m["label"] for m, n in zip(months, cum_net) if n >= 0), None)
    recent_imports = ImportLog.query.order_by(ImportLog.imported_at.desc()).limit(10).all()

    return render_template(
        "dashboard.html",
        months=months,
        total_income=round(cum_income, 2),
        total_cost=round(cum_cost + manual_total, 2),
        net_total=round(cum_income - cum_cost - manual_total, 2),
        break_even=break_even,
        recent_imports=recent_imports,
        month_labels=labels,
        cumulative_income=cum_inc,
        cumulative_cost=cum_cst,
        cumulative_net=cum_net,
    )


# ── Import ────────────────────────────────────────────────────────────────────

@app.route("/import", methods=["GET", "POST"])
def import_csv_route():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or f.filename == "":
            flash("Keine Datei ausgewählt!", "error")
            return redirect(request.url)
        try:
            records, yr, mo = import_csv_to_db(f.stream, f.filename)
            if records:
                flash(f"✓ {records} Tage importiert ({yr}-{mo:02d})", "success")
            else:
                flash("Keine gültigen Daten in der CSV!", "warning")
        except Exception as e:
            log.exception("Import failed")
            flash(f"Fehler: {e}", "error")
        return redirect(url_for("import_csv_route"))

    logs = ImportLog.query.order_by(ImportLog.imported_at.desc()).all()
    return render_template("import.html", logs=logs)


# ── Rates ─────────────────────────────────────────────────────────────────────

@app.route("/rates", methods=["GET", "POST"])
def rates():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            db.session.add(ElectricityRate(
                start_date=date.fromisoformat(request.form["start_date"]),
                end_date=date.fromisoformat(request.form["end_date"]),
                price_per_kwh=Decimal(request.form["price_per_kwh"]),
                monthly_base_fee=Decimal(request.form.get("monthly_base_fee", "0")),
                label=request.form.get("label", ""),
            ))
            db.session.commit()
            flash("Stromtarif gespeichert!", "success")
        elif action == "delete":
            r = ElectricityRate.query.get(int(request.form["id"]))
            if r:
                db.session.delete(r)
                db.session.commit()
                flash("Gelöscht.", "success")
        return redirect(url_for("rates"))
    return render_template(
        "rates.html",
        rates=ElectricityRate.query.order_by(ElectricityRate.start_date.desc()).all()
    )


# ── Feed-in ───────────────────────────────────────────────────────────────────

@app.route("/feedin", methods=["GET", "POST"])
def feedin():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            db.session.add(FeedInTariff(
                start_date=date.fromisoformat(request.form["start_date"]),
                end_date=date.fromisoformat(request.form["end_date"]),
                price_per_kwh=Decimal(request.form["price_per_kwh"]),
                label=request.form.get("label", ""),
            ))
            db.session.commit()
            flash("Einspeisevergütung gespeichert!", "success")
        elif action == "delete":
            t = FeedInTariff.query.get(int(request.form["id"]))
            if t:
                db.session.delete(t)
                db.session.commit()
                flash("Gelöscht.", "success")
        return redirect(url_for("feedin"))
    return render_template(
        "feedin.html",
        feedin_tariffs=FeedInTariff.query.order_by(FeedInTariff.start_date.desc()).all()
    )


# ── Manual Costs ──────────────────────────────────────────────────────────────

@app.route("/costs", methods=["GET", "POST"])
def costs():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            db.session.add(ManualCost(
                cost_date=date.fromisoformat(request.form["cost_date"]),
                amount=Decimal(request.form["amount"]),
                category=request.form.get("category", "Sonstiges"),
                description=request.form.get("description", ""),
            ))
            db.session.commit()
            flash("Kosten gespeichert!", "success")
        elif action == "delete":
            c = ManualCost.query.get(int(request.form["id"]))
            if c:
                db.session.delete(c)
                db.session.commit()
                flash("Gelöscht.", "success")
        return redirect(url_for("costs"))
    return render_template(
        "costs.html",
        costs=ManualCost.query.order_by(ManualCost.cost_date.desc()).all()
    )


# ── HA Settings ───────────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
def settings_route():
    if request.method == "POST":
        # HA Connection
        Settings.set("ha_url", request.form.get("ha_url", "").strip().rstrip("/"))
        token_val = request.form.get("ha_token", "").strip()
        if token_val:
            Settings.set("ha_token", token_val)
        # Sensors
        sensor_keys = [
            "ha_sensor_pv_production",
            "ha_sensor_battery_in", "ha_sensor_battery_out",
            "ha_sensor_grid_to_house", "ha_sensor_consumed_solar",
            "ha_sensor_consumption", "ha_sensor_solar_to_battery",
            "ha_sensor_solar_to_grid", "ha_sensor_battery_usage",
        ]
        for key in sensor_keys:
            Settings.set(key, request.form.get(key, "").strip())
        flash("Einstellungen gespeichert!", "success")
        return redirect(url_for("settings_route"))

    ha = get_ha_settings()
    return render_template("settings.html", ha=ha)


# ── HA Import API ─────────────────────────────────────────────────────────────

@app.route("/api/ha-import", methods=["POST"])
def ha_import_api():
    """Importiert Daten von HA. Query-Param: date=YYYY-MM-DD (default: gestern)."""
    date_str = request.args.get("date", "")
    if date_str:
        try:
            target = date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"error": "Ungültiges Datum"}), 400
    else:
        from datetime import timedelta
        target = date.today() - timedelta(days=1)

    count, msg = ha_import_day(target)
    if count:
        return jsonify({"ok": True, "date": str(target), "message": msg})
    return jsonify({"ok": False, "error": msg}), 400


@app.route("/api/ha-test", methods=["POST"])
def ha_test_api():
    """Testet die HA-Verbindung."""
    settings = get_ha_settings()
    url = settings.get("ha_url", "")
    token = settings.get("ha_token", "")
    if not url or not token:
        return jsonify({"ok": False, "error": "HA URL oder Token fehlt."}), 400

    test_url = f"{url}/api/"
    req = urllib.request.Request(test_url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode())
        return jsonify({"ok": True, "ha_message": data.get("message", "OK")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    with app.app_context():
        db.create_all()
        log.info("DB ready.")
    if "--init-db" in sys.argv:
        sys.exit(0)
    app.run(host="0.0.0.0", port=5000)
