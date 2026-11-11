import { defineConfig } from '../../apps/web/node_modules/vite/dist/node/index.js'
import react from '../../apps/web/node_modules/@vitejs/plugin-react/dist/index.js'

// Intentionally bypass the product's backend: importing it can start real work.
export default defineConfig({
  root: new URL('../../apps/web', import.meta.url).pathname,
  plugins: [react()],
  server: {
    host: '127.0.0.1', port: 7521, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:7522', changeOrigin: true } },
  },
})
