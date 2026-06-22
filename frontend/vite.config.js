import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// VITE_API_URL: production absolute backend URL (e.g. https://portfoliom-backend.zeabur.app)
// Leave empty in dev to use proxy
// VITE_BASE: production base path (e.g. '/portfoliom/' for cloud deployment)
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  return {
    plugins: [react()],
    base: env.VITE_BASE || '/',
    server: { port: 5173, proxy: { '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true } } },
    build: { outDir: 'dist', sourcemap: false },
  }
})
