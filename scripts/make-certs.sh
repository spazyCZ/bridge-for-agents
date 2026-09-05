#!/usr/bin/env bash
# Generates a shared secret and a TLS cert for the bridge.
#
#   ./scripts/make-certs.sh bridge.lan 10.0.0.5
#
# First argument is the hostname Claude Code will use in the hook URL; any
# further arguments are extra hostnames or IPs to put in the SAN. The name in
# the hook URL MUST appear here or the TLS handshake fails.
set -euo pipefail

OUT="${BRIDGE_PKI_DIR:-./pki}"
HOST="${1:?usage: make-certs.sh <hostname> [more-names-or-ips...]}"
shift
DAYS="${BRIDGE_CERT_DAYS:-825}"

mkdir -p "$OUT"
cd "$OUT"
umask 077

# --- SAN list ---------------------------------------------------------------
san="DNS:$HOST"
for n in "$@"; do
  if [[ "$n" =~ ^[0-9]+(\.[0-9]+){3}$ || "$n" == *:* ]]; then san="$san,IP:$n"; else san="$san,DNS:$n"; fi
done

# --- local CA (create once, reuse afterwards) -------------------------------
if [[ ! -f ca.key ]]; then
  openssl req -x509 -newkey rsa:4096 -nodes -sha256 -days 3650 \
    -keyout ca.key -out ca.crt -subj "/CN=claude-bridge local CA"
  echo "created CA: $OUT/ca.crt"
else
  echo "reusing existing CA: $OUT/ca.crt"
fi

# --- server cert ------------------------------------------------------------
openssl req -newkey rsa:2048 -nodes -sha256 \
  -keyout server.key -out server.csr -subj "/CN=$HOST"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days "$DAYS" -sha256 -out server.crt \
  -extfile <(printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n' "$san")
rm -f server.csr

chmod 600 ca.key server.key
chmod 644 ca.crt server.crt

# --- shared secret ----------------------------------------------------------
SECRET="$(openssl rand -hex 32)"

cat <<EOF

Certificate for $HOST ($san), valid $DAYS days.
  $OUT/server.crt  $OUT/server.key   -> bridge host
  $OUT/ca.crt                        -> every Claude Code host

--- bridge host -------------------------------------------------------------
export BRIDGE_BIND=0.0.0.0
export BRIDGE_TOKEN=$SECRET
export BRIDGE_TLS_CERT=$PWD/server.crt
export BRIDGE_TLS_KEY=$PWD/server.key

--- each Claude Code host ---------------------------------------------------
export BRIDGE_TOKEN=$SECRET
export NODE_EXTRA_CA_CERTS=/path/to/ca.crt     # so the hook trusts this CA

Hook URL: https://$HOST:8765/hook
Keep the secret out of settings.json and out of git.
EOF
