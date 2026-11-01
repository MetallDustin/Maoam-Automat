"""MAOM-Automat: Raspberry-Pi-Steuerung fuer den Arduino Mega."""
import copy
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
import threading
import time
from functools import wraps

import serial
from flask import Flask, Response, redirect, render_template_string, request, session, url_for

SERIAL_PORT = os.environ.get("MAOM_SERIAL_PORT", "/dev/ttyACM0")
BAUDRATE = 9600
SERIAL_RETRY_SECONDS = 2
HEARTBEAT_INTERVAL_SECONDS = 2
HEARTBEAT_TIMEOUT_SECONDS = 5
WEB_PORT = int(os.environ.get("MAOM_PORT", "8443"))
TLS_CERT_FILE = os.environ.get("MAOM_TLS_CERT", "")
TLS_KEY_FILE = os.environ.get("MAOM_TLS_KEY", "")
STATE_FILE = os.path.join(os.path.dirname(__file__), "automaten_state.json")
SLOTS = 5

DEFAULT_STATE = {
    "enabled": True,
    "credit": 0,
    "revenue": 0,
    "slots": [{"name": f"Fach {n}", "price": 20, "stock": 0, "sold": 0, "sales_revenue": 0} for n in range(1, SLOTS + 1)],
    "users": [],
    "admin_pin_hash": "",
    "audit": [],
    "servo": [{"home": 0, "output": 90, "out_ms": 450, "back_ms": 450} for _ in range(SLOTS)],
    "selection_timeout": 30,
    "hardware_error": "",
}

lock = threading.RLock()
serial_lock = threading.Lock()
state = copy.deepcopy(DEFAULT_STATE)
ser = None
mega_ready = False
persistence_error = ""
vend_in_progress = False
service_mode = True  # Sicherer Start, bis der Mega den Schluesselstatus gemeldet hat.
selected_slot = None
selected_quantity = 0
pending_dispenses = 0
active_dispense_slot = None
selection_changed_at = 0.0
last_pong_at = 0.0
last_key_on = None
KEY_ON_IS_SERVICE = True
STARTUP_ANIMATION_SECONDS = 9
startup_animation_finished = False


def load_state():
    global state
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            saved = json.load(file)
    except FileNotFoundError:
        state = copy.deepcopy(DEFAULT_STATE)
        return
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Zustandsdatei kann nicht gelesen werden: {STATE_FILE}") from error

    if not isinstance(saved, dict):
        raise RuntimeError("Die Zustandsdatei muss ein JSON-Objekt enthalten.")
    required_keys = ("enabled", "revenue", "slots", "users")
    missing_keys = [key for key in required_keys if key not in saved]
    if missing_keys:
        raise RuntimeError("Unvollständige Zustandsdatei; fehlt: " + ", ".join(missing_keys))

    loaded = copy.deepcopy(DEFAULT_STATE)
    loaded.update(saved)
    if type(loaded["enabled"]) is not bool:
        raise RuntimeError("Ungültiger Wert für 'enabled' in der Zustandsdatei.")
    if type(loaded["revenue"]) is not int or loaded["revenue"] < 0:
        raise RuntimeError("Ungültiger Umsatz in der Zustandsdatei.")
    if not isinstance(loaded["slots"], list) or len(loaded["slots"]) != SLOTS:
        raise RuntimeError(f"Die Zustandsdatei muss genau {SLOTS} Fächer enthalten.")
    for number, slot in enumerate(loaded["slots"], 1):
        if not isinstance(slot, dict) or not isinstance(slot.get("name"), str):
            raise RuntimeError(f"Ungültige Daten für Fach {number} in der Zustandsdatei.")
        for field, minimum in (("price", 1), ("stock", 0), ("sold", 0), ("sales_revenue", 0)):
            value = slot.get(field, 0 if field != "price" else 20)
            if type(value) is not int or value < minimum:
                raise RuntimeError(f"Ungültiger Wert für '{field}' in Fach {number}.")
            slot[field] = value
    if not isinstance(loaded["users"], list):
        raise RuntimeError("Ungültige Benutzerliste in der Zustandsdatei.")
    for user in loaded["users"]:
        if (not isinstance(user, dict) or not isinstance(user.get("name"), str)
                or not isinstance(user.get("password"), str)
                or user.get("role") not in ("admin", "manager", "refill", "viewer")):
            raise RuntimeError("Ungültiger Benutzer in der Zustandsdatei.")
    if not isinstance(loaded["admin_pin_hash"], str):
        raise RuntimeError("Ungültiger Admin-PIN in der Zustandsdatei.")
    if not isinstance(loaded["audit"], list):
        raise RuntimeError("Ungültiges Protokoll in der Zustandsdatei.")
    if not isinstance(loaded["servo"], list) or len(loaded["servo"]) != SLOTS:
        raise RuntimeError(f"Die Zustandsdatei muss genau {SLOTS} Servo-Konfigurationen enthalten.")
    for number, config in enumerate(loaded["servo"], 1):
        if not isinstance(config, dict):
            raise RuntimeError(f"Ungültige Servo-Konfiguration für Fach {number}.")
        for field, minimum, maximum in (
            ("home", 0, 180), ("output", 0, 180), ("out_ms", 50, 5000), ("back_ms", 50, 5000)
        ):
            value = config.get(field, DEFAULT_STATE["servo"][number - 1][field])
            if type(value) is not int or not minimum <= value <= maximum:
                raise RuntimeError(f"Ungültiger Wert für '{field}' bei Servo {number}.")
            config[field] = value
    if type(loaded["selection_timeout"]) is not int or not 5 <= loaded["selection_timeout"] <= 300:
        raise RuntimeError("Ungültiger Auswahl-Timeout in der Zustandsdatei.")
    if not isinstance(loaded["hardware_error"], str):
        raise RuntimeError("Ungültiger Hardware-Fehlerstatus in der Zustandsdatei.")

    state = loaded
    state["credit"] = 0  # Kein Guthaben nach einem Neustart uebernehmen.


def save_state():
    global persistence_error
    with lock:
        persistent = {key: value for key, value in state.items() if key != "credit"}
        temporary_path = None
        try:
            directory = os.path.dirname(os.path.abspath(STATE_FILE))
            os.makedirs(directory, exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=".automaten_state.", suffix=".tmp", dir=directory
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(persistent, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            if os.name != "nt":
                os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, STATE_FILE)
        except OSError as error:
            persistence_error = "Speicherfehler: Verkauf gesperrt, bis der Zustand sicher gespeichert werden kann."
            print("Zustand konnte nicht gespeichert werden:", error)
            send("RELAY OFF")
            send("LEDS OFF")
            raise
        finally:
            if temporary_path and os.path.exists(temporary_path):
                try:
                    os.unlink(temporary_path)
                except OSError as error:
                    print("Temporäre Zustandsdatei konnte nicht entfernt werden:", error)


def password_hash(value, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt.encode("ascii"), 200000).hex()
    return f"{salt}${digest}"


def password_matches(value, encoded):
    try:
        salt, digest = encoded.split("$", 1)
        return secrets.compare_digest(password_hash(value, salt), encoded)
    except (ValueError, AttributeError):
        return False


def euro(cents):
    """Formatiert Cent als deutschen Euro-Betrag ohne Fließkommafehler."""
    return f"{int(cents) // 100},{int(cents) % 100:02d} €"


def audit(action):
    with lock:
        state.setdefault("audit", []).append({"time": time.strftime("%d.%m.%Y %H:%M"), "user": session.get("user", "System"), "action": action})
        state["audit"] = state["audit"][-120:]
        save_state()


def send(line):
    error = None
    with serial_lock:
        connection = ser
        if connection is None or not mega_ready:
            print("Mega nicht bereit; Befehl nicht gesendet:", line)
            return False
        try:
            connection.write((line + "\n").encode("utf-8"))
            connection.flush()
        except (serial.SerialException, OSError, ValueError) as caught:
            error = caught
    if error is not None:
        serial_disconnected(connection, error)
        return False
    print(">>", line)
    return True


def lcd(line1="", line2=""):
    # Einzelne lange Meldungen lesbar auf beide 16-Zeichen-Zeilen umbrechen.
    if not line2 and len(line1) > 16:
        cut = line1.rfind(" ", 0, 17)
        if cut <= 0:
            cut = 16
        line1, line2 = line1[:cut], line1[cut:].lstrip()
    send(f"LCD {line1[:16]}|{line2[:16]}")


def any_stock():
    return any(slot["stock"] > 0 for slot in state["slots"])


def is_operational():
    return state["enabled"] and not service_mode and mega_ready and not state["hardware_error"] and not persistence_error


def machine_issues():
    """Technische Hinweise fuer die Verwaltung; am LCD erscheinen nur allgemeine Texte."""
    issues = []
    if not mega_ready:
        issues.append("Arduino Mega nicht verbunden")
    if state["hardware_error"]:
        issues.append(state["hardware_error"])
    if persistence_error:
        issues.append(persistence_error)
    empty = [str(number) for number, slot in enumerate(state["slots"], 1) if slot["stock"] <= 0]
    if empty:
        issues.append("Leer: Fach " + ", ".join(empty))
    low = [str(number) for number, slot in enumerate(state["slots"], 1) if 0 < slot["stock"] <= 2]
    if low:
        issues.append("Niedriger Bestand: Fach " + ", ".join(low))
    return issues


def update_hardware(message=None):
    """Setzt LEDs, Muenzpruefer und LCD aus dem aktuellen Zustand."""
    global vend_in_progress
    if vend_in_progress:
        send("RELAY OFF")
        send("LEDS OFF")
        return
    if not is_operational():
        send("RELAY OFF"); send("LEDS OFF")
        lcd("Wartungsmodus", "Ausser Betrieb")
        return
    if not any_stock():
        send("RELAY OFF"); send("LEDS OFF")
        lcd("Wartung noetig", "Bitte Service")
        return

    if selected_slot is None:
        send("RELAY OFF")
        for i, slot in enumerate(state["slots"], 1): send(f"LED {i} {'ON' if slot['stock'] > 0 else 'OFF'}")
        lcd("Bitte Fach", "auswaehlen")
    else:
        slot = state["slots"][selected_slot]
        send("LEDS OFF")
        send(f"LED {selected_slot + 1} ON")
        send("RELAY ON")
        total = slot["price"] * selected_quantity
        missing = max(0, total - state["credit"])
        lcd(f"{selected_quantity}x {total}ct", f"Noch {missing}ct")
    if message:
        lcd(*message)


def handle_line(line):
    global vend_in_progress, service_mode, selected_slot, selected_quantity
    global pending_dispenses, active_dispense_slot, selection_changed_at, last_key_on, mega_ready
    global startup_animation_finished, last_pong_at
    print("<<", line)
    if line == "PONG":
        with lock:
            last_pong_at = time.monotonic()
        return
    if line == "READY":
        with serial_lock:
            mega_ready = True
        with lock:
            last_pong_at = time.monotonic()
            apply_servo_settings()
            if startup_animation_finished:
                send("STARTUP DONE")
            update_hardware()
        return
    with lock:
        if line.startswith("COIN "):
            try:
                parts = line.split()
                if len(parts) != 2:
                    return
                cents = int(parts[1])
            except ValueError:
                return
            if is_operational() and selected_slot is not None and not vend_in_progress and cents in (5, 10, 20):
                credit_before_coin = state["credit"]
                state["credit"] += cents
                slot = state["slots"][selected_slot]
                total = slot["price"] * selected_quantity
                if state["credit"] >= total:
                    state["credit"] -= total
                    slot["stock"] -= selected_quantity
                    state["revenue"] += total
                    slot["sold"] = int(slot.get("sold", 0)) + selected_quantity
                    slot["sales_revenue"] = int(slot.get("sales_revenue", 0)) + total
                    try:
                        save_state()
                    except OSError:
                        slot["stock"] += selected_quantity
                        state["revenue"] -= total
                        slot["sold"] -= selected_quantity
                        slot["sales_revenue"] -= total
                        state["credit"] = credit_before_coin + cents
                        update_hardware()
                        return
                    vend_in_progress = True
                    pending_dispenses = selected_quantity
                    active_dispense_slot = selected_slot
                    send("RELAY OFF"); send("LEDS OFF")
                    lcd("Ausgabe laeuft", f"{selected_quantity}x {slot['name']}")
                    send(f"DISPENSE {selected_slot + 1}")
                else:
                    update_hardware()
        elif line.startswith("BUTTON "):
            try:
                parts = line.split()
                if len(parts) != 2:
                    return
                index = int(parts[1]) - 1
            except ValueError:
                return
            if not (0 <= index < SLOTS) or vend_in_progress or not is_operational():
                return
            slot = state["slots"][index]
            if slot["stock"] <= 0:
                return
            if selected_slot is None:
                selected_slot = index
                selected_quantity = 1
            elif selected_slot == index:
                if selected_quantity < slot["stock"]:
                    selected_quantity += 1
                else:
                    lcd("Bestand erreicht", f"Maximal {slot['stock']}x")
                    return
            else:
                return
            selection_changed_at = time.monotonic()
            update_hardware()
        elif line.startswith("OK ") and vend_in_progress:
            try:
                parts = line.split()
                if len(parts) != 2:
                    return
                acknowledged_slot = int(parts[1]) - 1
            except ValueError:
                return
            if (not 0 <= acknowledged_slot < SLOTS or acknowledged_slot != active_dispense_slot
                    or pending_dispenses <= 0):
                print("Unerwartete Ausgabe-Bestätigung ignoriert:", line)
                return
            pending_dispenses -= 1
            if pending_dispenses > 0:
                send(f"DISPENSE {active_dispense_slot + 1}")
            else:
                vend_in_progress = False
                active_dispense_slot = None
                selected_slot = None
                selected_quantity = 0
                selection_changed_at = 0.0
                update_hardware()
        elif line in ("KEY ON", "KEY OFF"):
            key_on = line == "KEY ON"
            last_key_on = key_on
            service_mode = key_on if KEY_ON_IS_SERVICE else not key_on
            state["credit"] = 0
            if not vend_in_progress:
                selected_slot = None
                selected_quantity = 0
                selection_changed_at = 0.0
            update_hardware()


def serial_disconnected(connection, error):
    global ser, mega_ready, service_mode, last_key_on
    global vend_in_progress, selected_slot, selected_quantity
    global pending_dispenses, active_dispense_slot, selection_changed_at, last_pong_at
    with serial_lock:
        if ser is not connection:
            return
        ser = None
        mega_ready = False
    print("Serielle Verbindung getrennt:", error)
    try:
        connection.close()
    except (serial.SerialException, OSError, ValueError):
        pass
    with lock:
        last_pong_at = 0.0
        service_mode = True
        last_key_on = None
        selected_slot = None
        selected_quantity = 0
        selection_changed_at = 0.0
        state["credit"] = 0
        if vend_in_progress:
            state["hardware_error"] = (
                "Verbindung während einer Ausgabe unterbrochen. Ware und Bestand prüfen."
            )
            vend_in_progress = False
            pending_dispenses = 0
            active_dispense_slot = None
            try:
                save_state()
            except OSError as save_error:
                print("Ausgabe-Prüfhinweis konnte nicht gespeichert werden:", save_error)


def connect_serial():
    global ser, mega_ready
    try:
        connection = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=0.2, write_timeout=1)
    except (serial.SerialException, OSError, ValueError) as error:
        print(f"Mega unter {SERIAL_PORT} nicht erreichbar; neuer Versuch in {SERIAL_RETRY_SECONDS}s: {error}")
        return False
    with serial_lock:
        ser = connection
        mega_ready = False
    print("Serielle Verbindung geöffnet; warte auf READY vom Mega.")
    return True


def serial_loop():
    global selected_slot, selected_quantity, selection_changed_at
    last_ping_at = 0.0
    while True:
        with serial_lock:
            connection = ser
        if connection is None:
            last_ping_at = 0.0
            if not connect_serial():
                time.sleep(SERIAL_RETRY_SECONDS)
            continue
        try:
            line = connection.readline().decode("utf-8", errors="replace").strip()
            if line:
                handle_line(line)
            with lock:
                timeout = int(state.get("selection_timeout", 30))
                if selected_slot is not None and state["credit"] == 0 and selection_changed_at and time.monotonic() - selection_changed_at >= timeout:
                    selected_slot = None
                    selected_quantity = 0
                    selection_changed_at = 0.0
                    lcd("Auswahl beendet", "Bitte neu waehlen")
                    update_hardware()
            now = time.monotonic()
            with serial_lock:
                ready = mega_ready
            if ready and now - last_ping_at >= HEARTBEAT_INTERVAL_SECONDS:
                if send("PING"):
                    last_ping_at = now
            with lock:
                pong_at = last_pong_at
            if ready and pong_at and now - pong_at > HEARTBEAT_TIMEOUT_SECONDS:
                serial_disconnected(connection, serial.SerialException("Keine PONG-Antwort vom Mega"))
        except serial.SerialException as error:
            serial_disconnected(connection, error)
            time.sleep(SERIAL_RETRY_SECONDS)
        except OSError as error:
            serial_disconnected(connection, error)
            time.sleep(SERIAL_RETRY_SECONDS)
        except ValueError as error:
            serial_disconnected(connection, error)
            time.sleep(SERIAL_RETRY_SECONDS)


def finish_startup_animation():
    """Beendet die Mega-Startanimation und stellt den echten LED-Status her."""
    global startup_animation_finished
    with lock:
        startup_animation_finished = True
        send("STARTUP DONE")
        update_hardware()


def apply_servo_settings():
    for number, config in enumerate(state.get("servo", []), 1):
        send(f"SERVO {number} {config['home']} {config['output']} {config['out_ms']} {config['back_ms']}")


app = Flask(__name__)
app.secret_key = os.environ.get("MAOM_SESSION_SECRET", secrets.token_hex(32))


def current_user():
    with lock:
        name = session.get("user")
        return next((item for item in state.get("users", []) if item["name"] == name), None)


def has_role(*roles):
    user = current_user()
    return user is not None and user.get("role") in roles


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not has_role(*roles):
                return redirect("/")
            return view(*args, **kwargs)
        return wrapped
    return decorator


def admin_pin_ok():
    with lock:
        return has_role("admin") and password_matches(request.form.get("pin", ""), state.get("admin_pin_hash", ""))


def return_to_page():
    """Nach Formularaktionen auf der aktuell geöffneten App-Seite bleiben."""
    referrer = request.referrer or "/"
    return redirect(referrer if referrer.startswith(request.host_url) else "/")


@app.before_request
def login_required():
    allowed = {"setup", "login", "manifest", "service_worker", "icon"}
    if request.endpoint in allowed or request.path.startswith("/static/"):
        return None
    with lock:
        if not state.get("users"):
            return redirect(url_for("setup"))
        if not current_user():
            return redirect(url_for("login"))
    return None


AUTH_TEMPLATE = """<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>MAOM Verwaltung</title><style>:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#090b12;color:#f7f8fc;font:16px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}.box{width:min(92vw,420px);padding:28px;border:1px solid #2a3040;border-radius:24px;background:#151925;box-shadow:0 22px 60px #0008}h1{margin:0 0 8px;color:#62a0ff}p{color:#a9b0c2}input{width:100%;padding:13px;margin:7px 0;border:1px solid #353d51;border-radius:12px;background:#0c0f18;color:#fff;font:inherit}button{width:100%;padding:13px;border:0;border-radius:12px;background:#397dff;color:white;font-weight:800;font:inherit;margin-top:10px}.error{color:#ff9bb4}</style><main class=box><h1>MAOM Verwaltung</h1><p>{{ text }}</p>{% if error %}<p class=error>{{ error }}</p>{% endif %}<form method=post><input name=name required maxlength=24 placeholder='Benutzername' autocomplete=username><input name=password required type=password minlength=8 placeholder='Passwort' autocomplete=new-password>{% if setup %}<input name=pin required inputmode=numeric pattern='[0-9]{4,12}' placeholder='Admin-PIN (4–12 Ziffern)'>{% endif %}<button>{{ button }}</button></form></main>"""


@app.route("/setup", methods=["GET", "POST"])
def setup():
    with lock:
        if state.get("users"):
            return redirect(url_for("login"))
    error = ""
    if request.method == "POST":
        name, password, pin = request.form.get("name", "").strip(), request.form.get("password", ""), request.form.get("pin", "")
        if len(name) < 3 or len(password) < 8 or not (pin.isdigit() and 4 <= len(pin) <= 12): error = "Bitte gültige Daten eingeben."
        else:
            with lock:
                if state.get("users"):
                    return redirect(url_for("login"))
                state["users"] = [{"name": name, "password": password_hash(password), "role": "admin"}]
                state["admin_pin_hash"] = password_hash(pin)
                save_state()
                session["user"] = name
                audit("Admin-Ersteinrichtung")
                return redirect("/")
    return render_template_string(AUTH_TEMPLATE, text="Ersten Administrator einrichten.", button="Admin-Konto erstellen", error=error, setup=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        with lock:
            user = next((u for u in state.get("users", []) if u["name"] == request.form.get("name", "").strip()), None)
            if user and password_matches(request.form.get("password", ""), user["password"]):
                session["user"] = user["name"]
                audit("Anmeldung")
                return redirect("/")
        error = "Anmeldung nicht möglich."
    return render_template_string(AUTH_TEMPLATE, text="Bitte anmelden.", button="Anmelden", error=error, setup=False)


@app.post("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))


@app.get("/api/status")
def api_status():
    with lock:
        return {"status": "Betriebsbereit" if is_operational() else "Wartungsmodus", "operational": is_operational(), "key": "nicht erkannt" if last_key_on is None else ("AN" if last_key_on else "AUS"), "credit": state["credit"], "revenue": state["revenue"], "revenue_eur": euro(state["revenue"]), "mega": "verbunden" if mega_ready else "nicht verbunden", "busy": vend_in_progress, "issues": machine_issues()}


@app.post("/users")
@role_required("admin")
def add_user():
    name = request.form.get("name", "").strip()[:24]
    password = request.form.get("password", "")
    role = request.form.get("role", "viewer")
    with lock:
        if name and len(password) >= 8 and role in ("admin", "manager", "refill", "viewer") and not any(u["name"] == name for u in state["users"]):
            state["users"].append({"name": name, "password": password_hash(password), "role": role})
            audit(f"Benutzer {name} angelegt ({role})")
    return return_to_page()


@app.post("/users/<name>/role")
@role_required("admin")
def change_user_role(name):
    if not admin_pin_ok() or name == session.get("user"):
        return return_to_page()
    role = request.form.get("role", "")
    with lock:
        account = next((u for u in state["users"] if u["name"] == name), None)
        if account and role in ("admin", "manager", "refill", "viewer"):
            account["role"] = role
            audit(f"Rechte von {name} geändert: {role}")
    return return_to_page()


@app.post("/users/<name>/delete")
@role_required("admin")
def delete_user(name):
    if not admin_pin_ok() or name == session.get("user"):
        return return_to_page()
    with lock:
        before = len(state["users"])
        state["users"] = [u for u in state["users"] if u["name"] != name]
        if len(state["users"]) != before:
            audit(f"Benutzer {name} gelöscht")
    return return_to_page()


@app.post("/system/restart-service")
@role_required("admin")
def restart_service():
    if admin_pin_ok():
        return "Software-Neustart ist nicht eingerichtet.", 501
    return return_to_page()


@app.post("/system/<action>")
@role_required("admin")
def system_action(action):
    if action not in ("reboot", "shutdown") or not admin_pin_ok():
        return return_to_page()
    try:
        subprocess.run(
            ["sudo", "-n", "/usr/local/sbin/maom-system-action", action],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print("Systemaktion konnte nicht gestartet werden:", error)
        return "Systemaktion nicht eingerichtet oder nicht erlaubt. Siehe docs/BUILD.md.", 503
    audit("Raspberry Pi " + ("Neustart" if action == "reboot" else "Herunterfahren") + " angefordert")
    return return_to_page()

APP_TEMPLATE = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#ee4d74"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="MAOM">
<link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/icon.svg">
<title>MAOM Automat</title>
<style>
:root{--pink:#ee4d74;--dark:#20222d;--paper:#fffaf6;--card:#fff;--muted:#6f7180;--green:#18a66c;--orange:#ef9918;--line:#eee8e4}*{box-sizing:border-box}body{margin:0;background:var(--paper);font:16px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--dark)}main{max-width:720px;margin:auto;padding:calc(18px + env(safe-area-inset-top)) 16px calc(28px + env(safe-area-inset-bottom))}.hero{background:linear-gradient(135deg,#f44974,#ff7b7e);color:#fff;padding:22px;border-radius:24px;box-shadow:0 12px 28px #ee4d7444}.hero h1{font-size:27px;margin:0 0 6px}.hero p{margin:0;opacity:.9}.status{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.pill{border-radius:999px;padding:8px 12px;background:#fff;font-weight:700;font-size:14px;box-shadow:0 2px 9px #20222d0d}.pill.ok{color:var(--green)}.pill.wait{color:var(--orange)}.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:17px;margin:14px 0;box-shadow:0 3px 12px #20222d08}.card h2{font-size:18px;margin:0 0 12px}.hint{color:var(--muted);font-size:14px;margin:0 0 13px}.slots{display:grid;gap:10px}.slot{border:1px solid var(--line);border-radius:16px;padding:13px}.slothead{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-weight:700}.slothead span{color:var(--pink)}.fields{display:grid;grid-template-columns:1.5fr 1fr 1fr;gap:8px}input{width:100%;border:1px solid #ded9d8;border-radius:11px;background:#fff;padding:11px;font:inherit}button{border:0;border-radius:12px;padding:12px 14px;background:var(--pink);color:#fff;font:inherit;font-weight:750;cursor:pointer;min-height:44px}button.secondary{background:#f2eef0;color:var(--dark)}button.warning{background:var(--orange)}button.green{background:var(--green)}button:disabled{opacity:.45}.actions{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}.actions form{margin:0}.actions button{width:100%}details summary{cursor:pointer;font-weight:750}details[open] summary{margin-bottom:14px}.testgrid{display:grid;grid-template-columns:repeat(5,1fr);gap:7px}.testgrid button{padding:10px 2px;font-size:13px}.custom{display:grid;grid-template-columns:1fr 1fr auto;gap:8px}.foot{text-align:center;color:var(--muted);font-size:13px;margin:24px 0 4px}@media(max-width:430px){.fields{grid-template-columns:1fr 70px 70px}.fields button{grid-column:1/-1}.custom{grid-template-columns:1fr 1fr}.custom button{grid-column:1/-1}}
</style><style>:root{color-scheme:dark;--pink:#ff4f7e;--dark:#f5f7ff;--paper:#090b12;--card:#151925;--muted:#a9b0c2;--green:#32d583;--orange:#ffb454;--line:#2a3040}body{background:#090b12}.pill{background:#202638;box-shadow:none}.card{box-shadow:0 10px 28px #0004}.hero{background:linear-gradient(135deg,#c92759,#7a2cf0)}input{background:#0c0f18;color:#f5f7ff;border-color:#353d51}button.secondary{background:#2a3143;color:#f5f7ff}.slot{background:#111521}</style></head><body><main>
<section class="hero"><h1>MAOM Automat</h1><p>Steuerung & Wartung</p></section>
<div class="status"><div class="pill {{ 'ok' if operational else 'wait' }}">{{ status }}</div><div class="pill">Schlüssel: {{ key_status }}</div><div class="pill">Guthaben {{ credit }} ct</div><div class="pill">Umsatz {{ revenue }} ct</div>{% if selected %}<div class="pill">Gewählt: Fach {{ selected }}</div>{% endif %}</div>
<section class="card"><h2>Fächer</h2><p class="hint">Name, Bestand und Preis pro Sorte einstellen.</p><div class="slots">{% for number, slot in slots %}<form class="slot" method="post" action="/slot/{{ number }}"><div class="slothead"><span>Fach {{ number }}</span><small>{{ slot.stock }} Stück · {{ slot.price }} ct</small></div><div class="fields"><input name="name" maxlength="24" value="{{ slot.name }}" aria-label="Name Fach {{ number }}"><input name="stock" type="text" inputmode="numeric" pattern="[0-9]*" value="{{ slot.stock }}" aria-label="Bestand Fach {{ number }}"><input name="price" type="text" inputmode="numeric" pattern="[0-9]*" value="{{ slot.price }}" aria-label="Preis Fach {{ number }}"><button>Speichern</button></div></form>{% endfor %}</div></section>
<section class="card"><h2>Automat</h2><p class="hint">Der Schlüsselschalter steuert den Wartungsmodus.</p><div class="actions"><form method="post" action="/toggle"><button class="{{ 'warning' if enabled else 'green' }}">{{ 'Software sperren' if enabled else 'Software aktivieren' }}</button></form><form method="post" action="/reset-credit"><button class="secondary">Guthaben löschen</button></form></div></section>
<section class="card"><details {% if not operational %}open{% endif %}><summary>Wartung & Test</summary>{% if operational %}<p class="hint">Zum Testen den Schlüsselschalter in den Wartungsmodus drehen.</p>{% else %}<p class="hint">Tests geben keine Ware aus dem Bestand ab. Während eines Servo-Tests ist die Ausgabe gesperrt.</p><h3>Servo testen</h3><div class="testgrid">{% for n in range(1, 6) %}<form method="post" action="/test/servo/{{ n }}"><button>Servo {{ n }}</button></form>{% endfor %}</div><h3>LEDs testen</h3><div class="actions"><form method="post" action="/test/leds/0"><button class="secondary">Alle LEDs</button></form><form method="post" action="/test/reset"><button class="secondary">Test beenden</button></form></div><h3>LCD testen</h3><form class="custom" method="post" action="/test/lcd"><input name="line1" maxlength="16" placeholder="Zeile 1"><input name="line2" maxlength="16" placeholder="Zeile 2"><button>Zeigen</button></form><h3>Münzprüfer-Relais</h3><div class="actions"><form method="post" action="/test/relay/on"><button class="warning">Relais einschalten</button></form><form method="post" action="/test/relay/off"><button class="secondary">Relais ausschalten</button></form></div>{% endif %}</details></section>
{% if user.role == 'admin' %}<section class="card"><details><summary>Technik · Admin-PIN erforderlich</summary><p class="hint">Servo-Werte werden nur nach PIN-Prüfung an den Mega übertragen.</p>{% for n,c in servo %}<form class="fields" method="post" action="/tech/servo/{{n}}"><input name="home" inputmode="numeric" value="{{c.home}}" aria-label="Startwinkel"><input name="output" inputmode="numeric" value="{{c.output}}" aria-label="Ausgabewinkel"><input name="out_ms" inputmode="numeric" value="{{c.out_ms}}" aria-label="Hin Zeit"><input name="back_ms" inputmode="numeric" value="{{c.back_ms}}" aria-label="Zurück Zeit"><input name="pin" type="password" inputmode="numeric" placeholder="Admin-PIN" required><button>Servo {{n}} speichern</button></form>{% endfor %}</details></section><section class="card"><details><summary>Protokoll</summary>{% for item in audit|reverse %}<p class="hint">{{item.time}} · {{item.user}} · {{item.action}}</p>{% endfor %}</details></section>{% endif %}<p class="foot">Auf dem iPhone: Teilen → „Zum Home-Bildschirm“</p></main><script>if('serviceWorker'in navigator)navigator.serviceWorker.register('/service-worker.js').catch(()=>{});</script></body></html>"""

ADMIN_EXTRA = """{% if user.role == 'admin' %}
<section class='card'><details><summary>Gratis-Ausgabe</summary><p class='hint'>Nur mit Admin-PIN. Die Ausgabe wird protokolliert und ändert den Bestand nicht.</p><div class='testgrid'>{% for n in range(1,6) %}<form method='post' action='/tech/free-vend/{{n}}'><input name='pin' type='password' inputmode='numeric' placeholder='PIN' required><button>Fach {{n}}</button></form>{% endfor %}</div></details></section>
<section class='card'><details><summary>Benutzerverwaltung</summary><form class='custom' method='post' action='/users'><input name='name' placeholder='Benutzername' required><input name='password' type='password' minlength='8' placeholder='Passwort' required><select name='role'><option value='manager'>Verwalter</option><option value='refill'>Auffüller</option><option value='viewer'>Ansicht</option><option value='admin'>Admin</option></select><button>Anlegen</button></form><p class='hint'>{% for account in users %}{{account.name}} · {{account.role}}{% if not loop.last %}<br>{% endif %}{% endfor %}</p></details></section>
{% endif %}<section class='card'><details><summary>Diagnose</summary><p class='hint'>Mega: {{ mega_status }}<br>Schlüsselschalter: {{key_status}}<br>Modus: {{status}}<br>Aktive Ausgabe: {{ 'ja' if busy else 'nein' }}</p></details></section>"""

NEW_TEMPLATE = """<!doctype html><html lang=de><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name=theme-color content=#080a10><title>MAOM Verwaltung</title><style>:root{color-scheme:dark;--bg:#080a10;--panel:#141824;--panel2:#1c2231;--text:#f4f6fb;--muted:#9aa4b8;--pink:#ff4f7e;--green:#35d084;--yellow:#ffbd5b;--red:#ff617b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}main{max-width:720px;margin:auto;padding:18px 14px 96px}.top{display:flex;justify-content:space-between;align-items:center;margin:6px 2px 18px}.top h1{font-size:24px;margin:0}.sub{color:var(--muted);font-size:13px}.status{padding:14px;border-radius:16px;background:var(--panel);border:1px solid #293147;margin-bottom:14px}.ok{color:var(--green)}.warn{color:var(--yellow)}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.stat,.card{background:var(--panel);border:1px solid #293147;border-radius:18px;padding:15px}.stat b{display:block;font-size:23px;margin-top:4px}.label{font-size:13px;color:var(--muted)}h2{font-size:18px;margin:0 0 12px}.card{margin:12px 0}.slot{border-top:1px solid #293147;padding:12px 0}.slot:first-child{border:0;padding-top:0}.row{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:8px}input,select{width:100%;min-height:42px;border:1px solid #343d53;border-radius:11px;background:#0d1018;color:#fff;padding:9px;font:inherit}button{min-height:42px;border:0;border-radius:11px;padding:9px 12px;background:var(--pink);color:#fff;font:inherit;font-weight:800}.soft{background:#2a3246}.danger{background:var(--red)}.nav{position:fixed;z-index:5;bottom:0;left:0;right:0;background:#111522f2;border-top:1px solid #293147;display:flex;justify-content:center;padding:8px env(safe-area-inset-right) calc(8px + env(safe-area-inset-bottom)) env(safe-area-inset-left)}.navin{width:min(720px,100%);display:grid;grid-template-columns:repeat(5,1fr);gap:4px}.nav a{text-align:center;color:var(--muted);text-decoration:none;font-size:11px;padding:8px 2px;border-radius:10px}.nav a.active{color:#fff;background:#272e40}.pin{position:fixed;z-index:20;inset:0;background:#080a10ee;display:none;align-items:center;justify-content:center;padding:24px}.pin.show{display:flex}.pinbox{width:min(100%,370px);background:#171c29;border:1px solid #3b455d;border-radius:24px;padding:24px;text-align:center}.pinbox h2{font-size:22px}.pinbox input{font-size:28px;letter-spacing:9px;text-align:center;margin:10px 0}.pinbuttons{display:grid;grid-template-columns:1fr 1fr;gap:9px}.hide{display:none}@media(max-width:420px){.row{grid-template-columns:1fr 70px 70px}.row button{grid-column:1/-1}}</style></head><body><main><header class=top><div><h1>MAOM</h1><div class=sub>Verwaltung · {{user.name}}</div></div><form method=post action=/logout><button class=soft>Abmelden</button></form></header>{% if page=='home' %}<section class=status><b class='{{"ok" if operational else "warn"}}'>{{status}}</b><span class=sub> · Schlüssel {{key_status}} · Mega {{mega_status}}</span></section><div class=grid><div class=stat><span class=label>Umsatz</span><b>{{revenue}} ct</b></div><div class=stat><span class=label>Guthaben</span><b>{{credit}} ct</b></div></div><section class=card><h2>Schnellstatus</h2><p class=label>{% for n,s in slots %}Fach {{n}}: {{s.stock}} Stück{% if not loop.last %} · {% endif %}{% endfor %}</p></section>{% elif page=='products' %}<section class=card><h2>Fächer</h2>{% for n,s in slots %}<form class=slot method=post action='/slot/{{n}}'><div class=row><input name=name value='{{s.name}}'><input name=stock inputmode=numeric value='{{s.stock}}'><input name=price inputmode=numeric value='{{s.price}}'><button>Speichern</button></div></form>{% endfor %}</section>{% elif page=='service' %}<section class=card><h2>Wartung</h2><p class=label>Für Tests muss der Automat im Wartungsmodus sein.</p><div class=grid>{% for n in range(1,6) %}<form method=post action='/test/servo/{{n}}'><button class=soft>Servo {{n}}</button></form>{% endfor %}</div><br><div class=grid><form method=post action='/test/leds/0'><button class=soft>Alle LEDs</button></form><form method=post action='/test/reset'><button class=soft>Test beenden</button></form></div></section>{% elif page=='tech' and user.role=='admin' %}<section class=card><h2>Technik</h2><p class=label>Startwinkel, Ausgabe-Winkel und Bewegungszeiten.</p>{% for n,c in servo %}<form class=slot data-pin method=post action='/tech/servo/{{n}}'><div class=row><input name=home value='{{c.home}}' inputmode=numeric><input name=output value='{{c.output}}' inputmode=numeric><input name=out_ms value='{{c.out_ms}}' inputmode=numeric><input name=back_ms value='{{c.back_ms}}' inputmode=numeric><button>Servo {{n}}</button></div></form>{% endfor %}</section><section class=card><h2>Gratis-Ausgabe</h2><div class=grid>{% for n in range(1,6) %}<form data-pin method=post action='/tech/free-vend/{{n}}'><button class=danger>Fach {{n}}</button></form>{% endfor %}</div></section>{% elif page=='admin' and user.role=='admin' %}<section class=card><h2>Benutzer</h2><form method=post action=/users><div class=row><input name=name placeholder=Benutzername required><input name=password type=password placeholder=Passwort required><select name=role><option value=manager>Verwalter</option><option value=refill>Auffüller</option><option value=viewer>Ansicht</option><option value=admin>Admin</option></select><button>Anlegen</button></div></form><br>{% for a in users %}<div class=label>{{a.name}} · {{a.role}}</div>{% endfor %}</section><section class=card><h2>Protokoll</h2>{% for e in audit|reverse %}<p class=label>{{e.time}} · {{e.user}} · {{e.action}}</p>{% endfor %}</section>{% endif %}</main><nav class=nav><div class=navin><a class='{{"active" if page=="home"}}' href='/?page=home'>Status</a><a class='{{"active" if page=="products"}}' href='/?page=products'>Fächer</a><a class='{{"active" if page=="service"}}' href='/?page=service'>Wartung</a>{% if user.role=='admin' %}<a class='{{"active" if page=="tech"}}' href='/?page=tech'>Technik</a><a class='{{"active" if page=="admin"}}' href='/?page=admin'>Admin</a>{% endif %}</div></nav><div id=pin class=pin><div class=pinbox><h2>Admin-PIN</h2><p class=label>Diese Aktion ist geschützt.</p><input id=pinvalue type=password inputmode=numeric maxlength=12 autofocus><div class=pinbuttons><button id=cancel class=soft>Abbrechen</button><button id=confirm>Bestätigen</button></div></div></div><script>let pending;const modal=document.querySelector('#pin'),value=document.querySelector('#pinvalue');document.querySelectorAll('form[data-pin]').forEach(f=>f.addEventListener('submit',e=>{e.preventDefault();pending=f;value.value='';modal.classList.add('show');value.focus()}));document.querySelector('#cancel').onclick=()=>modal.classList.remove('show');document.querySelector('#confirm').onclick=()=>{if(!value.value)return;let x=document.createElement('input');x.type='hidden';x.name='pin';x.value=value.value;pending.append(x);pending.removeAttribute('data-pin');pending.submit()};</script></body></html>"""


MENU_LIVE_EXTENSION = """<style>
.menu-button{position:fixed;top:calc(env(safe-area-inset-top) + 16px);left:14px;z-index:22;width:46px;min-height:46px;border-radius:14px;background:#222a3b;font-size:23px}.nav.drawer{z-index:21;top:0;bottom:0;left:0;right:auto;width:276px;padding:max(116px,calc(env(safe-area-inset-top) + 56px)) 14px 20px;background:#121724;transform:translateX(-105%);transition:transform .22s ease;display:block;border-right:1px solid #303a51;border-top:0}.nav.drawer.open{transform:translateX(0)}.nav.drawer .navin{display:grid;grid-template-columns:1fr;gap:8px}.nav.drawer a{text-align:left;font-size:16px;padding:14px}.scrim{position:fixed;z-index:20;inset:0;background:#0009;display:none}.scrim.show{display:block}.top{padding-left:48px}main{padding-top:max(82px,calc(env(safe-area-inset-top) + 72px))}
</style><button class='menu-button' id='menu' aria-label='Menü'>☰</button><div class='scrim' id='scrim'></div><script>
const nav=document.querySelector('.nav'),menu=document.querySelector('#menu'),scrim=document.querySelector('#scrim');nav.classList.add('drawer');const closeMenu=()=>{nav.classList.remove('open');scrim.classList.remove('show')};menu.onclick=()=>{nav.classList.add('open');scrim.classList.add('show')};scrim.onclick=closeMenu;
document.querySelectorAll('form:not([data-pin])').forEach(form=>form.addEventListener('submit',async e=>{if(form.action.includes('/logout'))return;e.preventDefault();const response=await fetch(form.action,{method:'POST',body:new FormData(form),credentials:'same-origin'});if(!response.ok){alert(await response.text());return}location.reload()}));
async function live(){try{const s=await fetch('/api/status',{cache:'no-store'}).then(r=>r.json());const status=document.querySelector('.status b'),sub=document.querySelector('.status .sub');if(status){status.textContent=s.status;status.className=s.operational?'ok':'warn'}if(sub)sub.textContent=' · Schlüssel '+s.key+' · Mega '+s.mega;const cards=document.querySelectorAll('.stat b');if(cards.length>1){cards[0].textContent=s.revenue+' ct';cards[1].textContent=s.credit+' ct'}}catch(e){}}live();setInterval(live,1200);
</script>"""
NEW_TEMPLATE = NEW_TEMPLATE.replace("</body>", MENU_LIVE_EXTENSION + "</body>")
NEW_TEMPLATE = NEW_TEMPLATE.replace("pending.submit()", "fetch(pending.action,{method:'POST',body:new FormData(pending),credentials:'same-origin'}).then(async response=>{if(!response.ok){alert(await response.text());return}location.reload()})")

MODERN_STYLE = """<style>
body{background:radial-gradient(700px 420px at 0% 0%,#21162f 0%,transparent 62%),radial-gradient(600px 360px at 100% 100%,#111d35 0%,transparent 64%),linear-gradient(155deg,#080a10,#0b0e17 52%,#070910)}.top h1{letter-spacing:-.8px;font-size:27px}.status,.stat,.card{background:linear-gradient(145deg,#1a2030dd,#111520e8);backdrop-filter:blur(18px);border-color:#34405a;box-shadow:0 10px 28px #0003}.status{border-left:3px solid var(--pink)}.stat b{font-size:26px;letter-spacing:-.6px}.card{border-radius:22px;padding:17px}.grid{gap:12px}.label{line-height:1.45}.nav.drawer{background:linear-gradient(160deg,#171c2b,#0e121d)}.nav.drawer a{border:1px solid transparent}.nav.drawer a.active{background:linear-gradient(135deg,#ff4f7e,#a44cff);box-shadow:0 8px 20px #a44cff38}.menu-button{background:linear-gradient(135deg,#ff4f7e,#9d4dff);box-shadow:0 8px 22px #9d4dff55}.pinbox{background:linear-gradient(160deg,#20283a,#111624);box-shadow:0 30px 70px #0009}button{background:linear-gradient(135deg,#ff4f7e,#b348ef);box-shadow:0 7px 16px #b348ef35}button.soft{box-shadow:none}.slot{border-color:#2d3850}
</style>"""
NEW_TEMPLATE = NEW_TEMPLATE.replace("</body>", MODERN_STYLE + "</body>")
NEW_TEMPLATE = NEW_TEMPLATE.replace("--pink:#ff4f7e;", "--pink:#397dff;")
NEW_TEMPLATE = NEW_TEMPLATE.replace("cards[0].textContent=s.revenue+' ct'", "cards[0].textContent=s.revenue_eur")
NEW_TEMPLATE = NEW_TEMPLATE.replace("#ff4f7e,#a44cff", "#397dff,#3fbdff")
NEW_TEMPLATE = NEW_TEMPLATE.replace("#a44cff38", "#397dff38")
NEW_TEMPLATE = NEW_TEMPLATE.replace("#ff4f7e,#9d4dff", "#397dff,#3fbdff")
NEW_TEMPLATE = NEW_TEMPLATE.replace("#9d4dff55", "#397dff55")
NEW_TEMPLATE = NEW_TEMPLATE.replace("#ff4f7e,#b348ef", "#397dff,#3fbdff")
NEW_TEMPLATE = NEW_TEMPLATE.replace("#b348ef35", "#397dff35")
NEW_TEMPLATE = NEW_TEMPLATE.replace("#21162f", "#14254a").replace("#111d35", "#102d4d")
NEW_TEMPLATE = NEW_TEMPLATE.replace("</body>", "<script>document.querySelectorAll('.nav a').forEach(a=>{if(a.href.includes('page=admin'))a.textContent='Benutzer'})</script></body>")
USER_LIST_OLD = """<br>{% for a in users %}<div class=label>{{a.name}} · {{a.role}}</div>{% endfor %}</section>"""
USER_LIST_NEW = """<br>{% for a in users %}<div class='slot'><b>{{a.name}}</b><div class='label'>Aktuelle Rolle: {{a.role}}</div>{% if a.name == user.name %}<p class='label'>Dein eigenes Konto ist geschützt.</p>{% else %}<form class='row' data-pin method='post' action='/users/{{a.name}}/role'><select name=role><option value=admin {% if a.role=='admin' %}selected{% endif %}>Admin</option><option value=manager {% if a.role=='manager' %}selected{% endif %}>Verwalter</option><option value=refill {% if a.role=='refill' %}selected{% endif %}>Auffüller</option><option value=viewer {% if a.role=='viewer' %}selected{% endif %}>Ansicht</option></select><button>Rechte speichern</button></form><form data-pin method='post' action='/users/{{a.name}}/delete'><button class=danger>Benutzer löschen</button></form>{% endif %}</div>{% endfor %}</section>"""
NEW_TEMPLATE = NEW_TEMPLATE.replace(USER_LIST_OLD, USER_LIST_NEW)
TIMEOUT_PANEL = """{% if page=='tech' and user.role=='admin' %}<section class='card'><h2>Auswahl-Timeout</h2><p class='label'>Ohne Geldeinwurf wird die Auswahl nach dieser Zeit zurückgesetzt.</p><form class='row' data-pin method='post' action='/tech/selection-timeout'><input name=seconds inputmode=numeric value='{{selection_timeout}}'><span class='label'>Sekunden</span><button>Speichern</button></form></section>{% endif %}"""
NEW_TEMPLATE = NEW_TEMPLATE.replace("</main>", TIMEOUT_PANEL + "</main>")
SYSTEM_PANEL = """{% if page=='tech' and user.role=='admin' %}<section class='card'><h2>Raspberry Pi</h2><p class='label'>Beim Herunterfahren die Stromversorgung erst trennen, wenn der Pi vollständig aus ist. Admin-PIN erforderlich.</p><div class='grid'><form data-pin method='post' action='/system/reboot'><button class='soft'>Neu starten</button></form><form data-pin method='post' action='/system/shutdown'><button class='danger'>Herunterfahren</button></form></div></section>{% endif %}"""
NEW_TEMPLATE = NEW_TEMPLATE.replace("</main>", SYSTEM_PANEL + "</main>")
SALES_PANEL = """{% if page=='products' and user.role in ('admin','manager') %}<section class='card'><h2>Verkaufsstatistik</h2><p class='label'>Nur bezahlte Ausgaben zählen hier mit.</p>{% for n,s in slots %}<div class='slot'><b>{{s.name}}</b><div class='label'>{{s.sold|default(0)}} verkauft · {{s.sales_revenue_eur}} Umsatz</div></div>{% endfor %}{% if user.role=='admin' %}<form data-pin method='post' action='/tech/reset-sales'><button class='danger'>Statistik zurücksetzen</button></form>{% endif %}</section>{% endif %}{% if page=='home' and user.role in ('admin','manager') and issues %}<section class='card'><h2>Wartungshinweise</h2>{% for issue in issues %}<p class='label'>• {{issue}}</p>{% endfor %}<p class='label'>Am Display erscheint nur „Wartung nötig“ – die Details stehen hier.</p></section>{% endif %}"""
NEW_TEMPLATE = NEW_TEMPLATE.replace("</main>", SALES_PANEL + "</main>")
RECOVERY_PANEL = """{% if page=='tech' and user.role=='admin' and hardware_error %}<section class='card'><h2>Ausgabe prüfen</h2><p class='label'>{{hardware_error}} Prüfe den tatsächlichen Bestand und korrigiere ihn zuerst unter „Fächer“. Erst danach den Fehler bestätigen.</p><form data-pin method='post' action='/tech/acknowledge-hardware'><button class='danger'>Bestand geprüft · Fehler bestätigen</button></form></section>{% endif %}"""
NEW_TEMPLATE = NEW_TEMPLATE.replace("</main>", RECOVERY_PANEL + "</main>")
NEW_TEMPLATE = NEW_TEMPLATE.replace("{{revenue}} ct", "{{revenue_eur}}")

@app.route("/")
def index():
    with lock:
        status = "Betriebsbereit" if is_operational() else "Wartungsmodus"
        key_status = "nicht erkannt" if last_key_on is None else ("AN" if last_key_on else "AUS")
        page = request.args.get("page", "home")
        if page not in ("home", "products", "service", "tech", "admin"): page = "home"
        slots_view = [(number, {**slot, "sales_revenue_eur": euro(slot.get("sales_revenue", 0))}) for number, slot in enumerate(state["slots"], 1)]
        return render_template_string(NEW_TEMPLATE, page=page, status=status, operational=is_operational(), enabled=state["enabled"], credit=state["credit"], revenue=state["revenue"], revenue_eur=euro(state["revenue"]), selected=selected_slot + 1 if selected_slot is not None else None, key_status=key_status, slots=slots_view, user=current_user(), users=state.get("users", []), servo=list(enumerate(state.get("servo", []), 1)), audit=state.get("audit", []), selection_timeout=state.get("selection_timeout", 30), mega_status="verbunden" if mega_ready else "nicht verbunden", busy=vend_in_progress, issues=machine_issues(), hardware_error=state["hardware_error"] or persistence_error)


@app.post("/slot/<int:number>")
@role_required("admin", "manager", "refill")
def set_slot(number):
    global selected_slot, selected_quantity, selection_changed_at
    if not 1 <= number <= SLOTS: return "Ungültiges Fach", 404
    with lock:
        slot = state["slots"][number - 1]
        slot["name"] = request.form.get("name", slot["name"]).strip()[:24] or f"Fach {number}"
        try:
            stock = int(request.form.get("stock", str(slot["stock"])).strip())
            price = int(request.form.get("price", str(slot["price"])).strip())
        except ValueError:
            return redirect("/")
        slot["stock"] = max(0, stock)
        slot["price"] = max(1, price)
        if selected_slot == number - 1 and selected_quantity > slot["stock"]:
            selected_slot = None
            selected_quantity = 0
            selection_changed_at = 0.0
            state["credit"] = 0
        audit(f"Fach {number} geändert")
        update_hardware()
    return return_to_page()


@app.post("/toggle")
@role_required("admin", "manager")
def toggle():
    global selected_slot, selected_quantity
    with lock:
        if vend_in_progress:
            return redirect("/")
        state["enabled"] = not state["enabled"]
        state["credit"] = 0
        selected_slot = None
        selected_quantity = 0
        audit("Automat " + ("freigegeben" if state["enabled"] else "gesperrt"))
        update_hardware()
    return redirect("/")


@app.post("/reset-credit")
@role_required("admin", "manager")
def reset_credit():
    global selected_slot, selected_quantity
    with lock:
        if vend_in_progress:
            return redirect("/")
        state["credit"] = 0
        selected_slot = None
        selected_quantity = 0
        update_hardware()
    return redirect("/")


@app.post("/tech/servo/<int:number>")
@role_required("admin")
def configure_servo(number):
    if not 1 <= number <= SLOTS or not admin_pin_ok():
        return redirect("/")
    try:
        home, output = int(request.form["home"]), int(request.form["output"])
        out_ms, back_ms = int(request.form["out_ms"]), int(request.form["back_ms"])
    except (KeyError, ValueError):
        return redirect("/")
    if not (0 <= home <= 180 and 0 <= output <= 180 and 50 <= out_ms <= 5000 and 50 <= back_ms <= 5000):
        return redirect("/")
    with lock:
        state["servo"][number - 1] = {"home": home, "output": output, "out_ms": out_ms, "back_ms": back_ms}
        send(f"SERVO {number} {home} {output} {out_ms} {back_ms}")
        audit(f"Servo {number} eingestellt")
    return redirect("/")


@app.post("/tech/selection-timeout")
@role_required("admin")
def configure_selection_timeout():
    if not admin_pin_ok():
        return return_to_page()
    try:
        seconds = int(request.form.get("seconds", "30"))
    except ValueError:
        return return_to_page()
    with lock:
        state["selection_timeout"] = max(5, min(300, seconds))
        audit(f"Auswahl-Timeout: {state['selection_timeout']} Sekunden")
    return return_to_page()


@app.post("/tech/reset-sales")
@role_required("admin")
def reset_sales():
    if not admin_pin_ok():
        return return_to_page()
    with lock:
        state["revenue"] = 0
        for slot in state["slots"]:
            slot["sold"] = 0
            slot["sales_revenue"] = 0
        audit("Verkaufsstatistik zurückgesetzt")
    return return_to_page()


@app.post("/tech/free-vend/<int:number>")
@role_required("admin")
def free_vend(number):
    global vend_in_progress, selected_slot, selected_quantity
    global pending_dispenses, active_dispense_slot, selection_changed_at
    if not 1 <= number <= SLOTS or not admin_pin_ok():
        return redirect("/")
    with lock:
        if (is_operational() or not mega_ready or vend_in_progress
                or state["hardware_error"] or persistence_error):
            return redirect("/")
        audit(f"Gratis-Ausgabe Fach {number}")
        vend_in_progress = True
        selected_slot = None
        selected_quantity = 0
        selection_changed_at = 0.0
        pending_dispenses = 1
        active_dispense_slot = number - 1
        send("RELAY OFF"); send("LEDS OFF"); lcd("Gratis-Ausgabe", f"Fach {number}")
        send(f"DISPENSE {number}")
    return redirect("/")


def service_required():
    # Tests sind im Wartungsmodus erlaubt: per Schluessel ODER Software-Sperre.
    return not mega_ready or (state["enabled"] and not service_mode) or vend_in_progress


@app.post("/test/servo/<int:number>")
@role_required("admin", "manager")
def test_servo(number):
    global vend_in_progress, selected_slot, selected_quantity
    global pending_dispenses, active_dispense_slot, selection_changed_at
    if not 1 <= number <= SLOTS: return redirect("/")
    with lock:
        if service_required(): return redirect("/")
        vend_in_progress = True
        selected_slot = None
        selected_quantity = 0
        selection_changed_at = 0.0
        pending_dispenses = 1
        active_dispense_slot = number - 1
        send("RELAY OFF"); send("LEDS OFF")
        lcd("Servo-Test", f"Fach {number}")
        send(f"DISPENSE {number}")
    return redirect("/")


@app.post("/test/leds/<int:number>")
@role_required("admin", "manager")
def test_leds(number):
    if not 0 <= number <= SLOTS: return redirect("/")
    with lock:
        if service_required(): return redirect("/")
        send("LEDS OFF")
        if number == 0:
            for index in range(1, SLOTS + 1): send(f"LED {index} ON")
            lcd("LED-Test", "Alle LEDs an")
        else:
            send(f"LED {number} ON")
            lcd("LED-Test", f"LED {number} an")
    return redirect("/")


@app.post("/test/relay/<state_name>")
@role_required("admin", "manager")
def test_relay(state_name):
    if state_name not in ("on", "off"): return redirect("/")
    with lock:
        if service_required(): return redirect("/")
        send(f"RELAY {state_name.upper()}")
        lcd("Relais-Test", "AN" if state_name == "on" else "AUS")
    return redirect("/")


@app.post("/test/lcd")
@role_required("admin", "manager")
def test_lcd():
    with lock:
        if service_required(): return redirect("/")
        line1 = request.form.get("line1", "")[:16].replace("|", " ")
        line2 = request.form.get("line2", "")[:16].replace("|", " ")
        lcd(line1 or "LCD-Test", line2)
    return redirect("/")


@app.post("/test/reset")
@role_required("admin", "manager")
def reset_test():
    with lock:
        if service_required(): return redirect("/")
        apply_servo_settings()
        update_hardware()
    return redirect("/")


@app.post("/tech/acknowledge-hardware")
@role_required("admin")
def acknowledge_hardware_error():
    global persistence_error
    if not admin_pin_ok():
        return return_to_page()
    with lock:
        if (not (service_mode or not state["enabled"]) or not mega_ready
                or vend_in_progress or not (state["hardware_error"] or persistence_error)):
            return return_to_page()
        previous_error = state["hardware_error"]
        state["hardware_error"] = ""
        try:
            audit("Hardware-Fehler nach Prüfung bestätigt")
        except OSError:
            state["hardware_error"] = previous_error
            return "Speicherfehler besteht weiter; Zustand nicht bestätigt.", 503
        persistence_error = ""
        update_hardware()
    return return_to_page()


@app.get("/manifest.webmanifest")
def manifest():
    return Response(json.dumps({"name": "MAOM Automat", "short_name": "MAOM", "start_url": "/", "display": "standalone", "background_color": "#fffaf6", "theme_color": "#ee4d74", "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}]}), mimetype="application/manifest+json")


@app.get("/service-worker.js")
def service_worker():
    return Response("self.addEventListener('install',e=>e.waitUntil(caches.open('maom-v1').then(c=>c.add('/'))));self.addEventListener('fetch',e=>e.respondWith(fetch(e.request).catch(()=>caches.match(e.request))));", mimetype="application/javascript")


@app.get("/icon.svg")
def icon():
    svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 180 180'><rect width='180' height='180' rx='40' fill='#ee4d74'/><circle cx='90' cy='75' r='44' fill='#fffaf6'/><circle cx='72' cy='68' r='8' fill='#20222d'/><circle cx='108' cy='68' r='8' fill='#20222d'/><path d='M62 96 Q90 122 118 96' fill='none' stroke='#20222d' stroke-width='8' stroke-linecap='round'/><text x='90' y='157' text-anchor='middle' font-family='Arial,sans-serif' font-size='26' font-weight='bold' fill='white'>MAOM</text></svg>"
    return Response(svg, mimetype="image/svg+xml")


if __name__ == "__main__":
    if not (TLS_CERT_FILE and TLS_KEY_FILE):
        raise RuntimeError(
            "HTTPS-Zertifikat fehlt. MAOM_TLS_CERT und MAOM_TLS_KEY setzen; "
            "siehe README.md."
        )
    load_state()
    threading.Thread(target=serial_loop, daemon=True).start()
    threading.Timer(STARTUP_ANIMATION_SECONDS, finish_startup_animation).start()
    app.run(host="0.0.0.0", port=WEB_PORT, ssl_context=(TLS_CERT_FILE, TLS_KEY_FILE))
