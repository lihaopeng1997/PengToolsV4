import { createApp, nextTick, reactive } from 'vue'
import DashboardApp from './DashboardApp.vue'
import { applyThemePayload, connectBridge, type BridgeApi } from '../shared/bridge'
import type { DashboardSummary } from './types'

const state = reactive<{
  summary: DashboardSummary | null
  bridge: BridgeApi | null
  error: string | null
}>({
  summary: null,
  bridge: null,
  error: null,
})

// 1. Vue mount：先挂载骨架，保证 DOM 容器就绪
const app = createApp(DashboardApp, { state })
app.mount('#app')

async function bootstrapDashboard(): Promise<void> {
  // 时序：Vue mount → connectBridge → dashboardSummary → JSON.parse → state 应用成功 → DOM/render 就绪 → pageReady('dashboard')
  const bridge = await connectBridge()
  state.bridge = bridge
  const rawSummary = await bridge.dashboardSummary()
  const themeRaw = await bridge.themePayload()
  applyThemePayload(themeRaw)
  bridge.onThemeChanged(applyThemePayload)
  const parsed = JSON.parse(rawSummary) as DashboardSummary
  state.summary = parsed
  await nextTick()
  bridge.pageReady('dashboard')
}

bootstrapDashboard().catch((err: unknown) => {
  const msg = err instanceof Error ? `${err.name}: ${err.message}` : String(err)
  console.error('dashboard init failed:', msg)
  state.error = msg
  // 失败时绝不调用 pageReady 槽通知健康状态，让 WebHealthTracker / timeout 接管回退
})
