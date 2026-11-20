<script setup lang="ts">
/**
 * 便当格 · 阶段进度：每个阶段一根横条（长度 = 数量占比），点击在「需要关注」里只看该阶段。
 */
import { computed } from 'vue'
import { STAGE_DEFS, type DashboardProgress, type StageKey } from '../types'

const props = defineProps<{
  progress?: DashboardProgress | null
  focus?: StageKey | null
}>()

const emit = defineEmits<{
  (e: 'focusStage', key: StageKey | null): void
}>()

const rows = computed(() => {
  const count = (key: string) => key === 'online'
    ? props.progress?.online_month ?? 0
    : props.progress?.stages?.find((stage) => stage.key === key)?.count ?? 0
  const list = STAGE_DEFS.map((stage) => ({ ...stage, count: count(stage.key) }))
  const max = Math.max(1, ...list.map((row) => row.count))
  return list.map((row) => ({ ...row, width: row.count ? Math.max(6, Math.round((row.count / max) * 100)) : 0 }))
})

const total = computed(() => rows.value.filter((r) => r.key !== 'online').reduce((sum, r) => sum + r.count, 0))

function pick(key: StageKey): void {
  emit('focusStage', props.focus === key ? null : key)
}
</script>

<template>
  <section class="tile stages" data-testid="stage-tile">
    <header class="tile-head">
      <h3>阶段进度</h3>
      <span class="tile-sub">{{ total }} 个在推进</span>
    </header>
    <ul class="bars">
      <li v-for="row in rows" :key="row.key">
        <button
          type="button"
          class="bar-row"
          :class="{ on: props.focus === row.key, dim: props.focus && props.focus !== row.key }"
          :style="{ '--stage': row.color, '--stage-bg': row.bg }"
          :data-stage="row.key"
          @click="pick(row.key)"
        >
          <span class="bar-label"><i class="stage-dot" />{{ row.label }}</span>
          <span class="bar-track"><span class="bar-fill" :style="{ width: `${row.width}%` }" /></span>
          <span class="bar-count">{{ row.count }}</span>
        </button>
      </li>
    </ul>
    <p class="stages-foot">{{ props.focus ? '再点一次取消筛选' : '点阶段，只看它的需求' }}</p>
  </section>
</template>

<style scoped>
.stages {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.bars {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bar-row {
  width: 100%;
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) 32px;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  border-radius: 10px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  transition: background 160ms ease, border-color 160ms ease, transform 160ms cubic-bezier(0.22, 1, 0.36, 1), opacity 140ms ease;
}

.bar-row:hover,
.bar-row.on {
  background: var(--stage-bg, var(--surface-soft));
  border-color: var(--stage);
  transform: translateX(2px);
}

.bar-row.dim {
  opacity: 0.45;
}

.bar-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  text-align: left;
  font-size: 12px;
  font-weight: 700;
  color: var(--text-strong);
}

.stage-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--stage);
  flex-shrink: 0;
}

.bar-track {
  height: 9px;
  border-radius: 999px;
  background: var(--surface-soft);
  overflow: hidden;
}

.bar-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--stage);
  transition: width 420ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

.bar-count {
  text-align: center;
  font-size: 12px;
  font-weight: 800;
  color: var(--stage);
  background: var(--stage-bg, var(--surface-soft));
  border-radius: 999px;
  padding: 1px 6px;
  font-variant-numeric: tabular-nums;
}

.stages-foot {
  margin: auto 0 0;
  font-size: 11px;
  color: var(--text-muted);
}
</style>
