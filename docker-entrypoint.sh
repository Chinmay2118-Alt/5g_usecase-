#!/bin/sh
set -e

# Ensure certs and logs directories exist
mkdir -p /app/certs /app/logs

# Auto-generate self-signed TLS certificates if missing and enabled
if [ ! -f /app/certs/cert.pem ] || [ ! -f /app/certs/key.pem ]; then
    echo "[5G-ENTRYPOINT] Generating TLS certificates for 5G MEC endpoints..."
    CERT_ARGS=""
    if [ -n "$SERVER_IP" ]; then
        CERT_ARGS="$CERT_ARGS --ip $SERVER_IP"
    fi
    if [ -n "$EXTRA_IPS" ]; then
        for ip in $(echo "$EXTRA_IPS" | tr ',' ' '); do
            CERT_ARGS="$CERT_ARGS --ip $ip"
        done
    fi
    python -m server.certs $CERT_ARGS || true
fi

echo "[5G-ENTRYPOINT] Starting 5G Remote Hands Server..."
exec "$@"

