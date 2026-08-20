#!/usr/bin/env bash
# Erzeugt ein lokales HTTPS-Zertifikat fuer den MAOM-Automaten.
# Aufruf: ./setup_local_https.sh raspberrypi.local 192.168.178.50
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Aufruf: $0 <hostname> [ip-adresse]"
  exit 1
fi

if ! command -v mkcert >/dev/null 2>&1; then
  echo "mkcert ist nicht installiert. Anleitung: https://github.com/FiloSottile/mkcert"
  exit 1
fi

CERT_DIR="$HOME/maom-tls"
mkdir -p "$CERT_DIR"
mkcert -install
mkcert -cert-file "$CERT_DIR/maom-cert.pem" -key-file "$CERT_DIR/maom-key.pem" "$@"
chmod 600 "$CERT_DIR/maom-key.pem"

echo
echo "Fertig. Zum Starten des Automatenprogramms:"
echo "export MAOM_TLS_CERT=$CERT_DIR/maom-cert.pem"
echo "export MAOM_TLS_KEY=$CERT_DIR/maom-key.pem"
echo "python3 automaten_server.py"
echo
echo "Wichtig: Nur rootCA.pem auf das iPhone uebertragen und dort als vertrauenswuerdig aktivieren."

