<script setup lang="ts">
import { computed } from 'vue'
import PrismIcon from '../../shared/components/PrismIcon.vue'

interface QuoteItem {
  id?: string
  text: string
  source: string
  author?: string
}

const props = withDefaults(
  defineProps<{
    greeting?: string | null
    username?: string | null
    dateText?: string | null
    quote?: QuoteItem | null
    isDemo?: boolean | null
  }>(),
  {
    greeting: '下午好',
    username: 'Lihp',
    dateText: '',
    quote: () => ({
      text: '长风破浪会有时，直挂云帆济沧海。',
      source: '李白《行路难·其一》',
      author: '李白'
    }),
    isDemo: false
  }
)

const emit = defineEmits<{
  (e: 'changeQuote'): void
  (e: 'writeDaily'): void
  (e: 'createRequirement'): void
  (e: 'continueTask'): void
}>()

const displayGreeting = computed(() => props.greeting || '下午好')
const displayUsername = computed(() => props.username || 'Lihp')
const quoteTitle = computed(() => {
  if (!props.quote) return ''
  return props.quote.source || props.quote.author || ''
})
</script>

<template>
  <section class="card hero">
    <div class="hero-content">
      <div class="eyebrow">YOUR NEXT MOVE / PRISM WORKSPACE</div>
      <h2 class="hero-title">
        <span>让每个想法，轻盈落地。</span>
        <small class="user-greeting">{{ displayGreeting }}，<em>{{ displayUsername }}</em> 👋</small>
        <span v-if="props.isDemo" class="demo-tag">示例数据</span>
      </h2>

      <p class="daily-line">
        <span class="date-part">{{ props.dateText }} · </span>
        <span class="quote-part" :title="quoteTitle">{{ props.quote?.text }}</span>
        <button
          class="quote-next"
          type="button"
          aria-label="换一句经典诗文"
          :title="`换一句 · ${quoteTitle}`"
          @click="emit('changeQuote')"
        >
          <PrismIcon name="refresh" :size="13" />
        </button>
      </p>

      <div class="hero-actions">
        <button class="btn btn-primary" type="button" @click="emit('createRequirement')">
          <PrismIcon name="plus" :size="15" />
          <span>新建需求</span>
        </button>
        <button class="btn btn-ghost" type="button" @click="emit('writeDaily')">
          <PrismIcon name="file" :size="15" />
          <span>写日报</span>
        </button>
      </div>
    </div>

    <!-- 120x120 需求权威规范主视觉图形及 4.8s 循环动效 -->
    <div class="prism-orb" aria-hidden="true">
      <div class="orb-inner">
        <span class="orb-gem">
          <PrismIcon name="spark" :size="30" />
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.hero {
  position: relative;
  min-height: 176px;
  height: 176px;
  padding: 24px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(110deg, rgba(228, 220, 250, 0.76), rgba(231, 231, 251, 0.65) 60%, rgba(214, 240, 242, 0.48));
  border: 1px solid var(--border, rgba(119, 123, 163, 0.16));
  box-shadow: 0 4px 14px var(--shadow-l2, rgba(113, 87, 209, 0.12));
  overflow: hidden;
  box-sizing: border-box;
}

:global([data-theme="black"]) .hero,
:global(body.theme-black) .hero {
  background: linear-gradient(110deg, rgba(38, 36, 55, 0.85), rgba(30, 30, 43, 0.8) 60%, rgba(25, 33, 44, 0.7));
  border-color: var(--border, rgba(255, 255, 255, 0.08));
}

.hero-content {
  flex: 1;
  min-width: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.eyebrow {
  font: 10px Consolas, monospace;
  letter-spacing: 2px;
  color: var(--primary, rgb(108, 88, 217));
  opacity: 0.85;
  margin-bottom: 4px;
}

.hero-title {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.4px;
  color: var(--text, rgb(40, 50, 74));
  margin: 2px 0 6px 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.user-greeting {
  font-size: 13px;
  font-weight: normal;
  color: var(--text-muted, rgb(123, 132, 153));
}

.user-greeting em {
  font-style: normal;
  color: var(--primary, rgb(108, 88, 217));
  font-weight: 500;
}

.demo-tag {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(108, 88, 217, 0.12);
  color: var(--primary, rgb(108, 88, 217));
}

.daily-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-muted, rgb(123, 132, 153));
  margin: 4px 0 14px 0;
  line-height: 1.5;
  flex-wrap: nowrap;
}

.date-part {
  color: var(--text, rgb(40, 50, 74));
  font-weight: 500;
  white-space: nowrap;
}

.quote-part {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 460px;
}

.quote-next {
  width: 28px;
  height: 28px;
  min-width: 28px;
  min-height: 28px;
  display: inline-grid;
  place-items: center;
  border-radius: 6px;
  border: 1px solid var(--border, rgba(119, 123, 163, 0.15));
  background: rgba(255, 255, 255, 0.6);
  color: var(--text-muted, rgb(123, 132, 153));
  cursor: pointer;
  padding: 0;
  transition: transform 0.18s ease, color 0.18s ease, background 0.18s ease;
}

.quote-next:hover {
  background: var(--primary-soft, rgba(108, 88, 217, 0.12));
  color: var(--primary, rgb(108, 88, 217));
  transform: rotate(30deg);
}

.hero-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 7px 14px;
  border: 1px solid var(--border, rgba(119, 123, 163, 0.2));
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.8);
  font-size: 11px;
  font-weight: 500;
  color: var(--text, rgb(40, 50, 74));
  cursor: pointer;
  transition: background 0.16s ease, transform 0.16s ease, border-color 0.16s ease;
}

.btn-primary {
  background: var(--primary, rgb(108, 88, 217));
  color: var(--on-primary, rgb(255, 255, 255));
  border-color: var(--primary, rgb(108, 88, 217));
  box-shadow: 0 6px 16px var(--shadow-l2, rgba(108, 88, 217, 0.2));
}

.btn-primary:hover {
  background: var(--primary-hover, rgb(91, 70, 199));
  transform: translateY(-1px);
}

.btn-ghost {
  background: rgba(255, 255, 255, 0.65);
  color: var(--text, rgb(40, 50, 74));
}

.btn-ghost:hover {
  background: var(--primary-soft, rgba(108, 88, 217, 0.1));
  color: var(--primary, rgb(108, 88, 217));
}

/* 120x120 主视觉图形规范 */
.prism-orb {
  width: 120px;
  height: 120px;
  min-width: 120px;
  min-height: 120px;
  position: relative;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  margin-right: 12px;
  user-select: none;
  pointer-events: none;
}

.orb-inner {
  width: 120px;
  height: 120px;
  position: relative;
  display: grid;
  place-items: center;
  animation: prismOrb 4.8s ease-in-out infinite;
  will-change: transform;
}

.orb-inner::before,
.orb-inner::after {
  content: "";
  position: absolute;
  inset: 10px 0;
  border: 1px solid rgba(183, 161, 228, 0.55);
  border-radius: 50%;
  transform: rotate(-30deg);
}

.orb-inner::after {
  inset: 0 14px;
  border-color: rgba(255, 255, 255, 0.85);
  transform: rotate(30deg);
}

.orb-gem {
  display: grid;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.85);
  width: 60px;
  height: 60px;
  border-radius: 20px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.8), rgba(198, 175, 248, 0.35));
  box-shadow: inset 4px 4px 12px rgba(255, 255, 255, 0.4), 0 6px 20px rgba(108, 88, 217, 0.15);
  color: var(--primary, rgb(108, 88, 217));
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

/* 4.8s 权威动效规范：y: 0 / -5 / 0, rotate: -5 / 5 / -5deg, scale: 1 / 1.04 / 1 */
@keyframes prismOrb {
  0%, 100% {
    transform: translateY(0px) rotate(-5deg) scale(1);
  }
  50% {
    transform: translateY(-5px) rotate(5deg) scale(1.04);
  }
}

/* 动效契约支持 */
@media (prefers-reduced-motion: reduce) {
  .orb-inner {
    animation: none !important;
  }
}

:global(body.motion-disabled) .orb-inner,
:global([data-motion="disabled"]) .orb-inner {
  animation: none !important;
}

:global(body.page-hidden) .orb-inner {
  animation-play-state: paused !important;
}
</style>
