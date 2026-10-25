import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  root: '.',
  base: './',
  build: {
    outDir: 'dist-renderer',
    emptyOutDir: true,
  },
  server: {
    // 显式绑 IPv6 回环（2026-10-24）：与 electron loadURL / package.json wait-on
    // 同走 [::1]——127.0.0.1:5173 被本机代理/TUN 类软件黑洞化，Chromium v4 优先
    // 要 SYN 超时轮询 ~3 分钟（窗口白板根因）。详见 electron/main.js 注释。
    host: '::1',
    port: 5173,
    strictPort: true,
    // 【2026-10-24 实验性移除】warmup 预转换实测反噬：server 启动即全量转换
    // 数千模块，首个 GET /index.html 被阻塞 ~3 分钟（外部 curl 同挂，HTTP 管道
    // 全局无响应），窗口白板期反而拉长。renderer 首载 ~3m 的主因仍在排查
    // （deps 扫描/Defender 逐文件扫描等），查明后另行优化。仅影响 dev。
    // warmup: {
    //   clientFiles: ['./index.html', './src/main.tsx'],
    // },
  },
  // vitest（0.4lifecycle-design.md §9 P3，v1.7 基建）：jsdom 环境渲染 hook/组件
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/setup.ts'],
  },
})
