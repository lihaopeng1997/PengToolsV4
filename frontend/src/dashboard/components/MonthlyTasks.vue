<script setup lang="ts">
import { computed } from 'vue'
import type { MonthlyReleaseTask, DashboardRecentItem } from '../types'
import PrismIcon from '../../shared/components/PrismIcon.vue'

const props = defineProps<{
  tasks?: MonthlyReleaseTask[] | null
  recent?: DashboardRecentItem[] | null
  isDemo?: boolean | null
}>()

const emit = defineEmits<{
  (e: 'openRequirement', id?: string | null): void
  (e: 'navigate', navIndex: number): void
}>()

const isDemoMode = computed(() => Boolean(props.isDemo))

const monthlyTasksCountText = computed(() => {
  const count = props.tasks?.length ?? 0
  return `${count} 项`
})

function statusChipClass(status?: string | null): string {
  if (status === 'ok' || status === '已完成') return 'ok'
  if (status === 'rev' || status === '待评审' || status === '测试中') return 'rev'
  return 'run'
}

function statusLabel(status?: string | null): string {
  if (!status) return '推进中'
  if (status === 'ok') return '已完成'
  if (status === 'rev') return '测试中'
  if (status === 'run') return '推进中'
  return status
}

function isRowInteractive(item?: { is_demo?: boolean | null } | null): boolean {
  return !isDemoMode.value && !item?.is_demo
}

function onOpenRequirement(reqId?: string | null, _fallbackNav = 10): void {
  if (isDemoMode.value || !reqId || String(reqId).startsWith('demo-')) {
    return
  }
  emit('openRequirement', reqId)
}
</script>

<template>
  <div class="tasks-container">
    <!-- 卡片 1: 最近需求 (Card 1) -->
    <section class="card pad task-section-card">
      <div class="ph">
        <div class="ph-left">
          <span class="tt">最近需求</span>
          <span class="sub">{{ isDemoMode ? '示例数据 · DEMO' : 'RECENT' }}</span>
        </div>
        <button class="btn btn-ghost btn-xs" type="button" @click="emit('navigate', 10)">
          进入需求台账
        </button>
      </div>

      <div class="req-list">
        <template v-if="props.recent && props.recent.length > 0">
          <div
            v-for="(r, idx) in props.recent"
            :key="r.id || r.code || idx"
            class="ck task-row"
            :class="{ 'clickable': isRowInteractive(r), 'is-demo': !isRowInteractive(r) }"
            :tabindex="isRowInteractive(r) ? 0 : undefined"
            :role="isRowInteractive(r) ? 'button' : undefined"
            @click="isRowInteractive(r) && onOpenRequirement(r.id)"
            @keydown.enter.self="isRowInteractive(r) && onOpenRequirement(r.id)"
            @keydown.space.self.prevent="isRowInteractive(r) && onOpenRequirement(r.id)"
          >
            <span class="dot" :class="statusChipClass(r.status)" />
            <span v-if="r.code" class="req-id-badge" :title="r.code">{{ r.code }}</span>
            <span class="t">
              <span class="req-title">{{ r.title || '未命名需求' }}</span>
              <span v-if="r.system" class="meta-inline"> · {{ r.system }}</span>
              <span v-if="r.actual_release_date" class="meta-inline"> · 发版: {{ r.actual_release_date }}</span>
            </span>

            <span v-if="r.test_points" class="test-points-badge" :title="'测试点: ' + r.test_points">
              <PrismIcon name="check" :size="12" />
              <span>{{ r.test_points }}</span>
            </span>

            <span class="chip" :class="statusChipClass(r.status)">
              {{ r.status_label || statusLabel(r.status) }}
            </span>

            <button
              v-if="isRowInteractive(r)"
              class="btn btn-ghost btn-xs row-act-btn"
              type="button"
              @click.stop="emit('openRequirement', r.id)"
            >
              查看
            </button>
            <span v-else class="demo-badge">示例</span>
          </div>
        </template>
        <div v-else class="empty-hint">暂无需求记录</div>
      </div>
    </section>

    <!-- 卡片 2: 本月上线任务 (Card 2) -->
    <section class="card pad task-section-card">
      <div class="ph">
        <div class="ph-left">
          <span class="tt">本月上线任务</span>
          <span class="sub">{{ isDemoMode ? '示例数据 · DEMO' : 'MONTHLY RELEASE' }}</span>
        </div>
        <button class="btn btn-ghost btn-xs" type="button" @click="emit('navigate', 10)">
          查看全部
        </button>
      </div>

      <div class="rel">
        <div class="ric"><PrismIcon name="rocket" :size="16" /></div>
        <div class="rel-info">
          <b>本月实际上线发版</b>
          <div class="rs">{{ isDemoMode ? '当前为示例数据展示' : '按实际发版日期归档' }}</div>
        </div>
        <span class="dchip">{{ monthlyTasksCountText }}</span>
      </div>

      <div class="req-list">
        <template v-if="props.tasks && props.tasks.length > 0">
          <div
            v-for="(task, idx) in props.tasks"
            :key="task.id || task.code || task.title || idx"
            class="ck task-row"
            :class="{ 'clickable': isRowInteractive(task), 'is-demo': !isRowInteractive(task) }"
            :tabindex="isRowInteractive(task) ? 0 : undefined"
            :role="isRowInteractive(task) ? 'button' : undefined"
            @click="isRowInteractive(task) && onOpenRequirement(task.id)"
            @keydown.enter.self="isRowInteractive(task) && onOpenRequirement(task.id)"
            @keydown.space.self.prevent="isRowInteractive(task) && onOpenRequirement(task.id)"
          >
            <span class="dot" :class="task.done ? 'ok' : statusChipClass(task.status)" />
            <span v-if="task.code" class="req-id-badge" :title="task.code">{{ task.code }}</span>
            <span class="t">
              <span class="req-title">{{ task.title }}</span>
              <span v-if="task.system" class="meta-inline"> · {{ task.system }}</span>
              <span v-if="task.actual_release_date" class="meta-inline"> · 发版: {{ task.actual_release_date }}</span>
            </span>

            <span v-if="task.test_points" class="test-points-badge" :title="'测试点: ' + task.test_points">
              <PrismIcon name="check" :size="12" />
              <span>{{ task.test_points }}</span>
            </span>

            <span class="chip" :class="task.done ? 'ok' : statusChipClass(task.status)">
              {{ task.done ? '已完成' : (task.status || '推进中') }}
            </span>

            <button
              v-if="isRowInteractive(task)"
              class="btn btn-ghost btn-xs row-act-btn"
              type="button"
              @click.stop="emit('openRequirement', task.id)"
            >
              查看
            </button>
            <span v-else class="demo-badge">示例</span>
          </div>
        </template>
        <div v-else class="empty-hint">本月暂无实际上线任务</div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.tasks-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.task-section-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 2px 8px var(--shadow-l1);
  box-sizing: border-box;
}

.ph {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.ph-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.tt {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.sub {
  font: 9px Consolas, monospace;
  letter-spacing: 1px;
  color: var(--text-muted);
}

.rel {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: 10px;
  margin-bottom: 12px;
}

.ric {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: var(--primary-soft);
  color: var(--primary);
  flex-shrink: 0;
}

.rel-info {
  flex: 1;
  min-width: 0;
}

.rel-info b {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  display: block;
}

.rs {
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 1px;
}

.dchip {
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  background: var(--primary-soft);
  color: var(--primary);
}

.req-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 240px;
  overflow-y: auto;
}

.ck {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 7px 10px;
  border-radius: 8px;
  border-bottom: 1px solid var(--border);
  background: transparent;
  transition: background 0.15s ease;
  box-sizing: border-box;
}

.ck.clickable:hover,
.ck:not(.is-demo):hover {
  background: var(--primary-soft);
  cursor: pointer;
}

.ck.is-demo:hover {
  background: transparent;
  cursor: default;
}

.ck:focus-visible:not(.is-demo) {
  outline: 2px solid var(--primary);
  outline-offset: 1px;
}

.ck.is-demo {
  opacity: 0.85;
  cursor: default;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot.run { background: var(--warning); }
.dot.rev { background: var(--accent-cyan, var(--cyan)); }
.dot.ok { background: var(--success); }

.req-id-badge {
  font-family: Consolas, monospace;
  font-size: 12px;
  font-weight: 650;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--surface-soft);
  color: var(--primary);
  white-space: nowrap;
  flex-shrink: 0;
}

.t {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
}

.req-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta-inline {
  font-size: 10px;
  color: var(--text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.test-points-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: var(--text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.chip {
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 10px;
  white-space: nowrap;
  flex-shrink: 0;
}
.chip.run {
  background: var(--primary-soft);
  color: var(--primary);
}
.chip.rev {
  background: var(--status-warning-bg, var(--primary-soft));
  color: var(--warning);
}
.chip.ok {
  background: var(--status-success-bg, var(--primary-soft));
  color: var(--success);
}

.btn-xs {
  padding: 3px 8px;
  font-size: 10px;
  border-radius: 4px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--primary);
  cursor: pointer;
  flex-shrink: 0;
}
.btn-xs:hover {
  background: var(--primary-soft);
}

.demo-badge {
  font-size: 9px;
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--surface-soft);
  color: var(--text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.empty-hint {
  padding: 18px 8px;
  font-size: 11px;
  color: var(--text-muted);
  text-align: center;
}
</style>
