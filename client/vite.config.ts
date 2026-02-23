import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

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
  plugins: [react()],
  server: {
    host: getHost(),
    port: 5173,
    allowedHosts: getAllowedHosts(),
  },
});
