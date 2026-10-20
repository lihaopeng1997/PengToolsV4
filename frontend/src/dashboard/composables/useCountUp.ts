import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

function motionReduced(): boolean {
  if (typeof window === 'undefined') return true
  if (document.body.classList.contains('motion-disabled')) return true
  return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
}

/** 数字从当前值缓动到目标值；减弱动态时直接跳到目标值。 */
export function useCountUp(target: Ref<number>, duration = 700): Ref<number> {
  const display = ref(0)
  let frame = 0

  function run(to: number): void {
    cancelAnimationFrame(frame)
    const from = display.value
    if (motionReduced() || from === to) {
      display.value = to
      return
    }
    const start = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - t, 3)
      display.value = Math.round(from + (to - from) * eased)
      if (t < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
  }

  watch(target, (value) => run(Number.isFinite(value) ? value : 0), { immediate: true })
  onBeforeUnmount(() => cancelAnimationFrame(frame))
  return display
}
