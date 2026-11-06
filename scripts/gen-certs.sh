#!/usr/bin/env bash
# Creates the certificates the stack uses for TLS between its parts, in ./certs (git-ignored):
#
#   ca.crt / ca.key      an internal certificate authority (created only if you do not bring your own)
#   edge.crt / edge.key  the HTTPS front door (nginx), valid for localhost and $EDGE_HOSTNAMES
#   core.crt / core.key  the file service
#   redis.crt / redis.key  Redis
#
# Bring your own internal CA: put ca.crt (the CA certificate your clients and containers trust) and the
# three certificate/key pairs in ./certs before starting. Files that already exist are never overwritten.
# The names the services use inside the compose network are core and redis; each certificate must be valid
# for them (subjectAltName), and edge for the host name users type.
#
# These are development-grade: keys are readable by the containers (mode 644). In production use your
# own PKI and mount the keys as secrets.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }

DIR="${CERT_DIR:-certs}"
EDGE_HOSTNAMES="${EDGE_HOSTNAMES:-}"   # extra names for the front door, comma separated
mkdir -p "$DIR"

if [[ ! -f "$DIR/ca.crt" ]]; then
  [[ -f "$DIR/ca.key" ]] && { echo "$DIR/ca.key exists without ca.crt; remove it or supply ca.crt" >&2; exit 1; }
  openssl genrsa -out "$DIR/ca.key" 4096 2>/dev/null
  openssl req -x509 -new -key "$DIR/ca.key" -sha256 -days 3650 -subj "/O=coneshare-alt/CN=coneshare-alt internal CA" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -out "$DIR/ca.crt"
  echo "Created internal CA: $DIR/ca.crt (import it into your browser to remove the certificate warning)"
fi

issue() {  # issue NAME SAN-LIST
  local name="$1" sans="$2"
  [[ -f "$DIR/$name.crt" && -f "$DIR/$name.key" ]] && return 0
  [[ -f "$DIR/ca.key" ]] || { echo "No $DIR/$name.crt and no CA key to create one; supply the certificate and key" >&2; exit 1; }
  openssl genrsa -out "$DIR/$name.key" 2048 2>/dev/null
  openssl req -new -key "$DIR/$name.key" -subj "/O=coneshare-alt/CN=$name" -out "$DIR/$name.csr"
  printf 'basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=%s\n' "$sans" > "$DIR/$name.ext"
  openssl x509 -req -in "$DIR/$name.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" -CAcreateserial -days 825 -sha256 \
    -extfile "$DIR/$name.ext" -out "$DIR/$name.crt" 2>/dev/null
  rm -f "$DIR/$name.csr" "$DIR/$name.ext"
  echo "Issued $DIR/$name.crt"
}

edge_sans="DNS:localhost,IP:127.0.0.1"
IFS=',' read -ra extra <<< "$EDGE_HOSTNAMES"
for h in "${extra[@]}"; do [[ -n "$h" ]] && edge_sans="$edge_sans,DNS:$h"; done
issue edge  "$edge_sans"
issue core  "DNS:core,DNS:localhost,IP:127.0.0.1"
issue redis "DNS:redis,DNS:localhost,IP:127.0.0.1"

chmod 644 "$DIR"/*.crt "$DIR"/*.key 2>/dev/null || true
chmod 600 "$DIR/ca.key" 2>/dev/null || true
