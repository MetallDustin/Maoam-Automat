# Aufbau und erster Start

Diese Anleitung führt vom frischen Raspberry Pi bis zum vorsichtigen Ersttest. Vor dem Anschluss von Servos oder Münzprüfer zuerst die Sicherheits- und Verdrahtungshinweise lesen.

## 1. Teile und Verdrahtung

Benötigt werden ein Arduino Mega 2560, ein Raspberry Pi, ein I²C-LCD mit PCF8574-Adapter, fünf Servos, fünf Taster, fünf LEDs mit Vorwiderständen, ein Schlüsselschalter und ein Münzprüfer mit passendem Relais-/Signalschnittstellenmodul.

| Bauteil | Mega-Pin |
|---|---|
| Servos 1–5, Steuersignal | D2–D6 |
| Münzprüfer-Signal | A0 |
| Tasten 1–5 | A1–A5 |
| LEDs 1–5 | A6–A10 |
| Schlüsselschalter | A11 |
| Relais-Eingang für Münzprüfer | A12 |
| LCD | SDA D20, SCL D21 |

Taster und Schlüsselschalter werden jeweils zwischen dem angegebenen Pin und GND angeschlossen. Die Eingänge verwenden den internen Pull-up. Jede LED braucht einen eigenen Vorwiderstand (typisch 220–330 Ohm). Das LCD wird mit `0x27` angesprochen; manche Adapter verwenden `0x3F`.

**Servos nicht aus dem 5-V-Pin des Mega versorgen.** Verwende ein ausreichend dimensioniertes, geregeltes Netzteil für die Servos und verbinde dessen GND mit dem GND des Mega. Prüfe vor dem Einschalten die zulässige Spannung und Polarität jedes Moduls. Verbinde den Münzprüfer nicht direkt mit einem Arduino-Pin, wenn sein Ausgang nicht ausdrücklich 5-V-kompatibel ist; nutze einen geeigneten Pegelwandler oder Optokoppler. Das Relais muss zur Spannung und zum Strom des Münzprüfers passen.

Die Firmware zählt Münzprüfer-Impulse: 1 Impuls = 5 ct, 2 = 10 ct, 3 = 20 ct. Prüfe und konfiguriere den Münzprüfer passend dazu. Falls das Signal nicht aktiv-low ist, muss `COIN_ACTIVE_LOW` in der Firmware angepasst werden.

## 2. Firmware bauen und hochladen

Installiere die [Arduino CLI](https://arduino.github.io/arduino-cli/latest/installation/) oder die Arduino IDE. Mit der CLI installierst du das Mega-Boardpaket und die beiden benötigten Bibliotheken:

```sh
arduino-cli core update-index
arduino-cli core install arduino:avr@1.8.8
arduino-cli lib install "LiquidCrystal I2C@1.1.2"
arduino-cli lib install "Servo@1.3.0"
arduino-cli compile --fqbn arduino:avr:mega arduino_mega/maom_mega
```

Zum Hochladen den richtigen Port einsetzen (`COM3` unter Windows ist nur ein Beispiel; auf Raspberry Pi OS meist `/dev/ttyACM0`):

```sh
arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:mega arduino_mega/maom_mega
```

In der Arduino IDE kannst du stattdessen `arduino_mega/maom_mega/maom_mega.ino` öffnen, **Arduino Mega or Mega 2560** auswählen und die Bibliotheken **LiquidCrystal I2C 1.1.2** sowie **Servo 1.3.0** installieren.

Die Servo-Winkel stehen am Anfang von `maom_mega.ino` in `homeAngle` und `dispenseAngle`. Die Standardwerte sind nur Platzhalter: beim ersten Test Hörner abnehmen oder Gestänge lösen, ohne Ware testen und jeden Winkel vorsichtig an den tatsächlichen Mechanismus anpassen. Im Betrieb wird nur der gerade benötigte Servo angeschlossen und danach wieder getrennt.

## 3. Raspberry Pi vorbereiten

Schließe den Mega per USB an. Bei Raspberry Pi OS füge den Benutzer, unter dem der Server gestartet wird, der seriellen Gruppe hinzu und melde dich danach neu an:

```sh
sudo usermod -aG dialout "$USER"
```

Installiere Python und lade dieses Repository:

```sh
sudo apt update
sudo apt install -y git python3-venv python3-pip
git clone https://github.com/MetallDustin/Maoam-Automat.git
cd Maoam-Automat/raspberry_pi
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Der Server verwendet standardmäßig `/dev/ttyACM0` und startet auch dann, wenn der Mega beim Start noch nicht angeschlossen ist. Er versucht die Verbindung regelmäßig erneut. Während des Betriebs prüft ein Heartbeat die Verbindung; die Firmware schaltet das Münzprüfer-Relais ab, wenn fünf Sekunden lang kein Pi-Befehl eintrifft. Für einen anderen Port setze vor dem Start beispielsweise `export MAOM_SERIAL_PORT=/dev/ttyUSB0`.

## 4. Lokales HTTPS einrichten

Die Web-App benötigt HTTPS, insbesondere für die Installation auf dem iPhone-Home-Bildschirm. Installiere `mkcert` nach der [offiziellen Anleitung](https://github.com/FiloSottile/mkcert). Verwende einen festen Hostnamen und die feste lokale IP-Adresse des Pi:

```sh
bash setup_local_https.sh raspberrypi.local 192.168.178.50
```

Ersetze `192.168.178.50` durch die tatsächliche feste IP-Adresse des Pi. Die Zertifikatsdateien werden in `$HOME/maom-tls` angelegt. Setze die Variablen in dem Terminal, in dem der Server läuft:

```sh
export MAOM_TLS_CERT="$HOME/maom-tls/maom-cert.pem"
export MAOM_TLS_KEY="$HOME/maom-tls/maom-key.pem"
export MAOM_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python automaten_server.py
```

Die Sitzungsschlüssel-Variable muss bei jedem späteren Start denselben geheimen Wert behalten, damit bestehende Anmeldesitzungen gültig bleiben. Bewahre ihn sicher auf und veröffentliche ihn nicht. Alternativ wird ohne Variable bei jedem Serverstart ein neuer Sitzungsschlüssel erzeugt.

Öffne `https://raspberrypi.local:8443`. Den Ordner mit der lokalen Zertifizierungsstelle zeigt `mkcert -CAROOT` an. Damit das iPhone dem Zertifikat vertraut, übertrage ausschließlich die Datei `rootCA.pem` aus diesem Ordner auf das iPhone, installiere das Profil und aktiviere unter **Einstellungen → Allgemein → Info → Zertifikatsvertrauenseinstellungen** das volle Vertrauen. **`rootCA-key.pem` ist ein privater Schlüssel und darf den Pi niemals verlassen.**

Beim ersten Aufruf richtest du ein Administratorkonto mit Passwort und Admin-PIN ein. Drehe den Schlüsselschalter in den Wartungsmodus, bevor du Hardwaretests ausführst.

## 5. Ersttest und Verkauf

1. Ohne Ware und mit gelöstem Servo-Gestänge jeden Servo einzeln testen und die Winkel kalibrieren.
2. LEDs, LCD, Schlüsselschalter, Münzprüfer-Relais und Münzimpulse einzeln prüfen.
3. Einen Verkauf mit einem einzelnen Fach und einem kleinen, bekannten Betrag testen.
4. Erst nach bestandenen Tests Ware einlegen und Bestand sowie Preise im Menü **Fächer** eintragen.
5. Den Schlüsselschalter zurück in den Verkaufsmodus drehen.

Nach einem USB-Abbruch während einer Ausgabe sperrt der Server den Verkauf und verlangt eine Prüfung. Kontrolliere die tatsächlich ausgegebene Ware, korrigiere den Bestand und bestätige den Fehler anschließend im Adminbereich **Technik**. Der Server kann nach einem Strom-/USB-Ausfall nicht sicher feststellen, ob ein Servo einen Artikel bereits ausgegeben hat.

## Optional: Raspberry Pi per Weboberfläche neu starten

Die Neustart-/Herunterfahren-Schaltflächen benötigen eine eng begrenzte `sudo`-Regel. Installiere nur den mitgelieferten festen Aktionshelfer:

```sh
sudo install -o root -g root -m 0755 maom-system-action /usr/local/sbin/maom-system-action
whoami
sudo visudo -f /etc/sudoers.d/maom-automat
```

Trage in der Datei die Ausgabe von `whoami` anstelle von `<BENUTZER>` ein:

```text
<BENUTZER> ALL=(root) NOPASSWD: /usr/local/sbin/maom-system-action reboot, /usr/local/sbin/maom-system-action shutdown
```

Speichere die Datei und beende `visudo`; das Werkzeug prüft die Syntax. Der Helfer akzeptiert ausschließlich `reboot` oder `shutdown`. Ohne diese optionale Einrichtung funktionieren die entsprechenden Schaltflächen nicht.

## Prüfungen für Entwickler

```sh
python -m pip install -r raspberry_pi/requirements.txt
python -m unittest discover -s tests -v
python -m py_compile raspberry_pi/automaten_server.py
arduino-cli compile --fqbn arduino:avr:mega arduino_mega/maom_mega
```
