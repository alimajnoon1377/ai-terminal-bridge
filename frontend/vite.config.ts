import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = process.env.ATB_URL ?? 'http://127.0.0.1:8750'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': { target: backend, ws: true } } },
  build: { chunkSizeWarningLimit: 4000 },
  worker: { format: 'es' },
})
