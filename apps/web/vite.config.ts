import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 局域网访问（2026-10-10）：
//   host 用环境变量 BIDOS_LAN_HOST 控制，默认 0.0.0.0 = 允许同一局域网内其他电脑访问。
//   改成 127.0.0.1 就退回「只允许本机」——改这一处即可开关，不需要改代码。
//   同事用 http://<本机局域网IP>:5173 打开，例如 http://192.168.1.50:5173
const host = process.env.BIDOS_LAN_HOST || '0.0.0.0'
const port = Number(process.env.BIDOS_WEB_PORT || 5173)

export default defineConfig({
  plugins: [react()],
  server: {
    host,
    port,
    strictPort: true,
    // 质检修复：dev 模式把 /api 代理到本地 FastAPI。
    // 此前无代理，.env.local 一丢 dev 就全 404——页面拿不到真实数据。
    // 前端一律用相对路径 /api，请求由本机 vite 转发，天然不涉及跨域，
    // 所以同事在局域网另一台电脑上打开也能正常取数。
    proxy: {
      '/api': {
        // 代理目标始终是「运行vite 的这台机器自己」，不能用局域网 IP：
        // 那样在容器/多网卡场景下会绕路。127.0.0.1 是本机回环，语义最准确。
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})