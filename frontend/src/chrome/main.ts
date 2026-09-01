import { createApp, nextTick, reactive } from 'vue'
import ChromeApp from './ChromeApp.vue'
import { applyThemePayload, connectBridge } from '../shared/bridge'
import { parseNavModel } from './nav'

function mountFallback(error: unknown): void {
  const app = createApp(ChromeApp, {
    model: null,
    active: { current: 0 },
    bridge: null,
    bridgeError: error instanceof Error ? error.message : String(error),
  })
  app.mount('#app')
}

async function bootstrapChrome(): Promise<void> {
  // 产品时序（STEP-4）：connect → navModel → 最小结构校验 → Vue mount →
  // activeChanged connect → 首屏 nextTick → 才允许 pageReady('chrome')。
  // 任何一步失败都不得伪造健康状态（Python readiness timeout / fallback 接管）。
  const bridge = await connectBridge()
  const [navRaw, themeRaw] = await Promise.all([
    bridge.navModel(),
    bridge.themePayload().catch(() => '{}'),
  ])
  applyThemePayload(themeRaw)
  bridge.onThemeChanged(applyThemePayload)
  const model = parseNavModel(navRaw)
  const active = reactive({ current: model.current ?? 0 })
  const app = createApp(ChromeApp, { model, active, bridge })
  app.mount('#app')
  bridge.onActiveChanged((index: number) => {
    active.current = index
  })
  await nextTick()   // 首屏 DOM 已渲染完成
  bridge.pageReady('chrome')
}

bootstrapChrome().catch((err: unknown) => {
  console.error(
    'chrome init failed:',
    err instanceof Error ? `${err.name}: ${err.message}` : String(err),
  )
  mountFallback(err)
})
