<script setup lang="ts">
/**
 * 便当格 · 本月足迹（工作记录 + 上线日历双视图）：
 * - 面向所有用户（非开发/开发通用）：记录并回看本月每天「做了什么事」，支持就地一行速记并自动同步到日报；
 * - 面向开发人员：同时清晰展示本月上线任务，支持查看详情、完成情况、测试点与排期调整；
 * - 顶部分类胶囊：全部 | 做了啥 | 上线，双色鲜明指示点一眼分辨。
 */
import { computed, ref } from 'vue'
import type { DashboardBoardItem, MonthlyReleaseTask, MonthlyWorkLog } from '../types'

const props = defineProps<{
  board?: DashboardBoardItem[] | null
  tasks?: MonthlyReleaseTask[] | null
  logs?: MonthlyWorkLog[] | null
  busyId?: string | null
}>()

const emit = defineEmits<{
  (e: 'openRequirement', id: string): void
  (e: 'openRequirementSection', id: string, section: 'completion' | 'test_points'): void
  (e: 'setReleaseDate', id: string, date: string): void
  (e: 'addDayNote', date: string, text: string): void
  (e: 'openDaily'): void
  (e: 'createRequirement'): void
  (e: 'openManager'): void
}>()

type DayTask = { id: string; title: string; done: boolean }
type FilterMode = 'all' | 'work' | 'release'

const today = new Date()
const year = today.getFullYear()
const month = today.getMonth()
const monthKey = `${year}-${String(month + 1).padStart(2, '0')}`
const filterMode = ref<FilterMode>('all')
const hoveredDay = ref<number | null>(null)
const selectedDay = ref<number | null>(null)
const pickedId = ref('')
const noteDraft = ref('')

const tasksByDay = computed(() => {
  const map = new Map<number, DayTask[]>()
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

const logsByDay = computed(() => {
  const map = new Map<number, MonthlyWorkLog>()
  for (const log of props.logs ?? []) {
    const text = String(log.date || '')
    if (!text.startsWith(monthKey)) continue
    const day = Number(text.slice(8, 10))
    if (day) map.set(day, log)
  }
  return map
})

const cells = computed(() => {
  const first = new Date(year, month, 1)
  const offset = (first.getDay() + 6) % 7
  const days = new Date(year, month + 1, 0).getDate()
  const list: Array<{
    day: number | null
    tasks: DayTask[]
    log: MonthlyWorkLog | null
    today: boolean
    past: boolean
  }> = []
  for (let i = 0; i < offset; i++) list.push({ day: null, tasks: [], log: null, today: false, past: false })
  for (let d = 1; d <= days; d++) {
    list.push({
      day: d,
      tasks: tasksByDay.value.get(d) ?? [],
      log: logsByDay.value.get(d) ?? null,
      today: d === today.getDate(),
      past: d < today.getDate(),
    })
  }
  return list
})

const totalWorkDays = computed(() => logsByDay.value.size)
const totalReleases = computed(() => [...tasksByDay.value.values()].reduce((sum, list) => sum + list.length, 0))

const headerSub = computed(() => {
  if (filterMode.value === 'work') return `${month + 1} 月 · 已记 ${totalWorkDays.value} 天`
  if (filterMode.value === 'release') return `${month + 1} 月 · ${totalReleases.value} 项上线`
  return `${month + 1} 月 · ${totalWorkDays.value}天足迹 / ${totalReleases.value}上线`
})

const upcoming = computed(() => {
  const days = [...tasksByDay.value.keys()].filter((d) => d >= today.getDate()).sort((a, b) => a - b)
  return days.length ? days[0] : null
})

const hoverSummary = computed(() => {
  if (!hoveredDay.value) return ''
  const log = logsByDay.value.get(hoveredDay.value)
  const tasks = tasksByDay.value.get(hoveredDay.value) ?? []
  const parts: string[] = []
  if (log && filterMode.value !== 'release') parts.push(`做了 ${log.count} 件事：${log.summary}`)
  if (tasks.length && filterMode.value !== 'work') parts.push(`上线 ${tasks.length} 项：${tasks.map((t) => t.title).join('、')}`)
  return parts.length ? `${hoveredDay.value} 日 · ${parts.join(' ｜ ')}` : ''
})

const selectedTasks = computed(() => (selectedDay.value ? tasksByDay.value.get(selectedDay.value) ?? [] : []))
const selectedLog = computed(() => (selectedDay.value ? logsByDay.value.get(selectedDay.value) ?? null : null))
const selectedDate = computed(() =>
  selectedDay.value ? `${monthKey}-${String(selectedDay.value).padStart(2, '0')}` : '',
)

const unplanned = computed(() => (props.tasks ?? [])
  .filter((task) => String(task.id || '') && !String(task.actual_release_date || task.actual_online_date || ''))
  .sort((a, b) => String(a.title || a.code || '').localeCompare(String(b.title || b.code || ''), 'zh-CN')))

function cellTone(cell: { day: number | null; tasks: DayTask[]; log: MonthlyWorkLog | null }): string {
  if (!cell.day) return 'blank'
  const showWork = filterMode.value !== 'release' && Boolean(cell.log)
  const showRel = filterMode.value !== 'work' && cell.tasks.length > 0
  if (showRel && showWork) return 'both'
  if (showRel) return cell.tasks.every((i) => i.done) ? 'rel-done' : 'rel-active'
  if (showWork) return 'work-logged'
  return 'l0'
}

function cellTooltip(cell: { day: number | null; tasks: DayTask[]; log: MonthlyWorkLog | null }): string | undefined {
  if (!cell.day) return undefined
  const lines: string[] = []
  if (cell.log) lines.push(`【做了什么】${cell.log.lines.slice(0, 3).join('；')}`)
  if (cell.tasks.length) lines.push(`【上线任务】${cell.tasks.map((t) => t.title).join('、')}`)
  return lines.length ? lines.join('\n') : '点击记录当天做了什么或安排任务'
}

function select(day: number | null): void {
  if (!day) return
  selectedDay.value = selectedDay.value === day ? null : day
  pickedId.value = ''
  noteDraft.value = ''
}

function submitDayNote(): void {
  const text = noteDraft.value.trim()
  const date = selectedDate.value
  if (!text || !date) return
  emit('addDayNote', date, text)
  noteDraft.value = ''
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
    <header class="cal-head">
      <div class="cal-title-row">
        <h3>本月足迹</h3>
        <div class="mode-pills" role="tablist" aria-label="日历视角">
          <button type="button" :class="{ on: filterMode === 'all' }" @click="filterMode = 'all'">全部</button>
          <button type="button" :class="{ on: filterMode === 'work' }" @click="filterMode = 'work'">做了啥</button>
          <button type="button" :class="{ on: filterMode === 'release' }" @click="filterMode = 'release'">上线</button>
        </div>
      </div>
      <div class="cal-meta-row">
        <span class="tile-sub">{{ headerSub }}</span>
        <span class="legend">
          <i class="lg-dot work" />工作记录
          <i class="lg-dot rel" />上线任务
        </span>
      </div>
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
        :class="[
          cellTone(cell),
          {
            today: cell.today,
            past: cell.past && !cell.log && !cell.tasks.length,
            selected: cell.day && cell.day === selectedDay,
          },
        ]"
        :disabled="!cell.day"
        :aria-label="cell.day ? `${month + 1} 月 ${cell.day} 日` : undefined"
        :title="cellTooltip(cell)"
        @mouseenter="hoveredDay = cell.day"
        @mouseleave="hoveredDay = null"
        @click="select(cell.day)"
      >
        <span class="day-num">{{ cell.day ?? '' }}</span>
        <span v-if="cell.day" class="dots" aria-hidden="true">
          <i v-if="cell.log && filterMode !== 'release'" class="dot work" />
          <i
            v-if="cell.tasks.length && filterMode !== 'work'"
            class="dot rel"
            :class="{ done: cell.tasks.every((i) => i.done) }"
          />
        </span>
      </button>
    </div>

    <div v-if="selectedDay" class="day-panel">
      <div class="day-head">
        <strong>{{ month + 1 }} 月 {{ selectedDay }} 日</strong>
        <span class="day-badges">
          <span v-if="selectedLog" class="mini-badge work">{{ selectedLog.count }} 条记录</span>
          <span v-if="selectedTasks.length" class="mini-badge rel">{{ selectedTasks.length }} 项上线</span>
        </span>
        <button type="button" class="link" @click="selectedDay = null">收起</button>
      </div>

      <!-- 区块一：当天做了什么（所有用户通用） -->
      <div v-if="filterMode !== 'release'" class="sub-block">
        <div class="sub-head">
          <span class="sub-title"><i class="lg-dot work" />当天做了什么</span>
          <button type="button" class="link-xs" @click="emit('openDaily')">完整日报 →</button>
        </div>
        <ul v-if="selectedLog && selectedLog.lines.length" class="work-list">
          <li v-for="(line, i) in selectedLog.lines.slice(0, 5)" :key="i">{{ line }}</li>
        </ul>
        <p v-else class="empty">还没有记录这天做了什么</p>
        <form class="quick-note-form" @submit.prevent="submitDayNote">
          <input
            v-model="noteDraft"
            type="text"
            maxlength="300"
            :placeholder="`记一笔 ${month + 1}/${selectedDay} 做了什么…（回车保存）`"
          />
          <button type="submit" class="btn-save-note" :disabled="!noteDraft.trim()">记一笔</button>
        </form>
      </div>

      <!-- 区块二：当天上线任务（开发/发版视角） -->
      <div v-if="filterMode !== 'work'" class="sub-block">
        <div class="sub-head">
          <span class="sub-title"><i class="lg-dot rel" />上线任务</span>
          <button type="button" class="link-xs" @click="emit('openManager')">需求管理 →</button>
        </div>
        <ul v-if="selectedTasks.length" class="day-list">
          <li v-for="item in selectedTasks" :key="item.id" class="day-item">
            <button type="button" class="task" :disabled="isBusy(item.id)" @click="openTask(item.id)">
              <span class="task-title">{{ item.title || item.id }}</span>
              <span class="task-state" :class="{ done: item.done }">{{ item.done ? '已上线' : '推进中' }}</span>
            </button>
            <div class="task-actions">
              <button type="button" @click="openSection(item.id, 'completion')">完成情况</button>
              <button type="button" @click="openSection(item.id, 'test_points')">测试点</button>
              <button type="button" :disabled="isBusy(item.id)" @click="clearDate(item.id)">移出</button>
            </div>
          </li>
        </ul>
        <p v-else class="empty">这天暂无排期上线任务</p>

        <div class="day-foot">
          <select v-if="unplanned.length" v-model="pickedId">
            <option value="">安排未排期需求到 {{ selectedDay }} 日…</option>
            <option v-for="task in unplanned" :key="String(task.id)" :value="String(task.id)">
              {{ task.title || task.code || task.id }}
            </option>
          </select>
          <button v-if="unplanned.length" type="button" :disabled="!pickedId" @click="schedule">排期</button>
          <button v-if="!selectedTasks.length" type="button" class="ghost" @click="emit('createRequirement')">+ 新建需求</button>
        </div>
      </div>
    </div>

    <p v-else class="calendar-foot">
      <template v-if="hoverSummary">{{ hoverSummary }}</template>
      <template v-else-if="upcoming">下一个上线：{{ upcoming }} 日 · 点任意日期记一笔或查看足迹</template>
      <template v-else>点任意日期即可记录「今天做了什么」或安排上线任务</template>
    </p>
  </section>
</template>

<style scoped>
.calendar {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cal-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cal-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.cal-title-row h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-strong);
}

.cal-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.mode-pills {
  display: inline-flex;
  padding: 2px;
  border-radius: 999px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  gap: 2px;
}

.mode-pills button {
  height: 22px;
  padding: 0 9px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease, box-shadow 160ms ease;
}

.mode-pills button.on {
  background: var(--primary);
  color: var(--on-primary);
  box-shadow: 0 2px 6px rgba(108, 88, 217, 0.28);
}

.legend {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
  color: var(--text-muted);
}

.lg-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-right: 2px;
}

.lg-dot.work { background: #10B981; }
.lg-dot.rel { background: #6C58D9; }

.weekdays,
.grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 4px;
}

.weekdays span {
  text-align: center;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
}

.cell {
  height: 30px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  border-radius: 8px;
  border: 1px solid transparent;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--text);
  background: var(--surface-soft);
  cursor: pointer;
  transition: transform 150ms cubic-bezier(0.22, 1, 0.36, 1), border-color 150ms ease, background 150ms ease, box-shadow 150ms ease;
}

.cell:not(.blank):hover {
  transform: translateY(-1px);
  border-color: var(--primary);
  box-shadow: 0 2px 8px rgba(108, 88, 217, 0.14);
}

.cell.blank {
  background: transparent;
  cursor: default;
}

.cell.past {
  opacity: 0.62;
}

.cell.work-logged {
  background: #E6F8F1;
  color: #047857;
  border-color: #A7F3D0;
  font-weight: 600;
}

.cell.rel-active {
  background: #EDE9FE;
  color: #5B21B6;
  border-color: #C4B5FD;
  font-weight: 700;
}

.cell.rel-done {
  background: #D1FAE5;
  color: #065F46;
  border-color: #6EE7B7;
  font-weight: 700;
}

.cell.both {
  background: linear-gradient(135deg, #EDE9FE 0%, #D1FAE5 100%);
  color: #3B288A;
  border-color: #A78BFA;
  font-weight: 700;
}

.cell.today {
  border: 1.5px solid var(--primary);
  font-weight: 800;
  color: var(--primary-active);
  box-shadow: 0 0 0 2px rgba(108, 88, 217, 0.12);
}

.cell.selected {
  outline: 2px solid var(--primary);
  outline-offset: 1px;
  transform: scale(1.04);
}

.day-num {
  line-height: 1;
}

.dots {
  display: inline-flex;
  gap: 3px;
  height: 4px;
  align-items: center;
}

.dots .dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
}

.dots .dot.work { background: #059669; }
.dots .dot.rel { background: #6C58D9; }
.dots .dot.rel.done { background: #059669; }

.day-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  border-radius: 12px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
}

.day-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.day-head strong {
  font-size: 13px;
  color: var(--text-strong);
}

.day-badges {
  display: inline-flex;
  gap: 4px;
}

.mini-badge {
  font-size: 10.5px;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 999px;
}

.mini-badge.work { background: #D1FAE5; color: #047857; }
.mini-badge.rel { background: #EDE9FE; color: #5B21B6; }

.link {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--primary);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}

.sub-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--border);
}

.sub-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.sub-title {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  font-weight: 700;
  color: var(--text-strong);
}

.link-xs {
  border: none;
  background: none;
  color: var(--primary);
  font-size: 11px;
  cursor: pointer;
}

.work-list {
  margin: 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 12px;
  color: var(--text);
  max-height: 92px;
  overflow-y: auto;
}

.quick-note-form {
  display: flex;
  gap: 6px;
}

.quick-note-form input {
  flex: 1;
  min-width: 0;
  height: 28px;
  padding: 0 9px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-strong);
  font-size: 12px;
  outline: none;
}

.quick-note-form input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px rgba(108, 88, 217, 0.14);
}

.btn-save-note {
  height: 28px;
  padding: 0 11px;
  border-radius: 999px;
  border: none;
  background: linear-gradient(135deg, #7C6AE6, #6C58D9);
  color: #FFFFFF;
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  flex-shrink: 0;
}

.btn-save-note:disabled {
  opacity: 0.45;
  cursor: default;
}

.day-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 124px;
  overflow-y: auto;
}

.day-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 8px;
  border-radius: 8px;
  background: var(--surface);
  border: 1px solid var(--border);
}

.task {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  border: none;
  background: none;
  padding: 0;
  text-align: left;
  cursor: pointer;
  color: var(--text-strong);
  font-size: 12px;
  font-weight: 600;
}

.task-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-state {
  flex: none;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 999px;
  background: #FEF3C7;
  color: #B45309;
}

.task-state.done {
  background: #D1FAE5;
  color: #047857;
}

.task-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.task-actions button,
.day-foot button {
  height: 24px;
  padding: 0 8px;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 11px;
  cursor: pointer;
}

.task-actions button:hover,
.day-foot button:hover:not(:disabled) {
  border-color: var(--primary);
  color: var(--primary);
}

.day-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  align-items: center;
}

.day-foot select {
  flex: 1;
  min-width: 120px;
  height: 24px;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 11px;
  padding: 0 6px;
}

.empty {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-muted);
}

.calendar-foot {
  margin: auto 0 0;
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
