import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteStaticCopy } from 'vite-plugin-static-copy'

export default defineConfig({
  plugins: [react(), viteStaticCopy({ targets: [
    { src: 'node_modules/pdfjs-dist/cmaps/*', dest: 'pdfjs/cmaps', rename: { stripBase: true } },
    { src: 'node_modules/pdfjs-dist/standard_fonts/*', dest: 'pdfjs/standard_fonts', rename: { stripBase: true } },
  ] })],
  server: {
    host: '127.0.0.1',
    port: 4000,
    proxy: { '/api': { target: process.env.ZHIHE_DEV_API_URL || 'http://127.0.0.1:6000', changeOrigin: true } },
  },
})

