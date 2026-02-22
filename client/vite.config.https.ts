import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// HTTPS config for external access
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: ['macbook-pro.tailc3138.ts.net', 'localhost', '127.0.0.1'],
    https: {
      key: './localhost-key.pem',
      cert: './localhost-cert.pem',
    },
  },
});
