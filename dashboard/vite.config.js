import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/portfolio': 'http://localhost:8003',
      '/agents/performance': 'http://localhost:8003',
      '/strategy/select': 'http://localhost:8003',
      '/cycles': 'http://localhost:8003',
      '/candles': 'http://localhost:8003',
      '/pairs': 'http://localhost:8003',
      '/analyze': 'http://localhost:8003',
      '/options': 'http://localhost:8003',
      '/validate': 'http://localhost:8003',
      '/events': 'http://localhost:8003',
      '/health': 'http://localhost:8003',
      '/system': 'http://localhost:8003',
      '/ws': {
        target: 'ws://localhost:8003',
        ws: true,
      }
    }
  }
})
