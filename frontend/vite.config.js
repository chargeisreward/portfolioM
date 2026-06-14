import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// VITE_API_URL: production absolute backend URL (e.g. https://portfoliom-backend.zeabur.app)
// Leave empty in dev to use proxy
export default defineConfig({
  plugins: [react()],
  define: {
    'import.meta.env.VITE_API_URL': JSON.stringify(process.env.VITE_API_URL || ''),
  },
  server: { port: 5173, proxy: { '/api': { target: 'http://localhost:8014', changeOrigin: true } } },
  build: { outDir: 'dist', sourcemap: false },
})
