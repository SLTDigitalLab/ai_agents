import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { existsSync } from 'node:fs'
import process from 'node:process'

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
    proxy: {
      '/api/simli': {
        // Inside Docker, loopback is the frontend container, not FastAPI.
        target: process.env.SIMLI_PROXY_TARGET || (existsSync('/.dockerenv')
          ? 'http://backend:8000'
          : 'http://127.0.0.1:8000'),
        changeOrigin: true,
      },
    },
    allowedHosts: ['aiagents.sltdigitallab.lk', 'theaisle.raccoon-ai.io'],
    watch: {
      usePolling: true
    }
  }
})
