import { createApp, nextTick, reactive } from 'vue'
import DashboardApp from './DashboardApp.vue'
import { applyThemePayload, connectBridge, type BridgeApi } from '../shared/bridge'
import type { DashboardSummary } from './types'

const state = reactive<{
  summary: DashboardSummary | null
  bridge: BridgeApi | null
  error: string | null
  retry?: () => Promise<void>
}>({
  summary: null,
  bridge: null,
  error: null,
})

// 1. Vue mount：先挂载骨架，保证 DOM 容器就绪
const app = createApp(DashboardApp, { state })
app.mount('#app')

async function loadData(bridge: BridgeApi): Promise<void> {
  const [rawSummary, rawNav, themeRaw] = await Promise.all([
    bridge.dashboardSummary(),
    typeof bridge.navModel === 'function' ? bridge.navModel() : Promise.resolve(''),
    bridge.themePayload(),
  ])
  applyThemePayload(themeRaw)
  bridge.onThemeChanged(applyThemePayload)
  const parsed = JSON.parse(rawSummary) as DashboardSummary
  if (rawNav) {
    try {
      const navData = JSON.parse(rawNav)
      if (navData && Array.isArray(navData.groups)) {
        const navItemMap = new Map<number, { i: number; zh?: string; tip?: string; icon?: string }>()
        for (const g of navData.groups) {
          if (Array.isArray(g.items)) {
            for (const it of g.items) {
              navItemMap.set(it.i, it)
              if (Array.isArray(it.children)) {
                for (const ch of it.children) {
                  navItemMap.set(ch.i, ch)
                }
              }
            }
          }
        }
        const quickIndices = [18, 16, 12, 13, 5, 11]
        const derivedTools = quickIndices.map(idx => {
          const item = navItemMap.get(idx)
          return {
            i: idx,
            zh: idx === 18 ? '数据中心' : (idx === 16 ? '模型对话' : (item?.zh || `工具 ${idx}`)),
            ds: item?.tip || '',
            icon: item?.icon || 'database',
          }
        })
        if (!parsed.tools || parsed.tools.length === 0) {
          parsed.tools = derivedTools
        }
      }
    } catch {
      // ignore
    }
  }
  state.summary = parsed
  if (typeof bridge.onSummaryChanged === 'function') {
    bridge.onSummaryChanged(async (newSummaryRaw: string) => {
      try {
        state.summary = JSON.parse(newSummaryRaw) as DashboardSummary
        await nextTick()
      } catch (err) {
        console.error('dashboard onSummaryChanged parse failed:', err)
      }
    })
  }
  await nextTick()
  bridge.pageReady('dashboard')
}

async function bootstrapDashboard(): Promise<void> {
  // 时序：Vue mount → connectBridge → dashboardSummary → JSON.parse → state 应用成功 → DOM/render 就绪 → pageReady('dashboard')
  const bridge = await connectBridge()
  state.bridge = bridge
  await loadData(bridge)
}

async function retryLoad(): Promise<void> {
  state.error = null
  try {
    if (!state.bridge) {
      await bootstrapDashboard()
      return
    }
    await loadData(state.bridge)
  } catch (err: unknown) {
    const msg = err instanceof Error ? `${err.name}: ${err.message}` : String(err)
    console.error('dashboard retry failed:', msg)
    state.error = msg
  }
}

state.retry = retryLoad

bootstrapDashboard().catch((err: unknown) => {
  const msg = err instanceof Error ? `${err.name}: ${err.message}` : String(err)
  console.error('dashboard init failed:', msg)
  state.error = msg
  // 失败时绝不调用 pageReady 槽通知健康状态，让 WebHealthTracker / timeout 接管回退
})
