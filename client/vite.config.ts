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
// Default points at the daemon's plain-HTTP loopback listener (19080).
const getDaemonUrl = (): string => {
  return process.env.VITE_DAEMON_URL || 'http://localhost:19080';
};

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    host: getHost(),
    port: 5173,
    allowedHosts: getAllowedHosts(),
    proxy: {
      '/api': { target: getDaemonUrl(), ws: true },
      '/start': getDaemonUrl(),
      '/status': getDaemonUrl(),
      '/sessions': { target: getDaemonUrl(), ws: true },
      '/ws': { target: getDaemonUrl(), ws: true },
    },
  },
});
