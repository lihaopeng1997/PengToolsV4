<script setup lang="ts">
import { computed } from 'vue'
import type { DashboardToolItem } from '../types'
import PrismIcon from '../../shared/components/PrismIcon.vue'

const props = defineProps<{
  tools?: DashboardToolItem[] | null
}>()

const emit = defineEmits<{
  (e: 'navigate', navIndex: number): void
}>()

const displayTools = computed<DashboardToolItem[]>(() => {
  return props.tools || []
})
</script>

<template>
  <section class="card pad quick-tools-card">
    <div class="card-head">
      <h2 class="card-title">常用工具</h2>
      <small class="card-sub">QUICK ACCESS</small>
    </div>

    <!-- 2 列网格，每卡 64px 高度；保留 .tool 供测试与运行态选择器识别 -->
    <div v-if="displayTools.length > 0" class="quick-grid">
      <button
        v-for="tool in displayTools"
        :key="tool.i"
        class="quick-btn tool"
        type="button"
        @click="emit('navigate', tool.i)"
      >
        <span class="quick-icon-wrap">
          <PrismIcon :name="tool.icon || 'db'" :size="18" />
        </span>
        <span class="quick-text">
          <strong class="quick-name">{{ tool.zh }}</strong>
          <small class="quick-sub">{{ tool.ds || '快速直达' }}</small>
        </span>
      </button>
    </div>
    <div v-else class="empty-tools-hint">
      暂无常用工具
    </div>
  </section>
</template>

<style scoped>
.quick-tools-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: rgba(253, 253, 255, 0.92);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 16px 18px 16px;
  box-shadow: 0 4px 16px rgba(38, 36, 56, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.92);
  box-sizing: border-box;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.card-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-strong);
  margin: 0;
}

.card-sub {
  font: 11px Consolas, monospace;
  letter-spacing: 1px;
  color: var(--text-muted);
}

.quick-grid {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  align-content: stretch;
}

.quick-btn {
  min-width: 0;
  height: auto;
  min-height: 74px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: rgba(246, 244, 252, 0.72);
  border: 1px solid rgba(216, 211, 234, 0.75);
  border-radius: 12px;
  text-align: left;
  cursor: pointer;
  box-sizing: border-box;
  will-change: transform;
  transition: transform 200ms cubic-bezier(0.22, 1, 0.36, 1), box-shadow 200ms cubic-bezier(0.22, 1, 0.36, 1), border-color 180ms ease, background 180ms ease;
}

.quick-btn:hover,
.quick-btn:focus-visible {
  transform: translateY(-2px);
  background: var(--surface);
  border-color: var(--primary);
  box-shadow: 0 8px 18px rgba(108, 88, 217, 0.14);
}

.quick-btn:hover .quick-icon-wrap {
  transform: scale(1.08);
  background: var(--primary);
  color: var(--on-primary);
}

.quick-btn:active {
  transform: scale(0.97);
}

.quick-btn:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}

.quick-icon-wrap {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  background: var(--primary-soft);
  color: var(--primary);
  flex-shrink: 0;
  transition: transform 200ms cubic-bezier(0.22, 1, 0.36, 1), background 180ms ease, color 180ms ease;
}

.quick-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.quick-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.quick-sub {
  font-size: 12px;
  color: var(--text-muted);
  white-space: normal;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 2px;
}

/* 动效契约支持 */
@media (prefers-reduced-motion: reduce) {
  .quick-btn {
    transition: none !important;
  }
  .quick-btn:hover,
  .quick-btn:focus-visible {
    transform: none !important;
  }
}

:global(body.motion-disabled .quick-btn),
:global([data-motion="disabled"] .quick-btn){
  transition: none !important;
}
:global(body.motion-disabled .quick-btn:hover),
:global([data-motion="disabled"] .quick-btn:hover){
  transform: none !important;
}
</style>
