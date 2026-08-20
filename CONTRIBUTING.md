# Mitwirken

Bitte keine privaten Zertifikate, Schlüssel, WLAN-Daten, Passwörter oder echten Verkaufsdaten hochladen. Lokale Laufzeitdaten werden durch `.gitignore` ausgeschlossen.

Änderungen an der Hardware sollten zunächst ohne Ware getestet werden. Bei Änderungen an der seriellen Kommunikation immer Arduino- und Raspberry-Pi-Teil zusammen prüfen.

Vor einem Pull Request bitte die Python-Datei mindestens mit folgendem Befehl auf Syntax prüfen:

```bash
python3 -m py_compile raspberry_pi/automaten_server.py
```
