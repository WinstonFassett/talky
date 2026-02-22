#!/bin/bash
# Generate self-signed SSL certificates for development HTTPS

set -e

echo "🔐 Generating SSL certificates for development..."

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

echo "✅ SSL certificates generated successfully!"
echo "📝 Files created:"
echo "   - client/localhost-key.pem"
echo "   - client/localhost-cert.pem" 
echo "   - server/server-key.pem"
echo "   - server/server-cert.pem"
echo ""
echo "🚀 You can now use HTTPS for external access:"
echo "   talky --host 0.0.0.0"
