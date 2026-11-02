# Mitwirken

Bitte keine privaten Zertifikate, Schlüssel, WLAN-Daten, Passwörter oder echten Verkaufsdaten hochladen. Lokale Laufzeitdaten werden durch `.gitignore` ausgeschlossen.

Änderungen an der Hardware sollten zunächst ohne Ware getestet werden. Bei Änderungen an der seriellen Kommunikation immer Arduino- und Raspberry-Pi-Teil zusammen prüfen.

Vor einem Pull Request die Python-Tests und den Syntaxcheck ausführen:

```bash
python -m pip install -r raspberry_pi/requirements.txt
python -m unittest discover -s tests -v
python3 -m py_compile raspberry_pi/automaten_server.py
bash -n raspberry_pi/setup_local_https.sh raspberry_pi/install_pi.sh raspberry_pi/maom-system-action
```

Firmware-Build für den Mega 2560:

```bash
arduino-cli core update-index
arduino-cli core install arduino:avr@1.8.8
arduino-cli lib install "LiquidCrystal I2C@1.1.2"
arduino-cli lib install "Servo@1.3.0"
arduino-cli compile --fqbn arduino:avr:mega arduino_mega/maom_mega
```

Die vollständigen Schritte für Verdrahtung, Raspberry Pi, HTTPS und Erstinbetriebnahme stehen in [docs/BUILD.md](docs/BUILD.md).
