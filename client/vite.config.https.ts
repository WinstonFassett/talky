import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

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

// Get daemon URL for proxying (supports remote via VITE_DAEMON_URL).
// Default points at the daemon's HTTPS listener (19443).
const getDaemonUrl = (): string => {
  return process.env.VITE_DAEMON_URL || 'https://localhost:19443';
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
