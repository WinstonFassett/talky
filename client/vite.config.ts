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

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    host: getHost(),
    port: 5173,
    allowedHosts: getAllowedHosts(),
    proxy: {
      '/api': { target: 'http://localhost:9090', ws: true },
      '/start': 'http://localhost:9090',
      '/status': 'http://localhost:9090',
      '/sessions': { target: 'http://localhost:9090', ws: true },
      '/ws': { target: 'http://localhost:9090', ws: true },
    },
  },
});
