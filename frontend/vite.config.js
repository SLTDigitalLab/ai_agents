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
    allowedHosts: ['aiagents.sltdigitallab.lk'],
    watch: {
      usePolling: true
    }
  }
})
