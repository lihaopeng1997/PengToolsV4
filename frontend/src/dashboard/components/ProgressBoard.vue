<script setup lang="ts">
/**
 * 首页「项目进度」看板：5 个阶段列，卡片 = 需求。
 * - 点击卡片：在首页右侧抽屉查看/修改，不跳转需求管理
 * - 拖动卡片到其它列：改阶段（Python 侧保留同组内更细的状态）
 * - 列头 “+”：在该阶段直接新建
 * 只通过事件把意图交给 DashboardApp，由它统一写入。
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import PrismIcon from '../../shared/components/PrismIcon.vue'
import { STAGE_DEFS, type DashboardBoardItem, type DashboardProgress, type StageKey } from '../types'

const props = defineProps<{
  progress?: DashboardProgress | null
  focus?: StageKey | null
  isDemo?: boolean | null
  busyId?: string | null
  /** 首页已有「阶段进度」卡时隐藏列表顶部的阶段页签 */
  hideStageTabs?: boolean
}>()

const emit = defineEmits<{
  (e: 'open', id: string): void
  (e: 'move', id: string, group: StageKey): void
  (e: 'create', group: StageKey, title: string): void
  (e: 'focusStage', key: StageKey | null): void
  (e: 'navigate', navIndex: number): void
  (e: 'viewChange', view: 'list' | 'board'): void
}>()

const dragId = ref<string | null>(null)
const dropKey = ref<StageKey | null>(null)
const creatingIn = ref<StageKey | null>(null)
const draftTitle = ref('')
const draftInput = ref<HTMLInputElement[] | null>(null)

const COLUMN_LIMIT = 4
const AUTO_COMPACT_ABOVE = 12
const PRIORITY_RANK: Record<string, number> = { 紧急: 0, 重要: 1 }

function sortItems(items: DashboardBoardItem[]): DashboardBoardItem[] {
  return [...items].sort((a, b) => {
    const pa = PRIORITY_RANK[String(a.priority || '')] ?? 2
    const pb = PRIORITY_RANK[String(b.priority || '')] ?? 2
    if (pa !== pb) return pa - pb
    const da = String(a.actual_release_date || '9999')
    const db = String(b.actual_release_date || '9999')
    return da.localeCompare(db)
  })
}

const columns = computed(() => {
  const items = props.progress?.board ?? []
  return STAGE_DEFS.map((stage) => ({
    ...stage,
    items: sortItems(items.filter((item) => item.group === stage.key)),
  }))
})

const totalCards = computed(() => (props.progress?.board ?? []).length)

// 展开的列（显示全部卡片）；聚焦某阶段时该列自动全部展开
const expanded = ref<Set<StageKey>>(new Set())

function isExpanded(key: StageKey): boolean {
  return props.focus === key || expanded.value.has(key)
}

function toggleExpanded(key: StageKey): void {
  const next = new Set(expanded.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expanded.value = next
}

function visibleItems(col: { key: StageKey; items: DashboardBoardItem[] }): DashboardBoardItem[] {
  return isExpanded(col.key) ? col.items : col.items.slice(0, COLUMN_LIMIT)
}

// 紧凑视图：记住用户选择；没选过时任务多就默认紧凑
const COMPACT_KEY = 'pengtools.home.board.compact'
function readCompactPref(): boolean | null {
  try {
    const raw = window.localStorage.getItem(COMPACT_KEY)
    return raw === null ? null : raw === '1'
  } catch {
    return null
  }
}
const compactPref = ref<boolean | null>(readCompactPref())
const compact = computed(() => compactPref.value ?? totalCards.value > AUTO_COMPACT_ABOVE)

// 视图：列表（默认，只列需要关注的）/ 看板（拖动改阶段）
const VIEW_KEY = 'pengtools.home.board.view'
function readView(): 'list' | 'board' {
  try {
    return window.localStorage.getItem(VIEW_KEY) === 'board' ? 'board' : 'list'
  } catch {
    return 'list'
  }
}
const view = ref<'list' | 'board'>(readView())

onMounted(() => emit('viewChange', view.value))

function setView(next: 'list' | 'board'): void {
  view.value = next
  emit('viewChange', next)
  try {
    window.localStorage.setItem(VIEW_KEY, next)
  } catch {
    /* 存不了就只在本次生效 */
  }
}

const LIST_LIMIT = 6
const STAGE_URGENCY: Record<string, number> = { release: 0, testing: 1, develop: 2, analysis: 3, online: 4 }
const listExpanded = ref(false)

const listItems = computed(() => {
  const items = (props.progress?.board ?? []).filter((item) => !props.focus || item.group === props.focus)
  return [...items].sort((a, b) => {
    const pa = PRIORITY_RANK[String(a.priority || '')] ?? 2
    const pb = PRIORITY_RANK[String(b.priority || '')] ?? 2
    if (pa !== pb) return pa - pb
    const sa = STAGE_URGENCY[a.group] ?? 5
    const sb = STAGE_URGENCY[b.group] ?? 5
    if (sa !== sb) return sa - sb
    return String(a.actual_release_date || a.updated_at || '9999').localeCompare(
      String(b.actual_release_date || b.updated_at || '9999'),
    )
  })
})

const visibleList = computed(() => (listExpanded.value ? listItems.value : listItems.value.slice(0, LIST_LIMIT)))

function stageMeta(group: string) {
  return STAGE_DEFS.find((stage) => stage.key === group) ?? STAGE_DEFS[1]
}

function pickStage(key: StageKey | null): void {
  listExpanded.value = false
  emit('focusStage', props.focus === key ? null : key)
}

function toggleCompact(): void {
  compactPref.value = !compact.value
  try {
    window.localStorage.setItem(COMPACT_KEY, compactPref.value ? '1' : '0')
  } catch {
    /* 存不了就只在本次生效 */
  }
}
const focusLabel = computed(() => STAGE_DEFS.find((s) => s.key === props.focus)?.label ?? '')

function isReal(item: DashboardBoardItem): boolean {
  return Boolean(item.id) && !props.isDemo && !String(item.id).startsWith('demo-') && !item.is_demo
}

function tpPercent(item: DashboardBoardItem): number {
  const total = item.tp_total ?? 0
  return total > 0 ? Math.round(((item.tp_done ?? 0) / total) * 100) : 0
}

function shortDate(value?: string | null): string {
  const text = String(value || '')
  return text.length >= 10 ? `${Number(text.slice(5, 7))}/${Number(text.slice(8, 10))}` : ''
}

function onDragStart(event: DragEvent, item: DashboardBoardItem): void {
  if (!isReal(item)) {
    event.preventDefault()
    return
  }
  dragId.value = item.id
  event.dataTransfer?.setData('text/plain', item.id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}

function onDragEnd(): void {
  dragId.value = null
  dropKey.value = null
}

function onDragOver(event: DragEvent, key: StageKey): void {
  if (!dragId.value) return
  event.preventDefault()
  dropKey.value = key
}

function onDrop(event: DragEvent, key: StageKey): void {
  event.preventDefault()
  const id = dragId.value || event.dataTransfer?.getData('text/plain') || ''
  const item = (props.progress?.board ?? []).find((row) => row.id === id)
  onDragEnd()
  if (item && item.group !== key) emit('move', id, key)
}

async function startCreate(key: StageKey): Promise<void> {
  creatingIn.value = key
  draftTitle.value = ''
  await nextTick()
  draftInput.value?.[0]?.focus()
}

function submitCreate(): void {
  const title = draftTitle.value.trim()
  if (title && creatingIn.value) emit('create', creatingIn.value, title)
  creatingIn.value = null
  draftTitle.value = ''
}

function onOpen(item: DashboardBoardItem): void {
  if (isReal(item)) emit('open', item.id)
}
</script>

<template>
  <section class="board card" data-testid="progress-board">
    <header class="board-head">
      <div class="head-title">
        <h2>项目进度</h2>
        <span v-if="totalCards" class="head-count">{{ totalCards }}</span>
        <span class="head-sub">{{
          !totalCards ? '还没有在推进的需求'
            : view === 'list' ? '按紧急度与上线日排序'
              : '拖动卡片即可切换阶段'
        }}</span>
      </div>
      <div class="head-actions">
        <button v-if="props.focus && view === 'board'" class="chip focus-chip" type="button" @click="emit('focusStage', null)">
          只看 {{ focusLabel }}
          <PrismIcon name="x" :size="12" />
        </button>
        <div v-if="totalCards" class="seg" role="tablist" aria-label="视图">
          <button type="button" :class="{ on: view === 'list' }" data-action="view-list" @click="setView('list')">列表</button>
          <button type="button" :class="{ on: view === 'board' }" data-action="view-board" @click="setView('board')">看板</button>
        </div>
        <button
          v-if="totalCards && view === 'board'"
          class="tool-btn view-toggle"
          type="button"
          :title="compact ? '切换为详细卡片' : '切换为一行一条的紧凑视图'"
          data-action="toggle-compact"
          @click="toggleCompact"
        >
          {{ compact ? '紧凑视图' : '详细卡片' }}
        </button>
        <button class="tool-btn nav-btn" type="button" @click="emit('navigate', 10)">
          <span>需求管理</span>
          <span class="nav-arrow" aria-hidden="true">→</span>
        </button>
      </div>
    </header>

    <div v-if="view === 'list' && totalCards" class="focus-list" data-testid="focus-list">
      <div v-if="!props.hideStageTabs" class="stage-tabs" role="tablist" aria-label="按阶段筛选">
        <button type="button" class="stage-tab" :class="{ on: !props.focus }" @click="pickStage(null)">
          全部 <b>{{ totalCards }}</b>
        </button>
        <button
          v-for="col in columns"
          :key="col.key"
          type="button"
          class="stage-tab"
          :class="{ on: props.focus === col.key, empty: !col.items.length }"
          :style="{ '--stage': col.color }"
          :data-stage="col.key"
          @click="pickStage(col.key)"
        >
          <span class="tab-dot" />{{ col.label }} <b>{{ col.items.length }}</b>
        </button>
      </div>

      <ul class="rows">
        <li
          v-for="item in visibleList"
          :key="item.id"
          class="row"
          :class="{ urgent: item.priority === '紧急', important: item.priority === '重要', busy: props.busyId === item.id }"
          :style="{ '--stage': stageMeta(item.group).color, '--stage-bg': stageMeta(item.group).bg }"
          :tabindex="isReal(item) ? 0 : undefined"
          :role="isReal(item) ? 'button' : undefined"
          @click="onOpen(item)"
          @keydown.enter.self="onOpen(item)"
          @keydown.space.self.prevent="onOpen(item)"
        >
          <span class="row-stage">{{ stageMeta(item.group).label }}</span>
          <span class="row-main">
            <span class="row-title">{{ item.title }}</span>
            <span class="row-sub">{{ [item.code, item.system].filter(Boolean).join(' · ') }}</span>
          </span>
          <span v-if="item.priority && item.priority !== '普通'" class="prio">{{ item.priority }}</span>
          <span v-if="(item.tp_total ?? 0) > 0" class="row-tp" :title="`测试点 ${item.tp_done}/${item.tp_total}`">
            <span class="tp-bar"><span :style="{ width: `${tpPercent(item)}%` }" /></span>
            {{ item.tp_done }}/{{ item.tp_total }}
          </span>
          <span v-else-if="item.actual_release_date" class="row-date">
            <PrismIcon name="calendar" :size="11" />{{ shortDate(item.actual_release_date) }}
          </span>
          <span v-else-if="item.next_action && item.group !== 'online'" class="row-next">{{ item.next_action }}</span>
        </li>
      </ul>

      <div v-if="!listItems.length" class="empty">这个阶段暂时没有需求</div>
      <button
        v-if="listItems.length > LIST_LIMIT"
        type="button"
        class="more list-more"
        data-action="toggle-list"
        @click="listExpanded = !listExpanded"
      >
        {{ listExpanded ? '收起' : `展开其余 ${listItems.length - LIST_LIMIT} 项` }}
      </button>
    </div>

    <div v-if="view === 'board' || !totalCards" class="columns" :class="{ compact }">
      <div
        v-for="col in columns"
        :key="col.key"
        class="column"
        :class="{
          dim: props.focus && props.focus !== col.key,
          drop: dropKey === col.key,
          focused: props.focus === col.key,
          'is-empty': !col.items.length,
        }"
        :style="{ '--stage': col.color }"
        @dragover="onDragOver($event, col.key)"
        @dragleave="dropKey === col.key && (dropKey = null)"
        @drop="onDrop($event, col.key)"
      >
        <div class="col-head">
          <span class="dot" />
          <span class="col-name">{{ col.label }}</span>
          <span class="col-count">{{ col.items.length }}</span>
          <button
            v-if="col.key !== 'online' && !props.isDemo"
            class="col-add"
            type="button"
            :title="`在「${col.label}」新建需求`"
            @click="startCreate(col.key)"
          >
            <PrismIcon name="plus" :size="14" />
          </button>
        </div>

        <div class="col-body">
          <form v-if="creatingIn === col.key" class="draft" @submit.prevent="submitCreate">
            <input
              ref="draftInput"
              v-model="draftTitle"
              class="draft-input"
              placeholder="需求标题，回车创建"
              maxlength="200"
              @keydown.esc="creatingIn = null"
              @blur="submitCreate"
            />
          </form>

          <TransitionGroup name="card">
            <article
              v-for="item in visibleItems(col)"
              :key="item.id"
              class="task"
              :class="{
                dragging: dragId === item.id,
                busy: props.busyId === item.id,
                urgent: item.priority === '紧急',
                important: item.priority === '重要',
              }"
              :draggable="isReal(item)"
              :tabindex="isReal(item) ? 0 : undefined"
              :role="isReal(item) ? 'button' : undefined"
              @dragstart="onDragStart($event, item)"
              @dragend="onDragEnd"
              @click="onOpen(item)"
              @keydown.enter.self="onOpen(item)"
              @keydown.space.self.prevent="onOpen(item)"
            >
              <div class="task-top">
                <span class="task-code">{{ item.code || item.status }}</span>
                <span v-if="item.priority && item.priority !== '普通'" class="prio">{{ item.priority }}</span>
              </div>
              <p class="task-title">{{ item.title }}</p>
              <div class="task-meta">
                <span v-if="item.system" class="sys">{{ item.system }}</span>
                <span v-if="item.actual_release_date" class="date">
                  <PrismIcon name="calendar" :size="11" />{{ shortDate(item.actual_release_date) }}
                </span>
              </div>
              <div v-if="(item.tp_total ?? 0) > 0" class="tp" :title="`测试点 ${item.tp_done}/${item.tp_total}`">
                <span class="tp-bar"><span :style="{ width: `${tpPercent(item)}%` }" /></span>
                <span class="tp-text">{{ item.tp_done }}/{{ item.tp_total }}</span>
              </div>
              <p v-else-if="item.next_action && col.key !== 'online'" class="next">{{ item.next_action }}</p>
            </article>
          </TransitionGroup>

          <button
            v-if="col.items.length > COLUMN_LIMIT && props.focus !== col.key"
            class="more"
            type="button"
            data-action="toggle-column"
            @click="toggleExpanded(col.key)"
          >
            {{ isExpanded(col.key) ? '收起' : `还有 ${col.items.length - COLUMN_LIMIT} 项` }}
          </button>

          <div v-if="!col.items.length && creatingIn !== col.key" class="empty">
            <span class="empty-text">{{ dragId ? '松手移入此阶段' : col.key === 'online' ? '本月暂无上线' : '暂无需求' }}</span>
            <button
              v-if="!dragId && col.key !== 'online' && !props.isDemo"
              type="button"
              class="empty-add"
              @click="startCreate(col.key)"
            >
              <PrismIcon name="plus" :size="12" />
              <span>新建</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.card {
  background: rgba(253, 253, 255, 0.94);
  border: 1px solid var(--border);
  border-radius: 20px;
  box-shadow: 0 4px 16px rgba(38, 36, 56, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.92);
}

.board {
  padding: 18px 20px 18px;
  min-width: 0;
}

.board-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.head-title {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--text-strong);
}

.head-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 20px;
  padding: 0 7px;
  border-radius: 999px;
  background: var(--primary-soft);
  color: var(--primary);
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.head-sub {
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 10px;
  border-radius: 9px;
  border: 1px solid rgba(108, 88, 217, 0.45);
  background: var(--primary-soft);
  color: var(--primary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: background 150ms ease, border-color 150ms ease;
}

.chip:hover {
  border-color: var(--primary);
}

.tool-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  height: 30px;
  padding: 0 11px;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 150ms ease, border-color 150ms ease, color 150ms ease;
}

.tool-btn:hover {
  border-color: rgba(108, 88, 217, 0.55);
  background: var(--primary-soft);
  color: var(--primary);
}

.tool-btn.nav-btn {
  background: transparent;
  color: var(--text-muted);
}

.tool-btn.nav-btn:hover {
  background: var(--primary-soft);
  color: var(--primary);
}

.nav-arrow {
  font-size: 12px;
  line-height: 1;
  transition: transform 150ms ease;
}

.tool-btn.nav-btn:hover .nav-arrow {
  transform: translateX(2px);
}

.columns {
  display: grid;
  grid-template-columns: repeat(5, minmax(176px, 1fr));
  gap: 12px;
  overflow-x: auto;
  padding-bottom: 2px;
}

.column {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  border-radius: 14px;
  background: linear-gradient(180deg, rgba(246, 244, 253, 0.78) 0%, rgba(250, 249, 255, 0.44) 100%);
  border: 1px solid rgba(216, 211, 234, 0.72);
  overflow: hidden;
  transition: opacity 200ms ease, border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
}

.column::before {
  content: "";
  display: block;
  height: 3px;
  width: 100%;
  background: var(--stage);
  opacity: 0.78;
}

.column.is-empty {
  background: rgba(248, 247, 253, 0.52);
  border-color: rgba(216, 211, 234, 0.52);
}

.column.is-empty::before {
  opacity: 0.32;
}

.column.dim {
  opacity: 0.36;
}

.column.focused {
  border-color: var(--stage);
  box-shadow: 0 0 0 2px rgba(108, 88, 217, 0.12);
}

.column.drop {
  border-color: var(--stage);
  background: var(--primary-soft);
}

.column.drop::before {
  opacity: 1;
}

.col-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 11px 10px 9px 12px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--stage);
  flex-shrink: 0;
}

.col-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-strong);
}

.col-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(216, 211, 234, 0.72);
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.col-add {
  margin-left: auto;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: background 140ms ease, border-color 140ms ease, color 140ms ease;
}

.col-add:hover {
  background: var(--surface);
  border-color: var(--border);
  color: var(--primary);
}

.col-body {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 2px 9px 11px;
  min-height: 116px;
  max-height: 430px;
  overflow-y: auto;
}

.seg {
  display: inline-flex;
  align-items: center;
  height: 30px;
  padding: 2px;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: var(--surface-soft);
}

.seg button {
  height: 24px;
  padding: 0 12px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 140ms ease, color 140ms ease, box-shadow 140ms ease;
}

.seg button:hover:not(.on) {
  color: var(--text-strong);
}

.seg button.on {
  background: var(--surface);
  color: var(--primary);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(38, 36, 56, 0.10);
}

.focus-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.stage-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.stage-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 12px;
  border-radius: 14px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
}

.stage-tab b {
  font-variant-numeric: tabular-nums;
  color: var(--text-strong);
}

.stage-tab.on {
  border-color: var(--stage, var(--primary));
  background: var(--primary-soft);
  color: var(--text-strong);
}

.stage-tab.empty {
  opacity: 0.55;
}

.tab-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--stage);
}

.rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  max-height: 460px;
  overflow-y: auto;
}

.row {
  display: grid;
  grid-template-columns: 68px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 12px;
  padding: 10px 14px 10px 12px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  border-left: 3.5px solid var(--stage);
  cursor: pointer;
  transition: background 150ms ease, transform 150ms cubic-bezier(0.22, 1, 0.36, 1);
}

.row:last-child {
  border-bottom: none;
}

.row:hover,
.row:focus-visible {
  background: var(--stage-bg, var(--surface-soft));
  transform: translateX(2px);
  outline: none;
}

.row.busy {
  opacity: 0.6;
  pointer-events: none;
}

.row-stage {
  justify-self: start;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  color: var(--stage);
  background: var(--stage-bg, var(--surface-soft));
  border: 1px solid var(--stage);
}

.prio {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  background: #FEF3C7;
  color: #B45309;
  border: 1px solid #FCD34D;
}

.row.urgent .prio {
  background: #FEE2E2;
  color: #DC2626;
  border-color: #FCA5A5;
}

.row-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.row-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-strong);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-sub {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row.urgent .row-title::before {
  content: "● ";
  color: var(--danger);
}

.row.important .row-title::before {
  content: "● ";
  color: var(--warning);
}

.row-tp,
.row-date,
.row-next {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.row-next {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-tp .tp-bar {
  width: 54px;
}

.list-more {
  align-self: center;
  min-width: 160px;
}

.more {
  align-self: stretch;
  padding: 6px 8px;
  border-radius: 10px;
  border: 1px dashed var(--border-strong);
  background: transparent;
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
}

.more:hover {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--surface);
}

.view-toggle {
  min-width: 52px;
  justify-content: center;
}

/* 紧凑：一行一条，只留编号与标题 */
.columns.compact .col-body {
  gap: 4px;
}

.columns.compact .task {
  padding: 7px 10px 7px 13px;
  border-radius: 9px;
}

.columns.compact .task::before {
  content: "";
  position: absolute;
  left: 0;
  top: 6px;
  bottom: 6px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--stage);
  opacity: 0.85;
}

.columns.compact .task-top {
  display: none;
}

.columns.compact .task-title {
  margin: 0;
  font-size: 12.5px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-line-clamp: unset;
  display: block;
}

.columns.compact .task-meta,
.columns.compact .tp,
.columns.compact .next {
  display: none;
}

.columns.compact .task.urgent .task-title::before {
  content: "● ";
  color: var(--danger);
}

.columns.compact .task.important .task-title::before {
  content: "● ";
  color: var(--warning);
}

.task {
  position: relative;
  padding: 12px 13px 11px;
  border-radius: 12px;
  background: var(--surface);
  border: 1px solid rgba(214, 208, 232, 0.82);
  box-shadow: 0 1px 3px rgba(38, 36, 56, 0.04);
  cursor: pointer;
  outline: none;
  transition: transform 160ms cubic-bezier(0.22, 1, 0.36, 1), box-shadow 160ms ease, border-color 160ms ease;
}

.task:hover,
.task:focus-visible {
  transform: translateY(-2px);
  border-color: rgba(108, 88, 217, 0.55);
  box-shadow: 0 8px 20px rgba(108, 88, 217, 0.11);
}

.task:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}

.task.dragging {
  opacity: 0.4;
}

.task.busy {
  opacity: 0.6;
  pointer-events: none;
}

.task-top {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.task-code {
  font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task .prio {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 10.5px;
  padding: 1px 7px;
  border-radius: 6px;
  background: var(--warning-bg);
  color: var(--warning);
  border: none;
}

.task.urgent .prio {
  background: var(--danger-bg);
  color: var(--danger);
}

.task-title {
  margin: 5px 0 8px;
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.44;
  color: var(--text-strong);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.task-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
  color: var(--text-muted);
  min-width: 0;
}

.sys {
  padding: 1px 7px;
  border-radius: 6px;
  background: rgba(240, 237, 250, 0.85);
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.date {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.tp {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 9px;
}

.tp-bar {
  flex: 1;
  height: 4px;
  border-radius: 999px;
  background: var(--loading-track);
  overflow: hidden;
}

.tp-bar span {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--stage);
  transition: width 300ms ease;
}

.tp-text {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.next {
  margin: 7px 0 0;
  font-size: 11px;
  color: var(--text-muted);
}

.empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 96px;
  padding: 12px 8px;
  border-radius: 10px;
  color: var(--text-muted);
  font-size: 12px;
}

.empty-text {
  opacity: 0.72;
}

.empty-add {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid rgba(212, 206, 230, 0.85);
  background: rgba(255, 255, 255, 0.82);
  color: var(--text-muted);
  font-size: 11.5px;
  cursor: pointer;
  transition: border-color 140ms ease, color 140ms ease, background 140ms ease;
}

.empty-add:hover {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
}

.draft-input {
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border-radius: 10px;
  border: 1px solid var(--primary);
  background: var(--input-bg);
  color: var(--text);
  outline: none;
  box-shadow: 0 0 0 3px var(--primary-soft);
}

.card-enter-active,
.card-leave-active {
  transition: opacity 220ms ease, transform 220ms ease;
}

.card-enter-from,
.card-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.98);
}

.card-move {
  transition: transform 260ms ease;
}
</style>
