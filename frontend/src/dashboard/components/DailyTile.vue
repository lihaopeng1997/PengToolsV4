<script setup lang="ts">
/**
 * 便当格 · 本周日报：圆环显示已写天数，今天没写时变成提醒色；点一下去写。
 */
import { computed } from 'vue'
import type { DashboardStats } from '../types'

const props = defineProps<{
  stats?: DashboardStats | null
}>()

const emit = defineEmits<{
  (e: 'writeDaily'): void
}>()

const done = computed(() => props.stats?.daily_done ?? 0)
const total = computed(() => Math.max(1, props.stats?.daily_total ?? 5))
const pending = computed(() => props.stats?.daily_note === '今日未填写')
const R = 26
const C = 2 * Math.PI * R
const dash = computed(() => `${(Math.min(done.value, total.value) / total.value) * C} ${C}`)
</script>

<template>
  <button type="button" class="tile daily" :class="{ pending }" data-testid="daily-tile" @click="emit('writeDaily')">
    <svg class="ring" viewBox="0 0 64 64" aria-hidden="true">
      <circle cx="32" cy="32" :r="R" class="ring-track" />
      <circle cx="32" cy="32" :r="R" class="ring-fill" :stroke-dasharray="dash" />
      <text x="32" y="37" text-anchor="middle" class="ring-text">{{ done }}/{{ total }}</text>
    </svg>
    <span class="daily-text">
      <strong>本周日报</strong>
      <small>{{ pending ? '今天还没写，点我去写' : (props.stats?.daily_note || '今天已写') }}</small>
    </span>
  </button>
</template>

<style scoped>
.daily {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  text-align: left;
  cursor: pointer;
}

.ring {
  width: 64px;
  height: 64px;
  flex: none;
  transform: rotate(-90deg);
}

.ring-track {
  fill: none;
  stroke: var(--surface-soft);
  stroke-width: 7;
}

.ring-fill {
  fill: none;
  stroke: var(--primary);
  stroke-width: 7;
  stroke-linecap: round;
  transition: stroke-dasharray 520ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

.daily.pending .ring-fill {
  stroke: var(--warning);
}

.ring-text {
  transform: rotate(90deg);
  transform-origin: 32px 32px;
  font-size: 13px;
  font-weight: 800;
  fill: var(--text-strong);
}

.daily-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.daily-text strong {
  font-size: 14px;
  color: var(--text-strong);
}

.daily-text small {
  font-size: 12px;
  color: var(--text-muted);
}

.daily.pending .daily-text small {
  color: var(--warning);
}
</style>
