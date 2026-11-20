/**
 * 全局轻量点击水波纹（用于 Vue Chrome 侧边栏与 Dashboard 首页）：
 * - 事件委托在 document，零逐组件改造负担；
 * - 尊重 prefers-reduced-motion 与 body.motion-disabled 契约；
 * - 动画结束自动清理 DOM，零内存泄漏。
 */

const STYLE_ID = 'pengtools-web-ripple-style'
const TARGET_SELECTOR =
  'button:not(:disabled), .nav-item, .hero-primary, .hero-ghost, .kpi:not(.static), .quick-btn, .search-hit'

function ensureRippleStyles(): void {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
    .pt-ripple-host {
      position: relative !important;
      overflow: hidden !important;
    }
    .pt-ripple-wave {
      position: absolute;
      border-radius: 999px;
      pointer-events: none;
      transform: translate(-50%, -50%) scale(0);
      opacity: 0.42;
      animation: ptRippleExpand 500ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
      will-change: transform, opacity;
      z-index: 2;
    }
    .pt-ripple-wave--light {
      background: rgba(255, 255, 255, 0.58);
    }
    .pt-ripple-wave--accent {
      background: var(--primary, #6c58d9);
      opacity: 0.22;
    }
    @keyframes ptRippleExpand {
      0% { transform: translate(-50%, -50%) scale(0); opacity: 0.42; }
      100% { transform: translate(-50%, -50%) scale(1); opacity: 0; }
    }
    @media (prefers-reduced-motion: reduce) {
      .pt-ripple-wave { display: none !important; }
    }
    body.motion-disabled .pt-ripple-wave,
    [data-motion="disabled"] .pt-ripple-wave {
      display: none !important;
    }
  `
  document.head.appendChild(style)
}

function isMotionAllowed(): boolean {
  if (typeof window === 'undefined' || typeof document === 'undefined') return false
  if (document.body.classList.contains('motion-disabled')) return false
  if (document.documentElement.getAttribute('data-motion') === 'disabled') return false
  try {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false
  } catch {
    // ignore
  }
  return true
}

export function installWebRipples(): () => void {
  if (typeof document === 'undefined') return () => {}
  ensureRippleStyles()

  const onPointerDown = (event: PointerEvent): void => {
    if (event.button !== 0 || !isMotionAllowed()) return
    const origin = event.target as HTMLElement | null
    const host = origin?.closest?.(TARGET_SELECTOR) as HTMLElement | null
    if (!host) return
    const rect = host.getBoundingClientRect()
    if (rect.width < 22 || rect.height < 18) return

    const computed = window.getComputedStyle(host)
    if (computed.position === 'static') {
      host.classList.add('pt-ripple-host')
    } else if (computed.overflow === 'visible') {
      host.style.overflow = 'hidden'
    }

    const size = Math.max(rect.width, rect.height) * 2.15
    const wave = document.createElement('span')
    const isPrimary =
      host.classList.contains('hero-primary') ||
      host.classList.contains('btn-primary') ||
      host.getAttribute('data-action') === 'create-requirement'
    wave.className = `pt-ripple-wave ${isPrimary ? 'pt-ripple-wave--light' : 'pt-ripple-wave--accent'}`
    wave.style.width = `${size}px`
    wave.style.height = `${size}px`
    wave.style.left = `${event.clientX - rect.left}px`
    wave.style.top = `${event.clientY - rect.top}px`
    host.appendChild(wave)
    window.setTimeout(() => wave.remove(), 540)
  }

  document.addEventListener('pointerdown', onPointerDown, { passive: true })
  return () => {
    document.removeEventListener('pointerdown', onPointerDown)
  }
}
