<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

/**
 * 品牌动态 Logo：入场描边 → 填充；空闲时光泽扫过、星芒闪烁、光晕呼吸；
 * 指针悬停 3D 倾斜，点击回弹并迸发光点。减弱动态/禁用动画/后台页面时全部静止。
 */
const props = withDefaults(
  defineProps<{
    size?: number
    interactive?: boolean
    halo?: boolean
  }>(),
  {
    size: 64,
    interactive: true,
    halo: true
  }
)

const uid = `pl-${Math.random().toString(36).slice(2, 9)}`
const tiltX = ref(0)
const tiltY = ref(0)
const bursting = ref(false)
let burstTimer: number | undefined

const sizePx = computed(() => `${props.size}px`)
const tiltStyle = computed(() => ({
  transform: `perspective(${props.size * 6}px) rotateX(${tiltX.value}deg) rotateY(${tiltY.value}deg)`
}))

function onMove(event: PointerEvent): void {
  if (!props.interactive) return
  const target = event.currentTarget as HTMLElement | null
  if (!target) return
  const rect = target.getBoundingClientRect()
  const dx = (event.clientX - rect.left) / rect.width - 0.5
  const dy = (event.clientY - rect.top) / rect.height - 0.5
  tiltY.value = Math.round(dx * 24)
  tiltX.value = Math.round(-dy * 24)
}

function onLeave(): void {
  tiltX.value = 0
  tiltY.value = 0
}

function onClick(): void {
  if (!props.interactive) return
  bursting.value = false
  window.clearTimeout(burstTimer)
  requestAnimationFrame(() => {
    bursting.value = true
    burstTimer = window.setTimeout(() => { bursting.value = false }, 900)
  })
}

onBeforeUnmount(() => window.clearTimeout(burstTimer))

const SPARKS = [0, 45, 90, 135, 180, 225, 270, 315]
</script>

<template>
  <span
    class="prism-logo"
    :class="{ interactive: props.interactive, bursting }"
    :style="{ width: sizePx, height: sizePx }"
    aria-hidden="true"
    @pointermove="onMove"
    @pointerleave="onLeave"
    @click="onClick"
  >
    <span v-if="props.halo" class="pl-halo"></span>
    <span class="pl-tilt" :style="tiltStyle">
      <svg class="pl-svg" viewBox="0 0 64 64">
        <defs>
          <linearGradient :id="`${uid}-bg`" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" class="pl-stop-a" />
            <stop offset="1" class="pl-stop-b" />
          </linearGradient>
          <linearGradient :id="`${uid}-shine`" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" class="pl-shine-edge" />
            <stop offset="0.5" class="pl-shine-mid" />
            <stop offset="1" class="pl-shine-edge" />
          </linearGradient>
          <clipPath :id="`${uid}-clip`">
            <rect x="2" y="2" width="60" height="60" rx="19" />
          </clipPath>
        </defs>
        <rect class="pl-bg" x="2" y="2" width="60" height="60" rx="19" :fill="`url(#${uid}-bg)`" />
        <g :clip-path="`url(#${uid}-clip)`">
          <rect class="pl-shine" x="-40" y="-10" width="26" height="84" :fill="`url(#${uid}-shine)`" />
        </g>
        <path
          class="pl-letter"
          d="M22 17h16c17 0 16 25-1 25h-8l-3 10H15Zm10 10-2 7h7c6 0 7-7 1-7Z"
          pathLength="1"
        />
        <path class="pl-star" d="m44 9 4 7 8 2-8 3-3 7-2-8-7-3 8-2Z" />
      </svg>
    </span>
    <span class="pl-sparks">
      <span v-for="angle in SPARKS" :key="angle" class="pl-spark" :style="{ '--a': `${angle}deg` }"></span>
    </span>
  </span>
</template>

<style scoped>
.prism-logo {
  position: relative;
  display: inline-grid;
  place-items: center;
  flex-shrink: 0;
  isolation: isolate;
}

.prism-logo.interactive {
  cursor: pointer;
}

.pl-halo {
  position: absolute;
  inset: -22%;
  border-radius: 50%;
  background: radial-gradient(circle, var(--primary-soft) 0%, transparent 70%);
  z-index: -1;
  animation: pl-breathe 4.8s ease-in-out infinite;
}

.pl-tilt {
  display: block;
  width: 100%;
  height: 100%;
  transition: transform 180ms ease-out;
  transform-style: preserve-3d;
}

.pl-svg {
  display: block;
  width: 100%;
  height: 100%;
  overflow: visible;
  filter: drop-shadow(0 6px 14px var(--shadow-l2));
}

.pl-stop-a { stop-color: var(--primary-grad-start); }
.pl-stop-b { stop-color: var(--primary-grad-end); }
.pl-shine-edge { stop-color: var(--on-primary); stop-opacity: 0; }
.pl-shine-mid { stop-color: var(--on-primary); stop-opacity: 0.55; }

.pl-bg {
  transform-origin: 32px 32px;
  animation: pl-pop 620ms cubic-bezier(0.2, 0.9, 0.3, 1.2) both;
}

.pl-letter {
  fill: var(--on-primary);
  fill-opacity: 1;
  stroke: var(--on-primary);
  stroke-width: 1.4;
  stroke-linejoin: round;
  stroke-dasharray: 1;
  stroke-dashoffset: 0;
  animation: pl-draw 1.1s ease-out 0.25s both;
}

.pl-star {
  fill: var(--accent-cyan);
  opacity: 0.92;
  transform-box: fill-box;
  transform-origin: center;
  animation: pl-twinkle 4s ease-in-out 1.4s infinite;
}

.pl-shine {
  transform: translateX(0) rotate(18deg);
  animation: pl-sweep 6s ease-in-out 1.6s infinite;
}

.pl-sparks {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.pl-spark {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 6px;
  height: 6px;
  margin: -3px 0 0 -3px;
  border-radius: 50%;
  background: var(--accent-cyan);
  opacity: 0;
}

.bursting .pl-spark {
  animation: pl-burst 760ms ease-out both;
}

.bursting .pl-tilt {
  animation: pl-bounce 520ms cubic-bezier(0.3, 1.6, 0.5, 1);
}

@keyframes pl-pop {
  from { transform: scale(0.6); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
}

@keyframes pl-draw {
  0% { stroke-dashoffset: 1; fill-opacity: 0; }
  70% { stroke-dashoffset: 0; fill-opacity: 0; }
  100% { stroke-dashoffset: 0; fill-opacity: 1; }
}

@keyframes pl-twinkle {
  0%, 70%, 100% { transform: scale(1) rotate(0deg); opacity: 0.92; }
  80% { transform: scale(1.35) rotate(45deg); opacity: 1; }
  90% { transform: scale(0.9) rotate(90deg); opacity: 0.8; }
}

@keyframes pl-sweep {
  0%, 60% { transform: translateX(0) rotate(18deg); }
  85%, 100% { transform: translateX(120px) rotate(18deg); }
}

@keyframes pl-breathe {
  0%, 100% { transform: scale(0.92); opacity: 0.7; }
  50% { transform: scale(1.06); opacity: 1; }
}

@keyframes pl-bounce {
  0% { scale: 1; }
  35% { scale: 0.88; }
  70% { scale: 1.08; }
  100% { scale: 1; }
}

@keyframes pl-burst {
  0% { opacity: 1; transform: rotate(var(--a)) translateY(0) scale(1); }
  100% { opacity: 0; transform: rotate(var(--a)) translateY(-150%) translateY(-18px) scale(0.3); }
}

@media (prefers-reduced-motion: reduce) {
  .pl-halo, .pl-bg, .pl-letter, .pl-star, .pl-shine, .pl-spark, .pl-tilt {
    animation: none !important;
    transition: none !important;
  }
}

:global(body.motion-disabled) .pl-halo,
:global(body.motion-disabled) .pl-bg,
:global(body.motion-disabled) .pl-letter,
:global(body.motion-disabled) .pl-star,
:global(body.motion-disabled) .pl-shine,
:global(body.motion-disabled) .pl-spark,
:global(body.motion-disabled) .pl-tilt {
  animation: none !important;
  transition: none !important;
}

:global(body.page-hidden) .pl-halo,
:global(body.page-hidden) .pl-star,
:global(body.page-hidden) .pl-shine {
  animation-play-state: paused !important;
}
</style>
