import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Development: run the backend with a fixed port and token
//   python -m pyGCxGC.app --port 8765 --token dev
// and open http://localhost:5173/?token=dev. The proxy rewrites the Host
// header to 127.0.0.1:<port>, which the backend's Host check requires.
const backend = `http://127.0.0.1:${process.env.PYGCXGC_PORT ?? '8765'}`

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    // Served by pyGCxGC.app.server and shipped in the wheel (not committed).
    outDir: '../src/pyGCxGC/app/static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
  },
  server: {
    proxy: {
      '/api': { target: backend, changeOrigin: true, ws: true },
    },
  },
})
