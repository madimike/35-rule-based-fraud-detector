
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  // Read environment variables without exposing them to client-side code.
  const env = loadEnv(mode, '..', '')
  const apiKey = env.FRAUD_API_KEY || process.env.FRAUD_API_KEY

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/health': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
        '/transactions': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (apiKey) {
                proxyReq.setHeader('X-API-Key', apiKey)
              }
            })
          },
        },
        '/assessments': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (apiKey) {
                proxyReq.setHeader('X-API-Key', apiKey)
              }
            })
          },
        },
        '/cases': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (apiKey) {
                proxyReq.setHeader('X-API-Key', apiKey)
              }
            })
          },
        },
      },
    },
  }
})
