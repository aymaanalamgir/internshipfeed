// Build assets for Vercel or local FastAPI hosting and proxy APIs only in standalone Vite development.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backend = process.env.BACKEND_URL ?? 'http://127.0.0.1:8000';
const vercel = process.env.VERCEL === '1';

export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' && !vercel ? '/profile/' : '/',
  server: {
    strictPort: true,
    proxy: vercel ? undefined : {
      // Preserve the browser's Host/Origin pair for the existing local-write guard.
      '/api': { target: backend, changeOrigin: false },
    },
  },
}));
