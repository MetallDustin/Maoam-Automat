# MAOM-Automat

Steuerung für einen Snackautomaten mit Arduino Mega 2560 und Raspberry Pi. Die Weboberfläche läuft lokal über HTTPS; der Mega steuert Servos, Tasten, LEDs, Münzprüfer-Relais und I²C-LCD.

> **Sicherheitshinweis:** Dieses Projekt ist für Bastler, Entwicklung und private Testaufbauten gedacht. Betreibe es nur in einem abgesicherten lokalen Netzwerk und stelle die Weboberfläche nicht direkt ins Internet. Für unbeaufsichtigten, öffentlichen oder kommerziellen Betrieb sind zusätzliche Sicherheits-, Elektro- und Funktionstests sowie eine geeignete Produktionsumgebung erforderlich.

## Schnellstart

Die komplette Verdrahtungs-, Installations-, HTTPS- und Ersttest-Anleitung steht in **[docs/BUILD.md](docs/BUILD.md)**. Sie enthält auch die genauen Befehle zum Kompilieren und Hochladen der Arduino-Firmware.

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
