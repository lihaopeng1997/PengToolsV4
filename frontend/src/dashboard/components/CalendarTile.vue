<script setup lang="ts">
/**
 * 便当格 · 本月上线日历：按天的热力格。颜色深浅 = 当天计划/实际上线的需求数，
 * 今天加描边。点击任意一天展开当天任务，可就地打开需求、进完成情况/测试点，
 * 也可把未排期需求排到该日，或把任务从该日移出（清空实际上线日）。
 */
import { computed, ref } from 'vue'
import type { DashboardBoardItem, MonthlyReleaseTask } from '../types'

const props = defineProps<{
  board?: DashboardBoardItem[] | null
  tasks?: MonthlyReleaseTask[] | null
  busyId?: string | null
}>()

const emit = defineEmits<{
  (e: 'openRequirement', id: string): void
  (e: 'openRequirementSection', id: string, section: 'completion' | 'test_points'): void
  (e: 'setReleaseDate', id: string, date: string): void
  (e: 'createRequirement'): void
  (e: 'openManager'): void
}>()

type DayItem = { id: string; title: string; done: boolean }

const today = new Date()
const year = today.getFullYear()
const month = today.getMonth()
const monthKey = `${year}-${String(month + 1).padStart(2, '0')}`
const hoveredDay = ref<number | null>(null)
const selectedDay = ref<number | null>(null)
const pickedId = ref('')

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
const selectedItems = computed(() => (selectedDay.value ? byDay.value.get(selectedDay.value) ?? [] : []))
const selectedDate = computed(() =>
  selectedDay.value ? `${monthKey}-${String(selectedDay.value).padStart(2, '0')}` : '',
)

/** Tasks without any actual release date: the pool that can still be scheduled. */
const unplanned = computed(() => (props.tasks ?? [])
  .filter((task) => String(task.id || '')
    && !String(task.actual_release_date || task.actual_online_date || ''))
  .sort((a, b) => String(a.title || a.code || '').localeCompare(String(b.title || b.code || ''), 'zh-CN')))

function level(count: number): string {
  if (!count) return 'l0'
  if (count === 1) return 'l1'
  if (count === 2) return 'l2'
  return 'l3'
}

function select(day: number | null): void {
  if (!day) return
  selectedDay.value = selectedDay.value === day ? null : day
  pickedId.value = ''
}

function isBusy(id: string): boolean {
  return Boolean(props.busyId && props.busyId === id)
}

function openTask(id: string): void {
  if (id) emit('openRequirement', id)
}

function openSection(id: string, section: 'completion' | 'test_points'): void {
  if (id) emit('openRequirementSection', id, section)
}

function clearDate(id: string): void {
  if (id) emit('setReleaseDate', id, '')
}

function schedule(): void {
  const id = pickedId.value
  const date = selectedDate.value
  if (!id || !date) return
  emit('setReleaseDate', id, date)
  pickedId.value = ''
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
        :class="[level(cell.items.length), { blank: !cell.day, today: cell.today, past: cell.past, done: cell.items.length && cell.items.every((i) => i.done), selected: cell.day && cell.day === selectedDay }]"
        :disabled="!cell.day"
        :aria-label="cell.day ? `${month + 1} 月 ${cell.day} 日，${cell.items.length} 项上线任务` : undefined"
        :title="cell.items.map((i) => i.title).join('\n') || (cell.day ? '点击查看当天安排' : undefined)"
        @mouseenter="hoveredDay = cell.day"
        @mouseleave="hoveredDay = null"
        @click="select(cell.day)"
      >
        {{ cell.day ?? '' }}
      </button>
    </div>

    <div v-if="selectedDay" class="day-panel">
      <div class="day-head">
        <strong>{{ month + 1 }} 月 {{ selectedDay }} 日</strong>
        <span class="day-count">{{ selectedItems.length }} 项</span>
        <button type="button" class="link" @click="selectedDay = null">收起</button>
      </div>

      <ul v-if="selectedItems.length" class="day-list">
        <li v-for="item in selectedItems" :key="item.id" class="day-item">
          <button type="button" class="task" :disabled="isBusy(item.id)" @click="openTask(item.id)">
            <span class="task-title">{{ item.title || item.id }}</span>
            <span class="task-state" :class="{ done: item.done }">{{ item.done ? '已完成' : '进行中' }}</span>
          </button>
          <div class="task-actions">
            <button type="button" @click="openSection(item.id, 'completion')">完成情况</button>
            <button type="button" @click="openSection(item.id, 'test_points')">测试点</button>
            <button type="button" :disabled="isBusy(item.id)" @click="clearDate(item.id)">移出该日</button>
          </div>
        </li>
      </ul>
      <p v-else class="empty">这天暂无上线任务</p>

      <div class="day-foot">
        <select v-if="unplanned.length" v-model="pickedId">
          <option value="">选择未排期需求…</option>
          <option v-for="task in unplanned" :key="String(task.id)" :value="String(task.id)">
            {{ task.title || task.code || task.id }}
          </option>
        </select>
        <button v-else type="button" class="ghost" disabled>没有未排期需求</button>
        <button type="button" :disabled="!pickedId" @click="schedule">排到 {{ selectedDay }} 日</button>
        <button v-if="!selectedItems.length" type="button" @click="emit('createRequirement')">新建需求</button>
        <button type="button" class="ghost" @click="emit('openManager')">去需求管理</button>
      </div>
    </div>

    <p v-else class="calendar-foot">
      <template v-if="hoverItems.length">{{ hoveredDay }} 日：{{ hoverItems.map((i) => i.title).join('、') }}</template>
      <template v-else-if="upcoming">下一个上线：{{ upcoming }} 日</template>
      <template v-else>本月没有待上线的需求，点任意日期可安排</template>
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
  cursor: pointer;
  transition: transform 140ms ease, border-color 140ms ease;
}

.cell.blank {
  background: transparent;
  cursor: default;
}

.cell.past {
  opacity: 0.6;
}

.cell.l1 { background: var(--primary-soft); color: var(--primary); }
.cell.l2 { background: var(--aurora-end); color: var(--on-primary); }
.cell.l3 { background: var(--primary); color: var(--on-primary); }
.cell.done { background: var(--success); color: var(--on-primary); }

.cell.today {
  border-color: var(--primary);
  font-weight: 800;
  color: var(--text-strong);
}

.cell.selected {
  outline: 2px solid var(--primary);
  outline-offset: 1px;
}

.cell:not(:disabled):hover {
  transform: translateY(-1px);
  border-color: var(--primary);
}

.day-panel {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 4px;
  border-top: 1px solid var(--border);
  max-height: 220px;
  overflow: auto;
}

.day-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  color: var(--text-strong);
}

.day-count {
  color: var(--text-muted);
}

.link {
  margin-left: auto;
  border: 0;
  background: none;
  color: var(--primary);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}

.day-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.day-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.task {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  border: 0;
  background: none;
  padding: 2px 0;
  font-size: 12px;
  color: var(--text-strong);
  text-align: left;
  cursor: pointer;
}

.task:hover .task-title {
  color: var(--primary);
  text-decoration: underline;
}

.task-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-state {
  flex: none;
  font-size: 11px;
  color: var(--text-muted);
}

.task-state.done {
  color: var(--success);
}

.task-actions {
  display: flex;
  gap: 4px;
  padding-left: 2px;
}

.task-actions button,
.day-foot button {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--surface-soft);
  color: var(--text-muted);
  cursor: pointer;
}

.task-actions button:hover:not(:disabled),
.day-foot button:hover:not(:disabled) {
  border-color: var(--primary);
  color: var(--primary);
}

.task-actions button:disabled,
.day-foot button:disabled {
  opacity: 0.5;
  cursor: default;
}

.empty {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}

.day-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.day-foot select {
  flex: 1;
  min-width: 120px;
  font-size: 11px;
  padding: 2px 4px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--surface-soft);
  color: var(--text-strong);
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
