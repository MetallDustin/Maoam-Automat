# MAOM-Automat

## Pinbelegung Mega

| Bauteil | Pins |
|---|---|
| Servo 1–5 | D2–D6 |
| Münzprüfer-Signal | A0 |
| Auswahltasten 1–5 | A1–A5 |
| LEDs 1–5 | A6–A10 |
| Schlüsselschalter | A11 |
| Relais für Münzprüfer | A12 |
| I²C-LCD | SDA D20, SCL D21 |

Tasten und Schlüssel liegen jeweils zwischen Pin und GND. Die LEDs benötigen Vorwiderstände (z. B. 220–330 Ohm). Das LCD wird standardmäßig mit I²C-Adresse `0x27` angesprochen.

## Servo-Test und Kalibrierung

Die Winkel `HOME_ANGLE` und `DISPENSE_ANGLE` in der `.ino`-Datei sind Platzhalter. Zuerst mit nur einem Fach und ohne Ware testen. Während einer Ausgabe ist ausschließlich der betroffene Servo angehängt; anschließend wird er wieder getrennt. So ziehen die Servos im Leerlauf keinen Strom.

## Raspberry Pi starten (HTTPS)

Auf dem Pi:

Die iPhone-Web-App läuft ausschließlich über HTTPS. Für den lokalen Automaten ist ein mit `mkcert` erstelltes, lokales Zertifikat passend. Installiere `mkcert` nach dessen [offizieller Anleitung](https://github.com/FiloSottile/mkcert), ermittle die feste Pi-IP und führe dann aus:

```bash
cd raspberry_pi
chmod +x setup_local_https.sh
./setup_local_https.sh raspberrypi.local 192.168.178.50
export MAOM_SERIAL_PORT=/dev/ttyACM0
export MAOM_TLS_CERT="$HOME/maom-tls/maom-cert.pem"
export MAOM_TLS_KEY="$HOME/maom-tls/maom-key.pem"
python3 automaten_server.py
```

Die IP im Beispiel durch die tatsächliche Pi-IP ersetzen. Die Seite ist dann z. B. unter `https://raspberrypi.local:8443` erreichbar.

Damit das iPhone das lokale Zertifikat akzeptiert, nur die Datei `rootCA.pem` aus `mkcert -CAROOT` auf das iPhone übertragen und installieren. Danach in **Einstellungen → Allgemein → Info → Zertifikatsvertrauenseinstellungen** das volle Vertrauen für dieses Root-Zertifikat einschalten. Den privaten Schlüssel `rootCA-key.pem` niemals kopieren oder teilen. Apple beschreibt die nötige manuelle Vertrauensaktivierung in seiner [Support-Anleitung](https://support.apple.com/en-au/102390).

Die Oberfläche ist für iPhones optimiert: in Safari **Teilen → Zum Home-Bildschirm** wählen, um sie ohne Browserleiste wie eine App zu öffnen. Das Testmenü für Servos, LEDs, LCD und Relais ist nur im Wartungsmodus über den Schlüsselschalter zugänglich. Falls der Mega nicht unter `/dev/ttyACM0` erscheint, `SERIAL_PORT` im Python-Programm auf den tatsächlichen Port ändern.

Der Ablauf ist: Zuerst leuchten alle gefüllten Fächer. Nach der Auswahl leuchtet nur dieses Fach und der Münzprüfer wird freigegeben. Sobald der eingeworfene Betrag den Preis dieses Fachs erreicht, startet die Ausgabe. Der Schlüsselschalter aktiviert den Wartungsmodus; bei `KEY ON` ist der Verkauf gesperrt. Falls der Schalter andersherum arbeiten soll, `KEY_ON_IS_SERVICE` im Python-Programm auf `False` setzen.
