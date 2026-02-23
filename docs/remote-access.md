# Remote Access

Use Talky from other devices on your network.

## Quick Start

1. **Generate SSL certificates** (required for WebRTC):
   ```bash
   ./scripts/generate-certs.sh
   ```

2. **Configure external host** in `~/.talky/settings.yaml`:
   ```yaml
   network:
     host: "0.0.0.0"
   ```

3. **Start with external access**:
   ```bash
   talky --host 0.0.0.0
   ```

4. **Access from other devices**:
   ```
   https://YOUR_IP:5173
   ```

## Configuration

### Host Binding
- `localhost` - Local access only
- `0.0.0.0` - External access (other devices)

### Configuration Options

#### Network Settings (`~/.talky/settings.yaml`)
```yaml
network:
  host: "0.0.0.0"                    # Host binding
  external_host: ""                  # External hostname for browser URLs (configure this!)
  frontend_port: 5173               # Client port
  backend_port: 7860                # Server port
```

#### Environment Variables
```bash
# Client configuration
export VITE_HOST="0.0.0.0"              # Host binding (default: localhost)
export VITE_ALLOWED_HOSTS="localhost,127.0.0.1,YOUR_IP"  # Allowed hosts
export VITE_BACKEND_PORT="7860"          # Backend port (default: 7860)
```

## Troubleshooting

**"Site not reachable"**
```bash
netstat -an | grep :5173  # Should show "0.0.0.0:5173"
```

**"HTTPS required"**
```bash
./scripts/generate-certs.sh  # Missing certificates
```

**"Microphone not working"**
- Use HTTPS (not HTTP)
- Grant browser microphone permissions

## Resources

- [Vite Server Config](https://vitejs.dev/config/server-options.html)
- [Pipecat Docs](https://pipecat.ai/docs)
