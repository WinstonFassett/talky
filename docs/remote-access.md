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
# Override host binding (optional)
talky --host 0.0.0.0

# Or configure in ~/.talky/settings.yaml
network:
  host: "0.0.0.0"
```

## Network Setup

### Local Network Access

1. **Find your IP:**
   ```bash
   ipconfig getifaddr en0  # macOS
   hostname -I  # Linux
   ```

2. **Access from other devices:**
   ```
   https://YOUR_IP:5173
   ```

3. **Firewall:** Allow ports 5173, 7860 if needed

### Internet Access

1. **Port Forwarding:** Forward 5173, 7860 to your computer
2. **Security:** Use VPN or firewall rules for protection

## SSL/HTTPS Setup

WebRTC requires HTTPS for microphone access.

### Generate Certificates
```bash
./scripts/generate-certs.sh
```

### Browser Warnings
Self-signed certs show security warnings - click "Advanced" → "Proceed" to continue.

## Troubleshooting

### Common Issues

**"Site not reachable"**
```bash
netstat -an | grep :5173  # Should show "0.0.0.0:5173"
```

**"HTTPS required" error**
```bash
./scripts/generate-certs.sh  # Missing certificates
```

**"Microphone not working"**
- Use HTTPS (not HTTP)
- Grant browser microphone permissions
- Refresh page after permissions

### Debug Commands
```bash
lsof -i :5173  # Check what's listening
curl -k https://localhost:5173  # Test HTTPS
```

## Security

- **Development**: Self-signed certs are fine
- **Production**: Use proper SSL certificates
- **Network**: Keep firewall enabled, only open needed ports

## Resources

- [Vite Server Config](https://vitejs.dev/config/server-options.html)
- [WebRTC Security](https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API/Security)
- [Pipecat Docs](https://pipecat.ai/docs)
- [Pipecat Discord](https://discord.gg/pipecat)

---

**Note**: External access requires careful security consideration. Only enable when needed and follow security best practices.
