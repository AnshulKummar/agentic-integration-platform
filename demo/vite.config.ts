import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Relative base so the static build works on GitHub Pages under /<repo>/.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          if (!id.includes('node_modules')) return undefined
          if (/node_modules[\/](d3|d3-[a-z-]+|internmap|delaunator|robust-predicates)[\/]/.test(id)) return 'd3'
          if (/node_modules[\/](framer-motion|motion-dom|motion-utils)[\/]/.test(id)) return 'motion'
          return 'vendor'
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
