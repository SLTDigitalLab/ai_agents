import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  envDir: '..',
  plugins: [react()],
  css: {
    postcss: './postcss.config.js',
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    allowedHosts: ['aiagents.sltdigitallab.lk', 'theaisle.raccoon-ai.io'],
    // Keep local API calls same-origin in the browser. This avoids browser
    // CORS/network failures while forwarding the request (including the
    // Azure Authorization header) to FastAPI on port 8000.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
    watch: {
      usePolling: true
    }
  }
})
