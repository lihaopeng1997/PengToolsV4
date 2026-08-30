import { createApp } from 'vue'
import ChromeApp from './ChromeApp.vue'
import '../styles/base.css'
import { connectBridge } from '../shared/bridge'

createApp(ChromeApp).mount('#app')

// pageReady 契约时序：Vue mounted（mount 同步完成）→ bridge connected → pageReady('chrome')。
// Python readiness state machine 本轮不动，仅供 STEP-4 接入。
connectBridge()
  .then((bridge) => {
    bridge.pageReady('chrome')
  })
  .catch((err: unknown) => {
    // 普通浏览器无 window.qt：明确记录但不抛未处理异常、不白屏
    console.warn('[chrome]', err instanceof Error ? err.message : String(err))
  })
