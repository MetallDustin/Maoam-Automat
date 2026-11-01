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
| Systemaktions-Helfer | `raspberry_pi/maom-system-action` | eng begrenzter Neustart/Shutdown mit optionaler sudo-Regel |
| Python-Abhängigkeiten | `raspberry_pi/requirements.txt` | reproduzierbare Pi-Installation |

Die bestehende Implementierung unterstützt fünf Fächer. Diese Anzahl ist derzeit in Firmware, Verdrahtung und Server gemeinsam festgelegt.

## Start und Fehlerbehandlung

Der Server startet auch ohne angeschlossenen Mega. Ein Hintergrund-Thread versucht die serielle Verbindung regelmäßig erneut. Erst nach der Firmwaremeldung `READY` gilt die Hardware als verbunden; danach überträgt der Server die gespeicherten Servo-Kalibrierungen und stellt LEDs, Relais und LCD passend zum Zustand wieder her. Während des Betriebs sendet der Pi alle zwei Sekunden `PING`; die Firmware schaltet das Münzprüfer-Relais nach fünf Sekunden ohne Pi-Befehl ab.

Ein Verbindungsabbruch sperrt den Verkauf. Bricht er während einer Ausgabe ab, wird ein persistenter Prüfhinweis gesetzt, weil der Pi nicht sicher feststellen kann, ob Ware bereits ausgegeben wurde. Vor dem Bestätigen muss der Bestand mit der tatsächlich ausgegebenen Ware abgeglichen werden.

Der Zustand wird in `raspberry_pi/automaten_state.json` atomar gespeichert. Ein beschädigter oder ungültiger Zustand stoppt den Serverstart mit einer klaren Fehlermeldung, statt stillschweigend mit leeren Beständen oder Benutzern weiterzuarbeiten.

## Prüfungen

Die Python-Tests simulieren das serielle Gerät und prüfen Verkaufsabläufe, Wartungsregeln, Wiederanlauf, Zustandsdatei und Weboberfläche. Ein GitHub-Actions-Workflow kompiliert zusätzlich die Firmware für den Mega 2560 mit festgelegten Bibliotheksversionen. Siehe [Aufbau und erster Start](BUILD.md).
