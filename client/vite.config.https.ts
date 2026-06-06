import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';
import os from 'os';
import { readFileSync } from 'fs';

// Get allowed hosts from environment or use defaults
const getAllowedHosts = (): string[] => {
  const envHosts = process.env.VITE_ALLOWED_HOSTS;
  if (envHosts) {
    return envHosts.split(',').map((h: string) => h.trim());
  }
  // Default hosts for development
  return ['localhost', '127.0.0.1'];
};

// Get host from environment or use default
const getHost = (): string => {
  return process.env.VITE_HOST || 'localhost';
};

// Get daemon URL for proxying (HTTPS variant — points at the primary
// listener which serves TLS when certs are configured).
// Precedence: VITE_DAEMON_URL env → ~/.talky/run/talky-daemon.port → error.
const getDaemonUrl = (): string => {
  if (process.env.VITE_DAEMON_URL) return process.env.VITE_DAEMON_URL;
  const runFile = path.join(os.homedir(), '.talky', 'run', 'talky-daemon.port');
  try {
    const port = parseInt(readFileSync(runFile, 'utf8').trim(), 10);
    if (port) return `https://localhost:${port}`;
  } catch {
    // fall through
  }
  throw new Error(
    'No daemon URL. Start `talky daemon` first, or set VITE_DAEMON_URL.',
  );
};

// HTTPS config for external access
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    host: getHost(),
    port: 5173,
    allowedHosts: getAllowedHosts(),
    https: {
      key: './localhost-key.pem',
      cert: './localhost-cert.pem',
    },
    proxy: {
      // secure:false accepts the daemon's self-signed cert
      '/api': { target: getDaemonUrl(), ws: true, changeOrigin: true, secure: false },
      '/start': { target: getDaemonUrl(), changeOrigin: true, secure: false },
      '/status': { target: getDaemonUrl(), changeOrigin: true, secure: false },
      '/sessions': { target: getDaemonUrl(), ws: true, changeOrigin: true, secure: false },
      '/ws': { target: getDaemonUrl(), ws: true, changeOrigin: true, secure: false },
    },
  },
});
