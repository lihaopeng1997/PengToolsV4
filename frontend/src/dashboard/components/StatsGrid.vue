<script setup lang="ts">
import { computed } from 'vue'
import type { DashboardStats } from '../types'
import PrismIcon from '../../shared/components/PrismIcon.vue'

const props = defineProps<{
  stats?: DashboardStats | null
  isDemo?: boolean | null
}>()

const emit = defineEmits<{
  (e: 'navigate', navIndex: number): void
}>()

const reqOpenText = computed(() => {
  const v = props.stats?.req_open
  if (v == null) return '0'
  return String(v).padStart(2, '0')
})

const dailyText = computed(() => {
  const s = props.stats
  if (!s || s.daily_done == null) return '0 / 5'
  return `${s.daily_done} / ${s.daily_total ?? 5}`
})

const dailyTip = computed(() => {
  const done = props.stats?.daily_done ?? 0
  return done > 0 ? '今日进展已记录' : '记录每一份进展'
})

const monthlyTotalText = computed(() => {
  const v = props.stats?.monthly_release_total ?? props.stats?.display_monthly_release_total
  return v != null ? String(v) : '0'
})

const monthlyDoneTip = computed(() => {
  const done = props.stats?.monthly_release_done ?? props.stats?.display_monthly_release_done ?? 0
  return props.isDemo ? `已完成 ${done} 项 · 示例` : `已完成 ${done} 项`
})

const completedTotalText = computed(() => {
  const v = props.stats?.completed_total
  return v != null ? String(v) : '0'
})
</script>

<template>
  <div class="stat-grid">
    <!-- 1. 待办需求 (可点击跳转 nav 10) -->
    <article class="stat card clickable stat-1" tabindex="0" role="button" @click="emit('navigate', 10)" @keydown.enter="emit('navigate', 10)" @keydown.space.prevent="emit('navigate', 10)">
      <label class="stat-label">
        <span class="stat-icon-wrap ic-1">
          <PrismIcon name="file" :size="14" />
        </span>
        <span>待办需求</span>
      </label>
      <strong class="stat-val">{{ reqOpenText }}</strong>
      <p class="stat-tip">把下一步安排清楚</p>
    </article>

    <!-- 2. 本周日报 (可点击跳转 nav 9) -->
    <article class="stat card clickable stat-2" tabindex="0" role="button" @click="emit('navigate', 9)" @keydown.enter="emit('navigate', 9)" @keydown.space.prevent="emit('navigate', 9)">
      <label class="stat-label">
        <span class="stat-icon-wrap ic-2">
          <PrismIcon name="calendar" :size="14" />
        </span>
        <span>本周日报</span>
      </label>
      <strong class="stat-val">{{ dailyText }}</strong>
      <p class="stat-tip">{{ dailyTip }}</p>
    </article>

    <!-- 3. 本月上线 (可点击跳转 nav 10) -->
    <article class="stat card clickable stat-3" tabindex="0" role="button" @click="emit('navigate', 10)" @keydown.enter="emit('navigate', 10)" @keydown.space.prevent="emit('navigate', 10)">
      <label class="stat-label">
        <span class="stat-icon-wrap ic-3">
          <PrismIcon name="rocket" :size="14" />
        </span>
        <span>本月上线</span>
      </label>
      <strong class="stat-val">{{ monthlyTotalText }} <small>项</small></strong>
      <p class="stat-tip">{{ monthlyDoneTip }}</p>
    </article>

    <!-- 4. 累计完成 -->
    <article class="stat card stat-4">
      <label class="stat-label">
        <span class="stat-icon-wrap ic-4">
          <PrismIcon name="check" :size="14" />
        </span>
        <span>累计完成</span>
      </label>
      <strong class="stat-val">{{ completedTotalText }}</strong>
      <p class="stat-tip">让好想法持续落地</p>
    </article>
  </div>
</template>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 700px) {
  .stat-grid {
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }
}

.stat {
  position: relative;
  height: 108px;
  min-height: 108px;
  padding: 16px 18px;
  background: var(--surface, #fdfdff);
  border: 1px solid var(--border, rgba(119, 123, 163, 0.15));
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  box-sizing: border-box;
  box-shadow: 0 4px 16px rgba(132, 128, 173, 0.04);
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

:global([data-theme="black"]) .stat,
:global(body.theme-black) .stat {
  background: var(--surface, #1e1e2b);
  border-color: var(--border, rgba(255, 255, 255, 0.08));
}

.stat.clickable {
  cursor: pointer;
}

.stat.clickable:hover {
  transform: translateY(-2px);
  border-color: var(--primary, #6c58d9);
  box-shadow: 0 8px 20px rgba(108, 88, 217, 0.08);
}

.stat.clickable:focus-visible {
  outline: 2px solid var(--primary, #6c58d9);
  outline-offset: 2px;
}

.stat-label {
  font-size: 11px;
  color: var(--muted, #7b8499);
  display: flex;
  align-items: center;
  gap: 7px;
  font-weight: 500;
}

.stat-icon-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: var(--primary-soft, rgba(108, 88, 217, 0.08));
  color: var(--primary, #6c58d9);
  will-change: transform;
}

/* 5s 周期动效：仅 80%~100% 期间 2px 上浮归位，延迟 0 / 0.3 / 0.6 / 0.9s */
.ic-1 {
  animation: statIconFloat 5s ease-in-out infinite 0s;
}

.ic-2 {
  animation: statIconFloat 5s ease-in-out infinite 0.3s;
}

.ic-3 {
  animation: statIconFloat 5s ease-in-out infinite 0.6s;
}

.ic-4 {
  animation: statIconFloat 5s ease-in-out infinite 0.9s;
}

@keyframes statIconFloat {
  0%, 80%, 100% {
    transform: translateY(0px);
  }
  90% {
    transform: translateY(-2px);
  }
}

.stat-val {
  font-size: 26px;
  letter-spacing: -0.8px;
  font-weight: 600;
  color: var(--text, #28324a);
  line-height: 1.1;
  margin: 2px 0;
}

.stat-val small {
  font-size: 12px;
  color: var(--muted, #7b8499);
  font-weight: 400;
}

.stat-tip {
  font-size: 10px;
  color: var(--muted, #7b8499);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin: 0;
}

/* 动效契约支持 */
@media (prefers-reduced-motion: reduce) {
  .stat-icon-wrap {
    animation: none !important;
  }
}

:global(body.motion-disabled) .stat-icon-wrap,
:global([data-motion="disabled"]) .stat-icon-wrap {
  animation: none !important;
}

:global(body.page-hidden) .stat-icon-wrap {
  animation-play-state: paused !important;
}
</style>
