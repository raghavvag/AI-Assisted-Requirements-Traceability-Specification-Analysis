import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // One three instance only - mixed copies break instanceof/prototype checks
    dedupe: ['three'],
    // shadcn/ui convention — import { cn } from '@/lib/utils'
    alias: { '@': path.resolve(process.cwd(), 'src') },
  },
  server: {
    // Bind to localhost only. ALWAYS open the app at http://localhost:5173 —
    // Google OAuth's origin check is an exact string match against the
    // "Authorised JavaScript origins" list in the Google Cloud Console.
    host: 'localhost',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Keep the heavy 3D/chart libs out of the main bundle
        manualChunks: {
          three: ['three', '@react-three/fiber', '@react-three/drei', '@react-three/postprocessing'],
          charts: ['plotly.js-dist-min', 'react-plotly.js'],
        },
      },
    },
    chunkSizeWarningLimit: 1600,
  },
})
