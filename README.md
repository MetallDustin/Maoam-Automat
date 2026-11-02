# MAOM-Automat

Steuerung für einen Snackautomaten mit Arduino Mega 2560 und Raspberry Pi. Die Weboberfläche läuft lokal über HTTPS; der Mega steuert Servos, Tasten, LEDs, Münzprüfer-Relais und I²C-LCD.

> **Sicherheitshinweis:** Dieses Projekt ist für Bastler, Entwicklung und private Testaufbauten gedacht. Betreibe es nur in einem abgesicherten lokalen Netzwerk und stelle die Weboberfläche nicht direkt ins Internet. Für unbeaufsichtigten, öffentlichen oder kommerziellen Betrieb sind zusätzliche Sicherheits-, Elektro- und Funktionstests sowie eine geeignete Produktionsumgebung erforderlich.

## Was macht das Projekt?

Der Raspberry Pi stellt die Weboberfläche und die Verkaufslogik bereit. Der Arduino Mega ist direkt mit der Hardware verbunden und steuert Taster, LEDs, Münzprüfer, LCD und Servos. Beide Geräte kommunizieren über USB-Seriell mit 9600 Baud.

Der Ablauf eines Verkaufs ist:

1. Der Arduino meldet dem Raspberry Pi `READY`.
2. Der Pi prüft Betriebsmodus, Bestand und Verbindung.
3. Ein Fach wird ausgewählt und der Münzprüfer freigegeben.
4. Bei ausreichendem Guthaben speichert der Pi Umsatz und Bestand.
5. Der Pi fordert den passenden Servo an; der Arduino meldet nach der Bewegung `OK`.
6. Nach jedem Neustart wird kein altes Guthaben übernommen.

Der Pi sendet regelmäßig einen Heartbeat. Wenn die Verbindung ausfällt, wird der Verkauf gesperrt. Während einer Ausgabe verlangt das Programm anschließend eine manuelle Prüfung, weil ein Computer nicht sicher erkennen kann, ob ein Artikel mechanisch bereits ausgegeben wurde.

## Schnellstart auf Raspberry Pi OS

### Voraussetzungen

- Raspberry Pi mit aktuellem Raspberry Pi OS und Netzwerkzugang
- Arduino Mega 2560 mit USB-Kabel
- lokales, abgesichertes Netzwerk
- fünf Servos, Taster, LEDs mit Vorwiderständen, LCD, Schlüsselschalter und Münzprüfer
- eine feste oder zuverlässig erreichbare lokale IP-Adresse des Pi

Das Repository kann direkt ohne GitHub-Anmeldung heruntergeladen werden:

```sh
sudo apt update
sudo apt install -y git avahi-daemon
sudo systemctl enable --now avahi-daemon
cd ~
git clone https://github.com/MetallDustin/Maoam-Automat.git
cd Maoam-Automat
```

Wenn du das Repository in ein bestimmtes Verzeichnis klonen möchtest:

```sh
git clone https://github.com/MetallDustin/Maoam-Automat.git ~/Maoam-Automat
cd ~/Maoam-Automat
```

### Automatische Installation

IP-Adresse anzeigen:

```sh
hostname -I
```

Installer mit dem tatsächlichen Hostnamen und der IPv4-Adresse ausführen:

```sh
sudo bash raspberry_pi/install_pi.sh \
  --hostname raspberrypi.local \
  --ip 192.168.178.50
```

Der Installer installiert Python, die benötigten Pakete, `mkcert`, ein lokales HTTPS-Zertifikat, eine virtuelle Python-Umgebung und den systemd-Dienst `maom-automat`. Außerdem wird der Benutzer für den Arduino-USB-Anschluss zur Gruppe `dialout` hinzugefügt. Nach der Installation ist eine Ab- und Anmeldung oder ein Neustart nötig, damit die Gruppenänderung aktiv wird.

Dienst prüfen:

```sh
systemctl status maom-automat --no-pager
journalctl -u maom-automat -f
```

### Erste Anmeldung

Die Oberfläche ist danach unter `https://raspberrypi.local:8443` erreichbar. Falls `.local` im Netzwerk nicht aufgelöst wird, nutze `https://192.168.178.50:8443` mit der tatsächlichen Pi-IP.

Beim ersten Aufruf gibt es noch kein Konto. Lege einen Administrator mit Benutzername, Passwort und Admin-PIN an. Die PIN wird für geschützte Wartungs- und Systemaktionen verwendet; sie ist nicht die Arduino-PIN und wird nicht in der Firmware gespeichert.

`mkcert` erzeugt eine lokale Zertifizierungsstelle. Für iPhone oder andere Geräte muss nur `rootCA.pem` installiert und anschließend als vertrauenswürdig aktiviert werden. `rootCA-key.pem` ist ein privater Schlüssel und darf niemals übertragen werden.

## Arduino-Firmware installieren

Den Server vor dem Upload stoppen, damit der serielle Port frei ist:

```sh
sudo systemctl stop maom-automat
```

Arduino CLI installieren und den Mega vorbereiten:

```sh
arduino-cli core update-index
arduino-cli core install arduino:avr@1.8.8
arduino-cli lib install "LiquidCrystal I2C@1.1.2"
arduino-cli lib install "Servo@1.3.0"
```

Port prüfen und Firmware bauen:

```sh
arduino-cli board list
cd ~/Maoam-Automat
arduino-cli compile \
  --fqbn arduino:avr:mega \
  arduino_mega/maom_mega
```

Firmware hochladen. `/dev/ttyACM0` kann bei deinem Pi anders heißen:

```sh
arduino-cli upload \
  --port /dev/ttyACM0 \
  --fqbn arduino:avr:mega \
  arduino_mega/maom_mega
```

Danach Server wieder starten und die Verbindung beobachten:

```sh
sudo systemctl start maom-automat
journalctl -u maom-automat -f
```

Im Log sollten unter anderem `<< READY` und danach die Statusmeldungen des Arduino erscheinen. `READY` wird nicht im Upload-Befehl angezeigt, sondern beim Start der Firmware an den laufenden Server gesendet.

## Erster Hardwaretest

Vor dem ersten Verkauf:

1. Keine Ware einlegen und Servo-Gestänge lösen.
2. Schlüsselschalter in den Wartungsmodus stellen.
3. LEDs, LCD, Taster und Relais einzeln prüfen.
4. Jeden Servo einzeln testen und `homeAngle`/`dispenseAngle` vorsichtig kalibrieren.
5. Münzprüfer-Signal und Impulszählung prüfen.
6. Einen einzelnen Verkauf mit wenig Ware durchführen.
7. Erst nach erfolgreichem Test Fächer bestücken und Preise eintragen.
8. Schlüsselschalter in den Verkaufsmodus stellen.

## Betrieb und Wartung

Der Server läuft als systemd-Dienst und startet nach einem Raspberry-Pi-Neustart automatisch:

```sh
sudo systemctl restart maom-automat
sudo systemctl stop maom-automat
sudo systemctl start maom-automat
sudo systemctl disable maom-automat
sudo systemctl enable maom-automat
```

Logs und Fehlerdiagnose:

```sh
journalctl -u maom-automat -n 100 --no-pager
journalctl -u maom-automat -f
arduino-cli board list
ls -l /dev/ttyACM*
```

Bei einem USB-Abbruch während einer Ausgabe bleibt der Verkauf gesperrt. Tatsächlich ausgegebene Ware und Bestand müssen manuell verglichen werden; erst danach darf der Hardwarefehler im Adminbereich bestätigt werden.

## Updates

Vor einem Update den Dienst stoppen und den aktuellen Stand sichern:

```sh
sudo systemctl stop maom-automat
cp raspberry_pi/automaten_state.json ~/automaten_state.backup.json 2>/dev/null || true
git pull --ff-only
sudo bash raspberry_pi/install_pi.sh \
  --hostname raspberrypi.local \
  --ip 192.168.178.50
sudo systemctl start maom-automat
```

Die Zustandsdatei enthält Benutzer, PIN-Hash, Bestände, Umsätze und Servo-Einstellungen. Sie gehört nicht ins Repository und sollte vor Änderungen gesichert werden.

## Weitere Dokumentation

Die vollständige Verdrahtungs-, Installations-, HTTPS- und Ersttest-Anleitung steht in **[docs/BUILD.md](docs/BUILD.md)**. Die technische Übersicht der Komponenten und des seriellen Protokolls findest du in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Pinbelegung Mega

| Bauteil | Pins |
|---|---|
| Servo-Steuersignale 1–5 | D2–D6 |
| Münzprüfer-Signal | A0 |
| Auswahltasten 1–5 | A1–A5 |
| LEDs 1–5 | A6–A10 |
| Schlüsselschalter | A11 |
| Relais für Münzprüfer | A12 |
| I²C-LCD | SDA D20, SCL D21 |

Taster und Schlüsselschalter liegen jeweils zwischen Pin und GND. Jede LED benötigt einen Vorwiderstand. **Servos nicht aus dem 5-V-Pin des Mega versorgen:** Nutze ein geeignetes externes Netzteil und verbinde dessen GND mit dem Mega-GND. Prüfe vor dem Anschluss die Spannungs- und Pegelverträglichkeit der Module.

Die Firmware wertet Münzimpulse als 1 = 5 ct, 2 = 10 ct und 3 = 20 ct aus. Der LCD-Standardwert ist `0x27` (manche Adapter verwenden `0x3F`).

## Verzeichnisübersicht

| Pfad | Inhalt |
|---|---|
| `arduino_mega/maom_mega/` | Firmware für den Mega |
| `raspberry_pi/` | Flask-Webserver, HTTPS-Helfer und Abhängigkeiten |
| `docs/BUILD.md` | Schritt-für-Schritt-Nachbau und Erstinbetriebnahme |
| `docs/ARCHITECTURE.md` | Komponenten und serielle Kommunikation |
| `tests/` | Automatisierte Tests der Verkaufs- und Serverlogik |

Die Servowinkel sind mechanische Platzhalter und müssen vor dem Verkauf ohne Ware kalibriert werden. Nach einem Verbindungsabbruch während einer Ausgabe ist eine manuelle Prüfung von Ware und Bestand erforderlich.
