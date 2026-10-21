<script setup lang="ts">
/**
 * 便当格 · 本月上线日历：按天的热力格。颜色深浅 = 当天计划/实际上线的需求数，
 * 今天加描边；悬停看当天需求，点击打开第一条。
 */
import { computed, ref } from 'vue'
import type { DashboardBoardItem, MonthlyReleaseTask } from '../types'

const props = defineProps<{
  board?: DashboardBoardItem[] | null
  tasks?: MonthlyReleaseTask[] | null
}>()

const emit = defineEmits<{
  (e: 'openRequirement', id: string): void
}>()

type DayItem = { id: string; title: string; done: boolean }

const today = new Date()
const year = today.getFullYear()
const month = today.getMonth()
const monthKey = `${year}-${String(month + 1).padStart(2, '0')}`
const hoveredDay = ref<number | null>(null)

const byDay = computed(() => {
  const map = new Map<number, DayItem[]>()
  const seen = new Set<string>()
  const add = (dateText: string | null | undefined, id: string, title: string, done: boolean) => {
    const text = String(dateText || '')
    if (!text.startsWith(monthKey) || !id || seen.has(`${id}@${text}`)) return
    seen.add(`${id}@${text}`)
    const day = Number(text.slice(8, 10))
    if (!day) return
    const list = map.get(day) ?? []
    list.push({ id, title, done })
    map.set(day, list)
  }
  for (const task of props.tasks ?? []) {
    add(task.actual_online_date || task.actual_release_date, String(task.id || ''), String(task.title || task.code || ''), Boolean(task.done))
  }
  for (const item of props.board ?? []) {
    add(item.actual_release_date, String(item.id || ''), String(item.title || item.code || ''), item.group === 'online')
  }
  return map
})

const cells = computed(() => {
  const first = new Date(year, month, 1)
  const offset = (first.getDay() + 6) % 7 // 周一开头
  const days = new Date(year, month + 1, 0).getDate()
  const list: Array<{ day: number | null; items: DayItem[]; today: boolean; past: boolean }> = []
  for (let i = 0; i < offset; i++) list.push({ day: null, items: [], today: false, past: false })
  for (let d = 1; d <= days; d++) {
    list.push({ day: d, items: byDay.value.get(d) ?? [], today: d === today.getDate(), past: d < today.getDate() })
  }
  return list
})

const totalThisMonth = computed(() => [...byDay.value.values()].reduce((sum, list) => sum + list.length, 0))
const upcoming = computed(() => {
  const days = [...byDay.value.keys()].filter((d) => d >= today.getDate()).sort((a, b) => a - b)
  return days.length ? days[0] : null
})

const hoverItems = computed(() => (hoveredDay.value ? byDay.value.get(hoveredDay.value) ?? [] : []))

function level(count: number): string {
  if (!count) return 'l0'
  if (count === 1) return 'l1'
  if (count === 2) return 'l2'
  return 'l3'
}

function open(items: DayItem[]): void {
  if (items.length) emit('openRequirement', items[0].id)
}
</script>

<template>
  <section class="tile calendar" data-testid="calendar-tile">
    <header class="tile-head">
      <h3>本月上线</h3>
      <span class="tile-sub">{{ month + 1 }} 月 · {{ totalThisMonth }} 项</span>
    </header>
    <div class="weekdays" aria-hidden="true">
      <span v-for="w in ['一', '二', '三', '四', '五', '六', '日']" :key="w">{{ w }}</span>
    </div>
    <div class="grid">
      <button
        v-for="(cell, index) in cells"
        :key="index"
        type="button"
        class="cell"
        :class="[level(cell.items.length), { blank: !cell.day, today: cell.today, past: cell.past, done: cell.items.length && cell.items.every((i) => i.done) }]"
        :disabled="!cell.day || !cell.items.length"
        :title="cell.items.map((i) => i.title).join('\n')"
        @mouseenter="hoveredDay = cell.day"
        @mouseleave="hoveredDay = null"
        @click="open(cell.items)"
      >
        {{ cell.day ?? '' }}
      </button>
    </div>
    <p class="calendar-foot">
      <template v-if="hoverItems.length">{{ hoveredDay }} 日：{{ hoverItems.map((i) => i.title).join('、') }}</template>
      <template v-else-if="upcoming">下一个上线：{{ upcoming }} 日</template>
      <template v-else>本月没有待上线的需求</template>
    </p>
  </section>
</template>

<style scoped>
.calendar {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.weekdays,
.grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 4px;
}

.weekdays span {
  text-align: center;
  font-size: 11px;
  color: var(--text-muted);
}

.cell {
  height: 26px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  border: 1px solid transparent;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
  background: var(--surface-soft);
  cursor: default;
  transition: transform 140ms ease, border-color 140ms ease;
}

.cell.blank {
  background: transparent;
}

.cell.past {
  opacity: 0.6;
}

.cell.l1 { background: var(--primary-soft); color: var(--primary); cursor: pointer; }
.cell.l2 { background: var(--aurora-end); color: var(--on-primary); cursor: pointer; }
.cell.l3 { background: var(--primary); color: var(--on-primary); cursor: pointer; }
.cell.done { background: var(--success); color: var(--on-primary); }

.cell.today {
  border-color: var(--primary);
  font-weight: 800;
  color: var(--text-strong);
}

.cell:not(:disabled):hover {
  transform: translateY(-1px);
  border-color: var(--primary);
}

.calendar-foot {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
