import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// VITE_API_URL: production absolute backend URL (e.g. https://portfoliom-backend.zeabur.app)
// Leave empty in dev to use proxy
export default defineConfig(({ mode }) => {
  // 让 Vite 自动加载 .env.<mode> 文件,把 VITE_ 前缀变量注入到 import.meta.env
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  return {
    plugins: [react()],
    server: { port: 5173, proxy: { '/api': { target: 'http://localhost:8001', changeOrigin: true } } },
    build: { outDir: 'dist', sourcemap: false },
  }
})
