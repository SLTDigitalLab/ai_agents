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
    host: '0.0.0.0', // Crucial for Docker
    port: 3000,      // Forces Vite to use 3000 instead of 5173
    watch: {
      usePolling: true // Helps with file changes in Docker on Windows
    }
  }
})
