import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const devApiTarget = process.env.VITE_DEV_API_TARGET || 'http://127.0.0.1:18080'
const hmrClientPort = Number(process.env.VITE_HMR_CLIENT_PORT || 4173)

export default defineConfig({
  plugins: [vue()],
  base: './',
  server: {
    port: 4173,
    strictPort: true,
    hmr: {
      clientPort: hmrClientPort,
    },
    proxy: Object.fromEntries(
      ['/portal-api', '/platform-api', '/auth', '/health'].map((path) => [path, {
        target: devApiTarget,
        changeOrigin: false,
      }]),
    ),
  },
})
