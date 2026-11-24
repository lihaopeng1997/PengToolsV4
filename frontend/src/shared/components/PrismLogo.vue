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
      <svg class="pl-svg" viewBox="0 0 64 64" fill="none">
        <defs>
          <linearGradient :id="`${uid}-bg`" x1="6" y1="4" x2="58" y2="60" gradientUnits="userSpaceOnUse">
            <stop offset="0%" class="pl-stop-a" />
            <stop offset="52%" class="pl-stop-mid" />
            <stop offset="100%" class="pl-stop-b" />
          </linearGradient>
          <radialGradient :id="`${uid}-glow-tl`" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(15 10) rotate(42) scale(38 28)">
            <stop offset="0%" stop-color="#D6CCFF" stop-opacity="0.54" />
            <stop offset="100%" stop-color="#D6CCFF" stop-opacity="0" />
          </radialGradient>
          <radialGradient :id="`${uid}-glow-br`" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(54 56) rotate(-135) scale(34 26)">
            <stop offset="0%" stop-color="#38E8D8" stop-opacity="0.28" />
            <stop offset="100%" stop-color="#38E8D8" stop-opacity="0" />
          </radialGradient>
          <linearGradient :id="`${uid}-rim`" x1="8" y1="4" x2="56" y2="60" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stop-color="#FFFFFF" stop-opacity="0.48" />
            <stop offset="52%" stop-color="#FFFFFF" stop-opacity="0.14" />
            <stop offset="100%" stop-color="#8CF5E8" stop-opacity="0.28" />
          </linearGradient>
          <linearGradient :id="`${uid}-stem`" x1="18" y1="20" x2="27" y2="52" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stop-color="#DDD6FF" />
            <stop offset="100%" stop-color="#BCB0F8" />
          </linearGradient>
          <linearGradient :id="`${uid}-shine`" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" class="pl-shine-edge" />
            <stop offset="0.5" class="pl-shine-mid" />
            <stop offset="1" class="pl-shine-edge" />
          </linearGradient>
          <clipPath :id="`${uid}-clip`">
            <rect x="2" y="2" width="60" height="60" rx="17" />
          </clipPath>
        </defs>
        <g class="pl-bg">
          <rect x="2" y="2" width="60" height="60" rx="17" :fill="`url(#${uid}-bg)`" />
          <rect x="2" y="2" width="60" height="60" rx="17" :fill="`url(#${uid}-glow-tl)`" />
          <rect x="2" y="2" width="60" height="60" rx="17" :fill="`url(#${uid}-glow-br)`" />
          <rect x="2.75" y="2.75" width="58.5" height="58.5" rx="16.25" :stroke="`url(#${uid}-rim)`" stroke-width="1.5" />
        </g>
        <g :clip-path="`url(#${uid}-clip)`">
          <rect class="pl-shine" x="-40" y="-10" width="26" height="84" :fill="`url(#${uid}-shine)`" />
        </g>
        <path
          class="pl-stem"
          d="M18 20.5C18 16.9 20.9 14 24.5 14H27V47.5C27 49.7 25.2 51.5 23 51.5H20.5C19.1 51.5 18 50.4 18 49V20.5Z"
          :fill="`url(#${uid}-stem)`"
        />
        <path d="M18 33.4L27 38.2V41.5L18 36.8V33.4Z" fill="#432FA8" fill-opacity="0.32" />
        <path
          class="pl-letter"
          fill-rule="evenodd"
          clip-rule="evenodd"
          d="M23 13.5H34C41 13.5 46 18.6 46 25.5C46 32.4 41 37.5 34 37.5H23.5C20.5 37.5 18 35 18 32V18.5C18 15.7 20.2 13.5 23 13.5ZM27 21.4V29.6H33.5C36 29.6 37.8 27.9 37.8 25.5C37.8 23.1 36 21.4 33.5 21.4H27Z"
          pathLength="1"
        />
        <path
          class="pl-star"
          d="M49.2 7.8C49.7 12.1 51.5 13.9 55.8 14.4C51.5 14.9 49.7 16.7 49.2 21C48.7 16.7 46.9 14.9 42.6 14.4C46.9 13.9 48.7 12.1 49.2 7.8Z"
        />
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

.pl-stop-a { stop-color: #9A86FF; }
.pl-stop-mid { stop-color: var(--primary, #6F57E8); }
.pl-stop-b { stop-color: #4934B6; }
.pl-shine-edge { stop-color: var(--on-primary); stop-opacity: 0; }
.pl-shine-mid { stop-color: var(--on-primary); stop-opacity: 0.45; }

.pl-bg {
  transform-origin: 32px 32px;
  animation: pl-pop 620ms cubic-bezier(0.2, 0.9, 0.3, 1.2) both;
}

.pl-stem {
  animation: pl-pop 680ms cubic-bezier(0.2, 0.9, 0.3, 1.2) 80ms both;
}

.pl-letter {
  fill: var(--on-primary);
  fill-opacity: 1;
  stroke: var(--on-primary);
  stroke-width: 0.6;
  stroke-linejoin: round;
  stroke-dasharray: 1;
  stroke-dashoffset: 0;
  animation: pl-draw 0.95s ease-out 0.2s both;
}

.pl-star {
  fill: #82F5E6;
  opacity: 0.96;
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
