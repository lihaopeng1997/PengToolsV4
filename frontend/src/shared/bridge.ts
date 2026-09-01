/**
 * QWebChannel bridge adapter：Qt callback-style → Vue Promise-style。
 *
 * 约束：
 * - Vue 组件不得直接散落 `new QWebChannel(...)`，必须统一经 connectBridge()；
 * - HomeBridge 带返回值的槽在 JS 端是 Promise（与生产 chrome.html 行为一致），
 *   adapter 统一转成 Promise<string>；
 * - 普通浏览器中 window.qt / QWebChannel 不存在：立即明确 reject
 *   （BridgeUnavailableError），绝不无限等待、绝不未处理异常；
 * - Qt 端 bridge 未按期注册时超时 reject，防止白屏悬挂。
 */

export type PageName = 'chrome' | 'dashboard'

export interface BridgeApi {
  navigate(index: number): void
  openPalette(): void
  navModel(): Promise<string>
  homeUsername(): Promise<string>
  dashboardSummary(): Promise<string>
  themePayload(): Promise<string>
  pageReady(page: PageName): void
  onActiveChanged(handler: (index: number) => void): void
  onThemeChanged(handler: (payloadJson: string) => void): void
}

export class BridgeUnavailableError extends Error {
  constructor(reason: string) {
    super(`bridge unavailable: ${reason}`)
    this.name = 'BridgeUnavailableError'
  }
}

/** QWebChannel 信号对象（activeChanged / themeChanged 等）的最小调用面。 */
interface QWebChannelSignal<T = any> {
  connect(callback: (val: T) => void): void
  disconnect(callback: (val: T) => void): void
}

/** Python HomeBridge 暴露的原始槽/信号形态（qwebchannel.js 生成的运行时对象）。 */
interface RawHomeBridge {
  navigate(index: number): void
  openPalette(): void
  navModel(): Promise<string>
  homeUsername(): Promise<string>
  dashboardSummary(): Promise<string>
  themePayload?(): Promise<string>
  pageReady(page: string): void
  activeChanged?: QWebChannelSignal<number>
  themeChanged?: QWebChannelSignal<string>
}

export function applyThemePayload(payloadStr?: string | null): void {
  if (!payloadStr) return
  try {
    const data = typeof payloadStr === 'string' ? JSON.parse(payloadStr) : payloadStr
    const themeId = data.id || 'calm'
    const isDark = Boolean(data.is_dark)
    document.documentElement.setAttribute('data-theme', themeId)
    document.documentElement.classList.toggle('dark', isDark)
  } catch {
    // ignore malformed payload
  }
}

function toPromiseString(value: unknown, slot: string): Promise<string> {
  // QWebChannel 的异步槽在 JS 端已是 Promise；同步返回值兜底包一层，
  // 保证 adapter 出口永远是 Promise<string>。
  if (value instanceof Promise) {
    return value
  }
  return Promise.resolve(value as string).then((v) => {
    if (typeof v !== 'string') {
      throw new TypeError(`bridge.${slot}() 返回了非字符串：${typeof v}`)
    }
    return v
  })
}

export function connectBridge(timeoutMs = 4000): Promise<BridgeApi> {
  return new Promise<BridgeApi>((resolve, reject) => {
    if (typeof QWebChannel !== 'function' || !window.qt?.webChannelTransport) {
      reject(new BridgeUnavailableError('window.qt / QWebChannel 不存在（普通浏览器环境）'))
      return
    }

    const timer = window.setTimeout(() => {
      reject(new BridgeUnavailableError(`QWebChannel 连接超时（${timeoutMs}ms）`))
    }, timeoutMs)

    new QWebChannel(window.qt.webChannelTransport, (channel) => {
      window.clearTimeout(timer)
      const raw = channel.objects?.['bridge'] as RawHomeBridge | undefined
      if (!raw) {
        reject(new BridgeUnavailableError('ch.objects.bridge 未注册'))
        return
      }
      if (typeof raw.navModel !== 'function' || typeof raw.pageReady !== 'function') {
        reject(new BridgeUnavailableError('bridge 缺少必需槽（navModel/pageReady）'))
        return
      }
      resolve({
        navigate: (index: number) => raw.navigate(index),
        openPalette: () => raw.openPalette(),
        navModel: () => toPromiseString(raw.navModel(), 'navModel'),
        homeUsername: () => toPromiseString(raw.homeUsername(), 'homeUsername'),
        dashboardSummary: () => toPromiseString(raw.dashboardSummary(), 'dashboardSummary'),
        themePayload: () => (typeof raw.themePayload === 'function' ? toPromiseString(raw.themePayload(), 'themePayload') : Promise.resolve('{}')),
        pageReady: (page: PageName) => raw.pageReady(page),
        onActiveChanged: (handler: (index: number) => void) => {
          if (!raw.activeChanged) {
            throw new BridgeUnavailableError('bridge.activeChanged 信号不存在')
          }
          raw.activeChanged.connect(handler)
        },
        onThemeChanged: (handler: (payloadJson: string) => void) => {
          if (raw.themeChanged) {
            raw.themeChanged.connect(handler)
          }
        },
      })
    })
  })
}
