# Nachbauanleitung

Diese Anleitung ergänzt den vorhandenen Quellcode, ohne dessen Einstellungen zu verändern.

## 1. Arduino Mega

1. Arduino IDE installieren und den Arduino Mega per USB anschließen.
2. Im Bibliotheksverwalter die Bibliothek **LiquidCrystal I2C** installieren. `Servo` ist bereits Teil der Arduino-Standardbibliothek.
3. `arduino_mega/maom_mega/maom_mega.ino` in der IDE öffnen.
4. Board **Arduino Mega oder Mega 2560** und den passenden Anschluss auswählen.
5. Die Servo-Winkel zunächst ohne Ware testen und erst dann die Firmware hochladen.

Die vollständige Pinbelegung steht in der README. Alle Tasten und der Schlüsselschalter werden gegen GND angeschlossen; LEDs brauchen Vorwiderstände.

## 2. Raspberry Pi

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
git clone https://github.com/DEIN-BENUTZERNAME/maom-automat.git
cd maom-automat
python3 -m venv .venv
. .venv/bin/activate
pip install -r raspberry_pi/requirements.txt
```

Der Mega sollte anschließend gewöhnlich als `/dev/ttyACM0` erscheinen. Falls nicht, den Wert von `SERIAL_PORT` in `raspberry_pi/automaten_server.py` auf den tatsächlichen Anschluss anpassen.

Für HTTPS bitte die bestehende Anleitung in der README befolgen. Zertifikate, Schlüssel und `automaten_state.json` gehören nicht ins Repository.

## 3. Ersttest

1. Den Mega per USB anschließen und den Schlüsselschalter auf Wartungsmodus stellen.
2. Im Ordner `raspberry_pi` die erforderlichen TLS-Umgebungsvariablen setzen.
3. `python3 automaten_server.py` starten.
4. Die Weboberfläche öffnen, den ersten Admin anlegen und die Hardware-Tests ausführen.
5. Erst nach erfolgreichem Test Ware einlegen und die Fachbestände konfigurieren.

## Projektgrenzen

Die Firmware und die Webanwendung sind getrennte Bausteine. Sie kommunizieren über USB-Seriell mit 9600 Baud und Zeilenbefehlen. Wer die Hardware erweitert, sollte die Kompatibilität beider Seiten mitprüfen.
