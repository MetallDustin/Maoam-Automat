# Architektur

```text
Browser / iPhone
        │ HTTPS
        ▼
Raspberry Pi: Flask-Weboberfläche und Verkaufslogik
        │ USB-Seriell, 9600 Baud
        ▼
Arduino Mega: Ein-/Ausgabe und Motorsteuerung
        ├── Münzprüfer, Tasten, Schlüsselschalter
        └── Servos, LEDs, Relais, LCD
```

## Bausteine

| Baustein | Pfad | Verantwortlichkeit |
| --- | --- | --- |
| Firmware | `arduino_mega/maom_mega/maom_mega.ino` | Hardware lesen, Servos auslösen, Befehle empfangen |
| Server | `raspberry_pi/automaten_server.py` | Verkauf, Benutzer, Bestand, Weboberfläche |
| HTTPS-Helfer | `raspberry_pi/setup_local_https.sh` | lokales Zertifikat erzeugen |
| Python-Abhängigkeiten | `raspberry_pi/requirements.txt` | reproduzierbare Pi-Installation |

Die bestehende Implementierung unterstützt fünf Fächer. Diese Anzahl ist derzeit in Firmware, Verdrahtung und Server gemeinsam festgelegt.
