# Remote Access

This guide explains how to configure Talky for external network access, allowing you to use the voice assistant from other devices on your network or via the internet.

## Overview

Talky supports two modes:
- **Local Access** (default): Only accessible from `localhost`/`127.0.0.1`
- **External Access**: Accessible from other devices using `0.0.0.0` binding

## Quick Start

### 1. Generate SSL Certificates

Required for HTTPS (WebRTC needs secure connections):

```bash
# Generate development SSL certificates
./scripts/generate-certs.sh
```

### 2. Configure External Host

Edit `~/.talky/settings.yaml` (or create if it doesn't exist):

```yaml
network:
  host: "0.0.0.0"  # Enable external access
  frontend_port: 5173
  backend_port: 7860
```

### 3. Start with External Access

```bash
# Start with external binding
talky --host 0.0.0.0

# Or use a profile with external host configured
talky my-profile --host 0.0.0.0
```

### 4. Access from Other Devices

Open your browser on any device on the same network:
- `https://YOUR_COMPUTER_IP:5173` (HTTPS required for WebRTC)
- Accept the self-signed certificate warning

## Configuration Options

### Host Binding

| Host Value | Access Type | Use Case |
|------------|-------------|----------|
| `localhost` | Local only | Development, privacy |
| `127.0.0.1` | Local only | Development, privacy |
| `0.0.0.0` | External | Multi-device access, remote testing |

### Environment Variables

#### Client (Vite) Configuration
```bash
# Configure allowed hosts for external access
export VITE_ALLOWED_HOSTS="localhost,127.0.0.1,192.168.1.100,your-domain.com"
```

#### Server Configuration  
```bash
# Override host binding
export TALKY_HOST="0.0.0.0"

# OpenClaw gateway URL (auto-configured based on host)
export OPENCLAW_GATEWAY_URL="ws://your-hostname:18789"
```

## Network Setup

### Local Network Access

1. **Find your computer's IP:**
   ```bash
   # macOS/Linux
   ifconfig | grep "inet " | grep -v 127.0.0.1
   
   # Or use:
   ipconfig getifaddr en0  # macOS
   hostname -I  # Linux
   ```

2. **Configure firewall** (if needed):
   - Allow ports 5173 (frontend) and 7860 (backend)
   - Allow ports 18789 (OpenClaw gateway, if used)

3. **Access from other devices:**
   ```
   https://192.168.1.100:5173
   ```

### Internet Access

For internet access, you'll need additional setup:

1. **Port Forwarding** (router):
   - Forward port 5173 → your computer:5173
   - Forward port 7860 → your computer:7860

2. **Dynamic DNS** (optional):
   - Use services like No-IP, DuckDNS for static domain

3. **Security Considerations**:
   - Use firewall rules to restrict access
   - Consider VPN for secure remote access
   - Monitor access logs

## SSL/HTTPS Setup

### Why HTTPS is Required

WebRTC (used for voice) requires secure connections:
- `getUserMedia()` only works on HTTPS pages
- Prevents mixed content security errors
- Required for external microphone access

### Certificate Types

#### Development (Self-Signed)
```bash
# Quick generation for development
./scripts/generate-certs.sh
```

#### Production (Let's Encrypt)
```bash
# Example for production setup
certbot certonly --standalone -d your-domain.com
```

### Browser Certificate Warnings

Self-signed certificates will show security warnings:
1. Click "Advanced" 
2. Click "Proceed to website" (unsafe)
3. The connection will work for voice features

## Troubleshooting

### Common Issues

#### "Site not reachable" from other devices
```bash
# Check if binding to external interface
netstat -an | grep :5173
# Should show "0.0.0.0:5173" not "127.0.0.1:5173"

# Check firewall
sudo ufw status  # Linux
# System Preferences > Security > Firewall  # macOS
```

#### "HTTPS required" error
```bash
# Ensure certificates exist
ls -la client/localhost-*.pem server/server-*.pem

# Regenerate if missing
./scripts/generate-certs.sh
```

#### "WebSocket connection failed"
```bash
# Check OpenClaw gateway URL
echo $TALKY_HOST
echo $OPENCLAW_GATEWAY_URL

# Should match your external hostname
```

#### "Microphone not working"
- Ensure you're using HTTPS (not HTTP)
- Check browser microphone permissions
- Try refreshing the page after granting permissions

### Debug Commands

```bash
# Check what's listening on ports
lsof -i :5173  # Frontend
lsof -i :7860  # Backend

# Test network connectivity
curl -k https://localhost:5173  # Test HTTPS
curl -k https://YOUR_IP:5173     # Test external access

# Check certificate validity
openssl x509 -in client/localhost-cert.pem -text -noout
```

## Security Best Practices

### Development Environment
- Use self-signed certificates only for development
- Keep firewall enabled, only open necessary ports
- Don't expose to internet unless needed

### Production Environment  
- Use proper SSL certificates (Let's Encrypt)
- Implement authentication/authorization
- Use VPN for secure remote access
- Monitor and log access attempts
- Regular security updates

### Network Security
```bash
# Example firewall rules (Linux)
sudo ufw allow from 192.168.1.0/24 to any port 5173
sudo ufw allow from 192.168.1.0/24 to any port 7860
sudo ufw deny 5173
sudo ufw deny 7860
```

## Advanced Configuration

### Custom Domains

Configure custom domains in your DNS:

```yaml
# ~/.talky/settings.yaml
network:
  host: "0.0.0.0"
  frontend_port: 5173
  backend_port: 7860
```

```bash
# Environment variables
export VITE_ALLOWED_HOSTS="talky.yourdomain.com,yourdomain.com"
export TALKY_HOST="talky.yourdomain.com"
```

### Reverse Proxy (Nginx)

```nginx
server {
    listen 443 ssl;
    server_name talky.yourdomain.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass https://localhost:5173;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /7860 {
        proxy_pass https://localhost:7860;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Docker Deployment

```dockerfile
# Example Docker setup
EXPOSE 5173 7860
ENV TALKY_HOST=0.0.0.0
ENV VITE_ALLOWED_HOSTS=localhost,127.0.0.1
```

## MCP Server External Access

The MCP server also supports external access:

```bash
# Start MCP server with external binding
talky mcp --host 0.0.0.0

# Access from other machines
# MCP will be available on port 9090
```

## Performance Considerations

- **WiFi vs Ethernet**: Use wired connection for better voice quality
- **Network Latency**: Lower latency = better voice response times  
- **Bandwidth**: Voice uses minimal bandwidth, but video requires more
- **Concurrent Users**: Monitor performance with multiple connections

## Getting Help

If you encounter issues:

1. Check the troubleshooting section above
2. Look at browser console for errors
3. Check server logs for connection issues
4. Create an issue with:
   - Your network setup
   - Error messages
   - Configuration used
   - Steps to reproduce

---

**Note**: External access requires careful security consideration. Only enable when needed and follow security best practices.
