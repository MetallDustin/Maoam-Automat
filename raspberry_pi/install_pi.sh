#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Aufruf:
  sudo bash raspberry_pi/install_pi.sh --hostname raspberrypi.local --ip 192.168.178.50

Optionen:
  --hostname NAME   Hostname fuer das lokale HTTPS-Zertifikat
  --ip ADDRESS      Lokale IP-Adresse des Raspberry Pi
  --repo DIR        Repository-Verzeichnis (Standard: automatisch erkannt)
  --help            Diese Hilfe anzeigen

Das Skript richtet den MAOM-Server als systemd-Dienst ein. Es erstellt kein
Admin-Konto; dieses wird beim ersten Browser-Aufruf eingerichtet.
EOF
}

die() {
  echo "Fehler: $*" >&2
  exit 1
}

if [ "$(id -u)" -ne 0 ]; then
  die "Bitte mit sudo ausfuehren."
fi

HOSTNAME_VALUE=""
IP_VALUE=""
REPO_DIR=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --hostname)
      [ "$#" -ge 2 ] || die "--hostname benoetigt einen Wert."
      HOSTNAME_VALUE="$2"
      shift 2
      ;;
    --ip)
      [ "$#" -ge 2 ] || die "--ip benoetigt einen Wert."
      IP_VALUE="$2"
      shift 2
      ;;
    --repo)
      [ "$#" -ge 2 ] || die "--repo benoetigt einen Wert."
      REPO_DIR="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unbekannte Option: $1"
      ;;
  esac
done

[ -n "$HOSTNAME_VALUE" ] || die "--hostname fehlt."
[ -n "$IP_VALUE" ] || die "--ip fehlt."

if [ -z "$REPO_DIR" ]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
  REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
else
  REPO_DIR="$(cd -- "$REPO_DIR" && pwd)"
fi

[ -f "$REPO_DIR/raspberry_pi/automaten_server.py" ] || die "Kein MAOM-Repository in $REPO_DIR gefunden."
command -v apt-get >/dev/null 2>&1 || die "Dieses Skript benoetigt Raspberry Pi OS mit apt."
command -v systemctl >/dev/null 2>&1 || die "systemd wurde nicht gefunden."

INSTALL_USER="${SUDO_USER:-}"
if [ -z "$INSTALL_USER" ] || [ "$INSTALL_USER" = "root" ]; then
  INSTALL_USER="$(stat -c '%U' "$REPO_DIR")"
fi
[ -n "$INSTALL_USER" ] && id "$INSTALL_USER" >/dev/null 2>&1 || die "Kein gueltiger Installationsbenutzer gefunden."
INSTALL_GROUP="$(id -gn "$INSTALL_USER")"

echo "Installiere Systempakete..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  git \
  libnss3-tools \
  mkcert \
  python3 \
  python3-venv \
  python3-pip

usermod -aG dialout "$INSTALL_USER"

VENV_DIR="$REPO_DIR/raspberry_pi/.venv"
echo "Erstelle Python-Umgebung..."
runuser -u "$INSTALL_USER" -- python3 -m venv "$VENV_DIR"
runuser -u "$INSTALL_USER" -- "$VENV_DIR/bin/python" -m pip install --upgrade pip
runuser -u "$INSTALL_USER" -- "$VENV_DIR/bin/pip" install -r "$REPO_DIR/raspberry_pi/requirements.txt"

TLS_DIR="/home/$INSTALL_USER/maom-tls"
install -d -o "$INSTALL_USER" -g "$INSTALL_GROUP" -m 700 "$TLS_DIR"
echo "Erzeuge lokales HTTPS-Zertifikat..."
runuser -u "$INSTALL_USER" -- env HOME="/home/$INSTALL_USER" \
  mkcert -install
runuser -u "$INSTALL_USER" -- env HOME="/home/$INSTALL_USER" \
  mkcert -cert-file "$TLS_DIR/maom-cert.pem" -key-file "$TLS_DIR/maom-key.pem" \
  "$HOSTNAME_VALUE" "$IP_VALUE"
chown "$INSTALL_USER:$INSTALL_GROUP" "$TLS_DIR/maom-cert.pem" "$TLS_DIR/maom-key.pem"
chmod 600 "$TLS_DIR/maom-key.pem"

CONFIG_DIR="/etc/maom-automat"
install -d -o root -g root -m 755 "$CONFIG_DIR"
SESSION_SECRET="$(runuser -u "$INSTALL_USER" -- python3 -c 'import secrets; print(secrets.token_hex(32))')"
cat > "$CONFIG_DIR/maom.env" <<EOF
MAOM_TLS_CERT=$TLS_DIR/maom-cert.pem
MAOM_TLS_KEY=$TLS_DIR/maom-key.pem
MAOM_SESSION_SECRET=$SESSION_SECRET
EOF
chown root:root "$CONFIG_DIR/maom.env"
chmod 600 "$CONFIG_DIR/maom.env"

install -o root -g root -m 0755 "$REPO_DIR/raspberry_pi/maom-system-action" \
  /usr/local/sbin/maom-system-action

SERVICE_FILE="/etc/systemd/system/maom-automat.service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=MAOM Automat Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$INSTALL_USER
Group=$INSTALL_GROUP
WorkingDirectory=$REPO_DIR/raspberry_pi
EnvironmentFile=$CONFIG_DIR/maom.env
ExecStart=$VENV_DIR/bin/python $REPO_DIR/raspberry_pi/automaten_server.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$REPO_DIR/raspberry_pi

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable maom-automat.service
systemctl restart maom-automat.service

cat <<EOF

Installation abgeschlossen.
URL: https://$HOSTNAME_VALUE:8443
Dienststatus: systemctl status maom-automat
Logs: journalctl -u maom-automat -f
Zertifikatsordner: $TLS_DIR

Wichtig:
- Der Benutzer $INSTALL_USER muss sich neu anmelden, damit die dialout-Gruppe aktiv wird.
- Nur rootCA.pem aus "mkcert -CAROOT" auf das iPhone uebertragen; niemals rootCA-key.pem.
- Das Admin-Konto wird beim ersten Aufruf im Browser eingerichtet.
- Die Weboberflaeche darf nicht ins Internet weitergeleitet werden.
- Die optionalen Web-Schaltflaechen fuer Reboot/Shutdown benoetigen eine separate,
  eng begrenzte sudoers-Regel. Siehe docs/BUILD.md.
EOF
