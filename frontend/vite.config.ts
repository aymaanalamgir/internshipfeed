import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backend = process.env.BACKEND_URL ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  base: '/profile/',
  server: {
    strictPort: true,
    proxy: {
      // Preserve the browser's Host/Origin pair for the existing local-write guard.
      '/api': { target: backend, changeOrigin: false },
      '/feed': { target: backend, changeOrigin: false },
      '/web': { target: backend, changeOrigin: false },
    },
  },
});
