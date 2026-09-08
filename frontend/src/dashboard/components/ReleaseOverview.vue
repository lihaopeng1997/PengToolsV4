<script setup lang="ts">
import { computed } from 'vue'
import type { DashboardRelease, DashboardStats, MonthlyReleaseTask } from '../types'
import PrismIcon from '../../shared/components/PrismIcon.vue'

const props = defineProps<{
  release?: DashboardRelease | null
  stats?: DashboardStats | null
  tasks?: MonthlyReleaseTask[] | null
  isDemo?: boolean | null
}>()

const emit = defineEmits<{
  (e: 'navigate', navIndex: number): void
}>()

const totalCount = computed(() => {
  return props.release?.total ?? props.stats?.monthly_release_total ?? props.tasks?.length ?? 0
})

const doneCount = computed(() => {
  return props.release?.done ?? props.stats?.monthly_release_done ?? props.tasks?.filter(t => t.done).length ?? 0
})

const pendingCount = computed(() => {
  return Math.max(0, totalCount.value - doneCount.value)
})

const percent = computed(() => {
  if (props.release?.percent != null) {
    return Math.min(100, Math.max(0, props.release.percent))
  }
  if (totalCount.value > 0) {
    return Math.round((doneCount.value / totalCount.value) * 100)
  }
  return 0
})

const ringStyle = computed(() => {
  const p = percent.value
  return {
    background: `conic-gradient(var(--primary, #6c58d9) ${p}%, var(--primary-soft, #eae5f5) 0)`
  }
})

const monthLabel = computed(() => {
  const d = new Date()
  const months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
  return `${months[d.getMonth()]} / ${d.getFullYear()}`
})
</script>

<template>
  <section class="card pad release-overview">
    <div class="card-head">
      <h2 class="card-title">上线总览</h2>
      <small class="month-label">{{ monthLabel }}</small>
    </div>

    <!-- 环形进度与概览 -->
    <div class="ring-row">
      <div class="ring" :style="ringStyle" role="img" :aria-label="`月度完成 ${percent}%`">
        <div class="ring-center">
          <strong class="ring-num">{{ percent }}%</strong>
          <small class="ring-sub">RELEASE</small>
        </div>
      </div>

      <div class="ring-info">
        <h3 class="info-title">每一步，都算数。</h3>
        <p class="info-hint">已完成 {{ doneCount }} / {{ totalCount }} 项</p>
        <span class="info-tag">{{ pendingCount }} 项正在推进</span>
      </div>
    </div>

    <!-- 关键里程碑检查 -->
    <div class="milestones">
      <div class="milestone">
        <span class="m-left">
          <PrismIcon name="check" :size="14" class="m-ico success" />
          <span>测试点已通过</span>
        </span>
        <span class="m-val">{{ doneCount }} 项</span>
      </div>

      <div class="milestone">
        <span class="m-left">
          <PrismIcon name="clock" :size="14" class="m-ico warning" />
          <span>待上线确认</span>
        </span>
        <span class="m-val">{{ Math.max(0, Math.floor(pendingCount / 2)) }} 项</span>
      </div>

      <div class="milestone">
        <span class="m-left">
          <PrismIcon name="activity" :size="14" class="m-ico primary" />
          <span>正在测试验证</span>
        </span>
        <span class="m-val">{{ Math.ceil(pendingCount / 2) }} 项</span>
      </div>
    </div>

    <!-- 底部操作与提示 -->
    <div class="card-foot">
      <span class="foot-hint">{{ props.isDemo ? '统计均为示例' : '月度真实发版' }}</span>
      <button class="foot-btn" type="button" @click="emit('navigate', 10)">
        <span>进入台账</span>
        <PrismIcon name="arrow" :size="13" />
      </button>
    </div>
  </section>
</template>

<style scoped>
.release-overview {
  height: 300px;
  min-height: 300px;
  background: var(--surface, #fdfdff);
  border: 1px solid var(--border, rgba(119, 123, 163, 0.15));
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 4px 16px rgba(132, 128, 173, 0.04);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  box-sizing: border-box;
}

:global([data-theme="black"]) .release-overview,
:global(body.theme-black) .release-overview {
  background: var(--surface, #1e1e2b);
  border-color: var(--border, rgba(255, 255, 255, 0.08));
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text, #28324a);
  margin: 0;
}

.month-label {
  font: 9px Consolas, monospace;
  letter-spacing: 1px;
  color: var(--muted, #7b8499);
}

.ring-row {
  display: flex;
  align-items: center;
  gap: 16px;
  margin: 6px 0;
}

.ring {
  width: 90px;
  height: 90px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  position: relative;
  flex-shrink: 0;
}

.ring-center {
  width: 76px;
  height: 76px;
  border-radius: 50%;
  background: var(--surface, #faf9fe);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 1;
}

:global([data-theme="black"]) .ring-center,
:global(body.theme-black) .ring-center {
  background: var(--surface, #1e1e2b);
}

.ring-num {
  font-size: 20px;
  font-weight: 600;
  color: var(--text, #28324a);
  letter-spacing: -0.5px;
  line-height: 1.1;
}

.ring-sub {
  font: 8px Consolas, monospace;
  letter-spacing: 0.5px;
  color: var(--muted, #7b8499);
}

.ring-info {
  flex: 1;
  min-width: 0;
}

.info-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text, #28324a);
  margin: 0 0 4px 0;
}

.info-hint {
  font-size: 11px;
  color: var(--muted, #7b8499);
  margin: 0 0 6px 0;
}

.info-tag {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 4px;
  font-size: 10px;
  background: var(--primary-soft, rgba(108, 88, 217, 0.1));
  color: var(--primary, #6c58d9);
}

.milestones {
  display: flex;
  flex-direction: column;
  gap: 7px;
  border-top: 1px solid var(--border, rgba(119, 123, 163, 0.08));
  padding-top: 8px;
}

.milestone {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text, #28324a);
}

.m-left {
  display: flex;
  align-items: center;
  gap: 7px;
}

.m-ico.success {
  color: var(--success, #247f75);
}
.m-ico.warning {
  color: var(--warning, #a5772a);
}
.m-ico.primary {
  color: var(--primary, #6c58d9);
}

.m-val {
  font-size: 11px;
  color: var(--muted, #7b8499);
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--border, rgba(119, 123, 163, 0.08));
  padding-top: 8px;
}

.foot-hint {
  font-size: 10px;
  color: var(--muted, #7b8499);
}

.foot-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--primary, #6c58d9);
  background: transparent;
  border: 0;
  cursor: pointer;
  padding: 3px 6px;
  border-radius: 4px;
  transition: background 0.15s ease;
}

.foot-btn:hover {
  background: var(--primary-soft, rgba(108, 88, 217, 0.1));
}
</style>
