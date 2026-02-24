#!/bin/bash
# Generate self-signed SSL certificates for development HTTPS

set -e

echo "🔐 Generating SSL certificates for development..."

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

# Client certificates
echo "📱 Generating client certificates..."
cd client
openssl req -x509 -newkey rsa:2048 \
  -keyout localhost-key.pem \
  -out localhost-cert.pem \
  -days 365 \
  -nodes \
  -subj "/CN=localhost"

# Server certificates  
echo "🖥️  Generating server certificates..."
cd ../server
openssl req -x509 -newkey rsa:2048 \
  -keyout server-key.pem \
  -out server-cert.pem \
  -days 365 \
  -nodes \
  -subj "/CN=localhost"

cd ..

# Validate generated certificates
validate_cert "client/localhost-cert.pem" "client/localhost-key.pem" "client certificates"
validate_cert "server/server-cert.pem" "server/server-key.pem" "server certificates"

echo "✅ SSL certificates generated and validated successfully!"
echo "📝 Files created:"
echo "   - client/localhost-key.pem"
echo "   - client/localhost-cert.pem" 
echo "   - server/server-key.pem"
echo "   - server/server-cert.pem"
echo ""
echo "🚀 You can now use HTTPS for external access:"
echo "   talky --host 0.0.0.0"
