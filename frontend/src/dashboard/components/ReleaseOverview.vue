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
    background: `conic-gradient(var(--primary) ${p}%, var(--primary-soft) 0)`
  }
})

const monthLabel = computed(() => {
  const d = new Date()
  const months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
  return `${months[d.getMonth()]} / ${d.getFullYear()}`
})
const milestoneStats = computed(() => {
  let passedTestPoints = 0
  let totalTestPoints = 0
  let pendingConfirmCount = 0
  let testingCount = 0

  const tasks = props.tasks || []
  for (const task of tasks) {
    let p = 0
    let t = 0
    let hasPoints = false
    if (task.test_points) {
      const parts = task.test_points.split('/')
      if (parts.length === 2) {
        p = parseInt(parts[0], 10) || 0
        t = parseInt(parts[1], 10) || 0
        hasPoints = true
      }
    }
    if (hasPoints) {
      passedTestPoints += p
      totalTestPoints += t
    }

    if (!task.done) {
      const status = String(task.status || '').trim()
      const allPassed = hasPoints && t > 0 && p >= t
      const isPendingConfirm =
        allPassed ||
        status === '待上线' ||
        status === '今日上线' ||
        status === '待确认' ||
        status.includes('待确认') ||
        status.includes('待上线')

      const isTestingVerification =
        !isPendingConfirm &&
        (status.includes('测试') ||
         status.includes('开发') ||
         status.includes('进行中') ||
         status.includes('待评审') ||
         status === 'rev' ||
         status === 'run')

      if (isPendingConfirm) {
        pendingConfirmCount++
      } else if (isTestingVerification) {
        testingCount++
      }
    }
  }

  const passedText = totalTestPoints > 0
    ? `${passedTestPoints} / ${totalTestPoints}`
    : `${passedTestPoints} 项`

  return {
    passedTestPoints,
    totalTestPoints,
    passedText,
    pendingConfirmCount,
    testingCount
  }
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
        <span class="m-val">{{ milestoneStats.passedText }}</span>
      </div>

      <div class="milestone">
        <span class="m-left">
          <PrismIcon name="clock" :size="14" class="m-ico warning" />
          <span>待上线确认</span>
        </span>
        <span class="m-val">{{ milestoneStats.pendingConfirmCount }} 项</span>
      </div>

      <div class="milestone">
        <span class="m-left">
          <PrismIcon name="activity" :size="14" class="m-ico primary" />
          <span>正在测试验证</span>
        </span>
        <span class="m-val">{{ milestoneStats.testingCount }} 项</span>
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
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 4px 16px var(--shadow-l1);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  box-sizing: border-box;
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
  color: var(--text);
  margin: 0;
}

.month-label {
  font: 9px Consolas, monospace;
  letter-spacing: 1px;
  color: var(--text-muted);
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
  background: var(--surface);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 1;
}

.ring-num {
  font-size: 20px;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.5px;
  line-height: 1.1;
}

.ring-sub {
  font: 8px Consolas, monospace;
  letter-spacing: 0.5px;
  color: var(--text-muted);
}

.ring-info {
  flex: 1;
  min-width: 0;
}

.info-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin: 0 0 4px 0;
}

.info-hint {
  font-size: 11px;
  color: var(--text-muted);
  margin: 0 0 6px 0;
}

.info-tag {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 4px;
  font-size: 10px;
  background: var(--primary-soft);
  color: var(--primary);
}

.milestones {
  display: flex;
  flex-direction: column;
  gap: 7px;
  border-top: 1px solid var(--border);
  padding-top: 8px;
}

.milestone {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text);
}

.m-left {
  display: flex;
  align-items: center;
  gap: 7px;
}

.m-ico.success {
  color: var(--success);
}
.m-ico.warning {
  color: var(--warning);
}
.m-ico.primary {
  color: var(--primary);
}

.m-val {
  font-size: 11px;
  color: var(--text-muted);
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--border);
  padding-top: 8px;
}

.foot-hint {
  font-size: 10px;
  color: var(--text-muted);
}

.foot-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--primary);
  background: transparent;
  border: 0;
  cursor: pointer;
  padding: 3px 6px;
  border-radius: 4px;
  transition: background 0.15s ease;
}

.foot-btn:hover {
  background: var(--primary-soft);
}
</style>
