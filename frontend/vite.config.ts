import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API has no /api prefix, so the dev proxy strips it: /api/auth/login -> http://localhost:8000/auth/login
export default defineConfig({
  plugins: [react()],
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'] },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.API_TARGET ?? 'http://localhost:8000', // set API_TARGET to run the API on another port
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        // an agent call can take up to a minute on CPU
        timeout: 120_000,
        proxyTimeout: 120_000,
      },
    },
  },
})
