import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 4000,
    proxy: { '/api': { target: process.env.ZHIHE_DEV_API_URL || 'http://127.0.0.1:6000', changeOrigin: true } },
  },
})

