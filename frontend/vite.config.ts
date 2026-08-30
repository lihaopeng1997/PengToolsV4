import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

// PengToolsHub 最终由 QWebEngineView 通过 file:// 加载本地构建产物：
// base 必须为 './'，否则 /assets/... 绝对路径在 file:// 下失效。
export default defineConfig({
  base: './',
  plugins: [vue()],
  build: {
    rollupOptions: {
      // 双入口（chrome / dashboard），不是 SPA；不用 Vue Router 模拟多页。
      input: {
        chrome: fileURLToPath(new URL('./chrome.html', import.meta.url)),
        dashboard: fileURLToPath(new URL('./dashboard.html', import.meta.url)),
      },
    },
  },
})
