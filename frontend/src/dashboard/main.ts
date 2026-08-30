import { createApp } from 'vue'
import DashboardApp from './DashboardApp.vue'
import '../styles/base.css'
import { connectBridge } from '../shared/bridge'

createApp(DashboardApp).mount('#app')

// pageReady 契约时序：Vue mounted（mount 同步完成）→ bridge connected → pageReady('dashboard')。
connectBridge()
  .then((bridge) => {
    bridge.pageReady('dashboard')
  })
  .catch((err: unknown) => {
    console.warn('[dashboard]', err instanceof Error ? err.message : String(err))
  })
