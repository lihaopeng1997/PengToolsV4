<script setup lang="ts">
/**
 * 首页主视觉「棱镜光谱」：一束光射入棱镜，折射成 5 道光带 = 需求的 5 个阶段。
 * 光带粗细与流动光点数量跟随该阶段的需求数；悬停高亮，点击在下方看板中筛选该阶段。
 * 指针移动会轻微改变入射角，棱镜中心保留原首页的星芒图案。
 */
import { computed, ref } from 'vue'
import { STAGE_DEFS, type StageKey } from '../types'

const props = defineProps<{
  counts: Partial<Record<StageKey, number>>
  focus?: StageKey | null
}>()

const emit = defineEmits<{
  (e: 'focusStage', key: StageKey | null): void
}>()

const W = 600
const H = 300
const EXIT = { x: 300, y: 150 }
const END_X = 468
const ENTRY = { x: 222, y: 150 }

const hovered = ref<StageKey | null>(null)
const tilt = ref(0) // -1..1，指针纵向位置
const pulse = ref(0)

const total = computed(() => STAGE_DEFS.reduce((sum, s) => sum + (props.counts[s.key] ?? 0), 0))
const maxCount = computed(() => Math.max(1, ...STAGE_DEFS.map((s) => props.counts[s.key] ?? 0)))

const beams = computed(() => STAGE_DEFS.map((stage, index) => {
  const count = props.counts[stage.key] ?? 0
  const y = 46 + index * 52
  // 光带末端厚度：空阶段保留一道细光，避免“没数据就空”
  const thick = count > 0 ? 12 + 26 * (count / maxCount.value) : 7
  const spread = 3 + index * 0.8
  const startTop = EXIT.y - 16 + index * 7 - spread / 2
  const startBottom = startTop + spread
  const points = `${EXIT.x},${startTop} ${END_X},${y - thick / 2} ${END_X},${y + thick / 2} ${EXIT.x},${startBottom}`
  const particles = count > 0 ? Math.min(5, 1 + Math.ceil(count / 2)) : 0
  return {
    ...stage,
    count,
    y,
    thick,
    points,
    path: `M${EXIT.x} ${(startTop + startBottom) / 2} L${END_X} ${y}`,
    particles,
    dur: 2.6 + index * 0.35,
  }
}))

const sourceY = computed(() => ENTRY.y - 70 + tilt.value * 46)

function active(key: StageKey): boolean {
  if (hovered.value) return hovered.value === key
  if (props.focus) return props.focus === key
  return true
}

function onMove(event: PointerEvent): void {
  const el = event.currentTarget as SVGSVGElement
  const rect = el.getBoundingClientRect()
  const ratio = (event.clientY - rect.top) / Math.max(1, rect.height)
  tilt.value = Math.max(-1, Math.min(1, (ratio - 0.5) * 2))
}

function onLeave(): void {
  hovered.value = null
  tilt.value = 0
}

function pick(key: StageKey): void {
  emit('focusStage', props.focus === key ? null : key)
}

function onPrism(): void {
  pulse.value += 1
  emit('focusStage', null)
}

function onKey(event: KeyboardEvent, key: StageKey): void {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    pick(key)
  }
}
</script>

<template>
  <div class="spectrum" :class="{ focused: Boolean(props.focus) }">
    <svg
      :viewBox="`0 0 ${W} ${H}`"
      role="img"
      :aria-label="`需求阶段光谱，共 ${total} 个`"
      @pointermove="onMove"
      @pointerleave="onLeave"
    >
      <defs>
        <linearGradient id="ps-incoming" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" style="stop-color: var(--primary); stop-opacity: 0" />
          <stop offset="0.6" style="stop-color: var(--primary); stop-opacity: 0.35" />
          <stop offset="1" style="stop-color: var(--aurora-end); stop-opacity: 0.9" />
        </linearGradient>
        <linearGradient id="ps-core" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" style="stop-color: var(--surface); stop-opacity: 0" />
          <stop offset="1" style="stop-color: var(--surface); stop-opacity: 1" />
        </linearGradient>
        <linearGradient id="ps-side" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style="stop-color: var(--primary); stop-opacity: 0.42" />
          <stop offset="1" style="stop-color: var(--accent-indigo); stop-opacity: 0.18" />
        </linearGradient>
        <linearGradient id="ps-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style="stop-color: var(--surface); stop-opacity: 0.95" />
          <stop offset="0.55" style="stop-color: var(--primary-soft); stop-opacity: 0.85" />
          <stop offset="1" style="stop-color: var(--aurora-end); stop-opacity: 0.55" />
        </linearGradient>
        <linearGradient v-for="b in beams" :id="`ps-beam-${b.key}`" :key="b.key" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" :style="{ stopColor: b.color, stopOpacity: 0.95 }" />
          <stop offset="1" :style="{ stopColor: b.color, stopOpacity: 0.12 }" />
        </linearGradient>
        <filter id="ps-glow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="6" />
        </filter>
      </defs>

      <!-- 入射光 -->
      <g class="incoming">
        <line :x1="0" :y1="sourceY" :x2="ENTRY.x" :y2="ENTRY.y" stroke="url(#ps-incoming)" stroke-width="14" filter="url(#ps-glow)" />
        <line :x1="30" :y1="sourceY" :x2="ENTRY.x" :y2="ENTRY.y" stroke="url(#ps-incoming)" stroke-width="4" stroke-linecap="round" />
        <line :x1="90" :y1="sourceY + (ENTRY.y - sourceY) * 0.4" :x2="ENTRY.x" :y2="ENTRY.y" stroke="url(#ps-core)" stroke-width="1.6" stroke-linecap="round" />
        <circle :cx="ENTRY.x" :cy="ENTRY.y" r="5" class="entry-flare" filter="url(#ps-glow)" />
      </g>

      <!-- 光带 -->
      <g
        v-for="b in beams"
        :key="b.key"
        class="beam"
        :class="{ dim: !active(b.key), empty: b.count === 0 }"
        role="button"
        tabindex="0"
        :aria-label="`${b.label} ${b.count} 个，点击在看板中筛选`"
        :aria-pressed="props.focus === b.key"
        @pointerenter="hovered = b.key"
        @pointerleave="hovered = null"
        @click="pick(b.key)"
        @keydown="onKey($event, b.key)"
      >
        <polygon :points="b.points" :fill="`url(#ps-beam-${b.key})`" class="beam-glow" filter="url(#ps-glow)" />
        <polygon :points="b.points" :fill="`url(#ps-beam-${b.key})`" />
        <circle v-for="n in b.particles" :key="n" r="2.4" class="particle" :style="{ fill: b.color }">
          <animateMotion :dur="`${b.dur}s`" repeatCount="indefinite" :begin="`-${(b.dur / b.particles) * n}s`" :path="b.path" />
        </circle>
        <g :transform="`translate(${END_X + 14} ${b.y})`" class="label">
          <circle r="4" :style="{ fill: b.color }" />
          <text x="12" y="1" class="label-name">{{ b.label }}</text>
          <text x="72" y="2" class="label-count" :style="{ fill: b.count ? b.color : undefined }">{{ b.count }}</text>
        </g>
        <!-- 扩大点击热区 -->
        <rect :x="EXIT.x + 30" :y="b.y - 24" :width="W - EXIT.x - 30" height="48" class="hit" />
      </g>

      <!-- 棱镜 -->
      <g class="prism" :key="pulse" role="button" tabindex="0" aria-label="显示全部阶段" @click="onPrism" @keydown.enter.prevent="onPrism">
        <ellipse cx="270" cy="246" rx="78" ry="9" class="prism-floor" filter="url(#ps-glow)" />
        <polygon points="262,52 288,40 352,224 328,236" fill="url(#ps-side)" class="prism-side" />
        <polygon points="262,52 196,236 328,236" fill="url(#ps-glass)" class="prism-body" />
        <polyline points="262,58 206,228" class="prism-edge" />
        <polyline points="264,54 286,44" class="prism-edge thin" />
        <path d="M262 132 l5 13 13 5 -13 5 -5 13 -5 -13 -13 -5 13 -5z" class="prism-spark" />
      </g>
    </svg>
    <p class="hint">{{ props.focus ? '再次点击光带取消筛选' : '点击光带，在看板中只看该阶段' }}</p>
  </div>
</template>

<style scoped>
.spectrum {
  position: relative;
  width: min(100%, 600px);
  flex-shrink: 0;
}

svg {
  display: block;
  width: 100%;
  height: auto;
  overflow: visible;
}

.incoming-core {
  stroke: var(--surface);
  opacity: 0.95;
  filter: drop-shadow(0 0 4px var(--primary-soft));
}

.beam {
  cursor: pointer;
  outline: none;
  transition: opacity 220ms ease;
}

.beam.dim {
  opacity: 0.22;
}

.beam .beam-glow {
  opacity: 0.55;
}

.beam:hover .beam-glow,
.beam:focus-visible .beam-glow {
  opacity: 0.95;
}

.beam.empty polygon {
  opacity: 0.6;
}

.beam:focus-visible .label-name {
  text-decoration: underline;
}

.particle {
  opacity: 0.9;
}

.label-name {
  font-size: 14px;
  font-weight: 600;
  fill: var(--text);
  dominant-baseline: middle;
}

.label-count {
  font-size: 22px;
  font-weight: 700;
  fill: var(--text-muted);
  dominant-baseline: middle;
  font-variant-numeric: tabular-nums;
}

.hit {
  fill: transparent;
}

.prism {
  cursor: pointer;
  outline: none;
  transform-origin: 262px 160px;
  animation: prism-in 700ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.prism:hover .prism-body {
  filter: brightness(1.04);
}

.prism-floor {
  fill: var(--primary);
  opacity: 0.16;
}

.prism-side {
  stroke: var(--surface);
  stroke-width: 1.5;
  stroke-linejoin: round;
}

.entry-flare {
  fill: var(--surface);
  opacity: 0.95;
}

.prism-body {
  stroke: var(--surface);
  stroke-width: 2;
  stroke-linejoin: round;
  filter: drop-shadow(0 10px 18px var(--shadow-l2));
}

.prism-edge {
  fill: none;
  stroke: var(--surface);
  stroke-width: 3;
  stroke-linecap: round;
  opacity: 0.9;
}

.prism-edge.thin {
  stroke-width: 1.5;
  opacity: 0.7;
}

.prism-spark {
  fill: var(--primary);
  transform-origin: 262px 150px;
  animation: spark 3.6s ease-in-out infinite;
}

.hint {
  margin: 2px 0 0;
  text-align: right;
  font-size: 12px;
  color: var(--text-muted);
}

@keyframes prism-in {
  from { opacity: 0; transform: scale(0.86) rotate(-6deg); }
  to { opacity: 1; transform: none; }
}

@keyframes spark {
  0%, 100% { transform: scale(1) rotate(0deg); opacity: 0.85; }
  50% { transform: scale(1.18) rotate(45deg); opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .prism, .prism-spark { animation: none; }
  .particle { display: none; }
}

:global(body.motion-disabled) .particle,
:global([data-motion="disabled"]) .particle {
  display: none;
}
</style>
