#!/bin/bash
# Generate self-signed SSL certificates for development HTTPS
#
# DEPRECATED (ticket 1edf): Use `talky certs` instead — same logic, no repo
# checkout needed, no openssl shell-out. This script is kept for one release
# because it also handles the repo-only client/localhost-*.pem path. It will
# be removed once Vite dev cert reuse stabilizes.

set -e

echo "⚠️  scripts/generate-certs.sh is deprecated — prefer 'talky certs'." >&2
echo "🔐 Generating SSL certificates for development..."

# Function to get external hostname from settings
get_external_hostname() {
    local settings_file="$HOME/.talky/settings.yaml"
    if [[ -f "$settings_file" ]]; then
        # Extract external_host from settings.yaml - get the value after the colon
        grep "^[[:space:]]*external_host:" "$settings_file" | sed 's/.*external_host:[[:space:]]*//' | sed 's/[[:space:]]*#.*//' | sed 's/"//g' | tr -d ' '
    fi
}

# Function to validate certificate file
validate_cert() {
    local cert_file=$1
    local key_file=$2
    local description=$3
    
    echo "🔍 Validating $description..."
    
    # Check if files exist
    if [[ ! -f "$cert_file" ]]; then
        echo "❌ Certificate file not found: $cert_file"
        exit 1
    fi
    
    if [[ ! -f "$key_file" ]]; then
        echo "❌ Private key file not found: $key_file"
        exit 1
    fi
    
    # Validate certificate format
    if ! openssl x509 -in "$cert_file" -noout -text >/dev/null 2>&1; then
        echo "❌ Invalid certificate format: $cert_file"
        exit 1
    fi
    
    # Validate private key format
    if ! openssl rsa -in "$key_file" -check -noout >/dev/null 2>&1; then
        echo "❌ Invalid private key format: $key_file"
        exit 1
    fi
    
    # Check if certificate and key match
    cert_modulus=$(openssl x509 -noout -modulus -in "$cert_file" 2>/dev/null | openssl md5)
    key_modulus=$(openssl rsa -noout -modulus -in "$key_file" 2>/dev/null | openssl md5)
    
    if [[ "$cert_modulus" != "$key_modulus" ]]; then
        echo "❌ Certificate and private key do not match: $description"
        exit 1
    fi
    
    echo "✅ $description validated successfully"
}

# Determine hostname for certificates
EXTERNAL_HOST=$(get_external_hostname)
HOSTNAME=${1:-$EXTERNAL_HOST}
HOSTNAME=${HOSTNAME:-"localhost"}

echo "🌐 Using hostname: $HOSTNAME"

# Server certificates → ~/.talky/ssl/ (user-specific, not project artifacts)
# The daemon serves HTTPS from these regardless of how talky was installed.
SSL_DIR="$HOME/.talky/ssl"
mkdir -p "$SSL_DIR"
echo "🖥️  Generating server certificates in $SSL_DIR..."
openssl req -x509 -newkey rsa:2048 \
  -keyout "$SSL_DIR/server-key.pem" \
  -out "$SSL_DIR/server-cert.pem" \
  -days 365 \
  -nodes \
  -subj "/CN=$HOSTNAME"
validate_cert "$SSL_DIR/server-cert.pem" "$SSL_DIR/server-key.pem" "server certificates"

# Client certificates are only useful when running the Vite dev server from
# the repo. Wheel installs don't have a `client/` directory and don't need
# these — the SPA is bundled in the wheel and served by the daemon over
# HTTPS using the server cert above.
if [[ -d "client" ]]; then
    echo "📱 Repo checkout detected — generating client certificates for Vite dev server..."
    (cd client && openssl req -x509 -newkey rsa:2048 \
      -keyout localhost-key.pem \
      -out localhost-cert.pem \
      -days 365 \
      -nodes \
      -subj "/CN=$HOSTNAME")
    validate_cert "client/localhost-cert.pem" "client/localhost-key.pem" "client certificates"
else
    echo "ℹ️  No client/ directory — skipping Vite dev-server certs (wheel install)."
fi

echo "✅ SSL certificates generated and validated successfully!"
echo "📝 Files created:"
echo "   - $SSL_DIR/server-key.pem"
echo "   - $SSL_DIR/server-cert.pem"
if [[ -d "client" ]]; then
    echo "   - client/localhost-key.pem"
    echo "   - client/localhost-cert.pem"
fi
echo ""
echo "🚀 Point ~/.talky/settings.yaml network.https at the new paths, then:"
echo "   talky kill && talky daemon"
