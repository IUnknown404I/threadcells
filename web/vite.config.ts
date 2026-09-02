/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  define: {
    __THREADCELLS_REVISION__: JSON.stringify(process.env.THREADCELLS_SOURCE_REVISION || 'source'),
    __THREADCELLS_VERSION__: JSON.stringify(process.env.npm_package_version || '0.3.4-alpha'),
  },
  build: {
    outDir: '../src/cli_agent_orchestrator/web_ui',
    emptyOutDir: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
  server: {
    host: 'localhost',
    port: 5173,
    proxy: {
      '/sessions': { target: process.env.CAO_API_URL || 'http://localhost:9889', changeOrigin: true },
      '/terminals': { target: process.env.CAO_API_URL || 'http://localhost:9889', changeOrigin: true, ws: true },
      '/health': { target: process.env.CAO_API_URL || 'http://localhost:9889', changeOrigin: true },
      '/agents': { target: process.env.CAO_API_URL || 'http://localhost:9889', changeOrigin: true },
      '/settings': { target: process.env.CAO_API_URL || 'http://localhost:9889', changeOrigin: true },
      '/flows': { target: process.env.CAO_API_URL || 'http://localhost:9889', changeOrigin: true },
      '/projects': { target: process.env.CAO_API_URL || 'http://localhost:9889', changeOrigin: true },
    },
  },
})
