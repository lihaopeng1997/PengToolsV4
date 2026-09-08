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
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 2px 8px var(--shadow-l1);
  box-sizing: border-box;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}

.card-sub {
  font: 9px Consolas, monospace;
  letter-spacing: 1px;
  color: var(--text-muted);
}

.quick-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.quick-btn {
  height: 64px;
  min-height: 64px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: 10px;
  text-align: left;
  cursor: pointer;
  box-sizing: border-box;
  will-change: transform;
  /* 动效契约：hover/focus 上浮 2px、旋转 -6 度、200ms */
  transition: transform 200ms ease, box-shadow 200ms ease, border-color 200ms ease;
}

.quick-btn:hover,
.quick-btn:focus-visible {
  transform: translateY(-2px) rotate(-6deg);
  border-color: var(--primary);
  box-shadow: 0 4px 14px var(--shadow-l2);
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
  border-radius: 8px;
  background: var(--primary-soft);
  color: var(--primary);
  flex-shrink: 0;
}

.quick-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.quick-name {
  font-size: 11px;
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.quick-sub {
  font-size: 9px;
  color: var(--text-muted);
  white-space: nowrap;
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

:global(body.motion-disabled) .quick-btn,
:global([data-motion="disabled"]) .quick-btn {
  transition: none !important;
}
:global(body.motion-disabled) .quick-btn:hover,
:global([data-motion="disabled"]) .quick-btn:hover {
  transform: none !important;
}
</style>
