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
  (e: 'openRequirementSection', id: string, section: 'completion' | 'test_points'): void
  (e: 'navigate', navIndex: number): void
}>()

const isDemoMode = computed(() => Boolean(props.isDemo))

const monthlyTasksCountText = computed(() => {
  const count = props.tasks?.length ?? 0
  return isDemoMode.value ? `示例预览 · ${count} 项` : `${count} 项`
})

function statusChipClass(status?: string | null): string {
  if (status === 'ok' || status === '已完成') return 'ok'
  if (status === 'rev' || status === '待评审' || status === '测试中') return 'rev'
  return 'run'
}

function isRealTask(item?: { id?: string | null; is_demo?: boolean | null } | null): boolean {
  if (isDemoMode.value || !item || !item.id) return false
  const idStr = String(item.id).trim()
  if (!idStr || idStr.toLowerCase().startsWith('demo-') || item.is_demo) return false
  return true
}

function isDemoRow(item?: { id?: string | null; is_demo?: boolean | null } | null): boolean {
  if (isDemoMode.value || item?.is_demo) return true
  if (item?.id && String(item.id).trim().toLowerCase().startsWith('demo-')) return true
  return false
}

function onOpenRequirement(reqId?: string | null): void {
  if (!reqId) return
  const idStr = String(reqId).trim()
  if (!idStr || idStr.toLowerCase().startsWith('demo-') || isDemoMode.value) return
  emit('openRequirement', idStr)
}

function onOpenSection(reqId: string | null | undefined, section: 'completion' | 'test_points'): void {
  if (!reqId) return
  const idStr = String(reqId).trim()
  if (!idStr || idStr.toLowerCase().startsWith('demo-') || isDemoMode.value) return
  emit('openRequirementSection', idStr, section)
}

function onRowClick(event: MouseEvent, task: MonthlyReleaseTask): void {
  if (!isRealTask(task)) return
  const target = event.target as HTMLElement | null
  if (target && target.closest('button')) {
    return
  }
  onOpenRequirement(task.id)
}

function onRowKeydown(task: MonthlyReleaseTask): void {
  if (!isRealTask(task)) return
  onOpenRequirement(task.id)
}
</script>

<template>
  <div class="tasks-container">
    <!-- 首页唯一任务区；recent DTO保留兼容，但不再重复显示旧需求卡片。 -->
    <section class="card pad task-section-card" data-testid="monthly-release-tasks">
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

      <div
        v-if="isDemoMode"
        class="demo-notice"
        role="status"
        data-testid="demo-preview-notice"
      >
        <div class="demo-notice-copy">
          <strong>当前是示例预览</strong>
          <span>这些示例不代表你的真实任务，也不能直接编辑。请前往需求管理导入已有文件夹或新建需求；首页本月列表只显示设置了本月实际上线日期的真实需求。</span>
        </div>
        <button
          class="btn btn-ghost btn-xs demo-manage-btn"
          data-action="manage-requirements"
          data-testid="demo-manage-requirements"
          type="button"
          @click="emit('navigate', 10)"
        >
          去管理真实需求
        </button>
      </div>

      <div class="req-list">
        <template v-if="props.tasks && props.tasks.length > 0">
          <div
            v-for="(task, idx) in props.tasks"
            :key="task.id || task.code || task.title || idx"
            class="ck task-row"
            :class="{ 'clickable': isRealTask(task), 'is-demo': !isRealTask(task) }"
            :tabindex="isRealTask(task) ? 0 : undefined"
            :role="isRealTask(task) ? 'button' : undefined"
            @click="onRowClick($event, task)"
            @keydown.enter.self="onRowKeydown(task)"
            @keydown.space.self.prevent="onRowKeydown(task)"
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

            <div v-if="isRealTask(task)" class="row-actions">
              <button
                class="btn btn-ghost btn-xs row-act-btn"
                data-action="view"
                type="button"
                @click.stop="onOpenRequirement(task.id)"
              >
                查看
              </button>
              <button
                class="btn btn-ghost btn-xs row-act-btn"
                data-action="completion"
                type="button"
                @click.stop="onOpenSection(task.id, 'completion')"
              >
                编辑状态
              </button>
              <button
                class="btn btn-ghost btn-xs row-act-btn"
                data-action="test_points"
                type="button"
                @click.stop="onOpenSection(task.id, 'test_points')"
              >
                测试点
              </button>
            </div>
            <span v-else-if="isDemoRow(task)" class="demo-badge">示例</span>
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
  font: 11px Consolas, monospace;
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
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  display: block;
}

.rs {
  font-size: 13px;
  color: var(--text-muted);
  margin-top: 1px;
}

.dchip {
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  background: var(--primary-soft);
  color: var(--primary);
}

.demo-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 12px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-soft);
}

.demo-notice-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.demo-notice-copy strong {
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
}

.demo-notice-copy span {
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.45;
}

.demo-manage-btn {
  flex-shrink: 0;
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
  font-size: 14px;
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
  font-size: 14px;
  font-weight: 500;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta-inline {
  font-size: 13px;
  color: var(--text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.test-points-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.chip {
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
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

.row-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.btn-xs {
  padding: 3px 8px;
  font-size: 13px;
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
  font-size: 12px;
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--surface-soft);
  color: var(--text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.empty-hint {
  padding: 18px 8px;
  font-size: 13px;
  color: var(--text-muted);
  text-align: center;
}
</style>
