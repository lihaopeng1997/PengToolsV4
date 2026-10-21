<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import type { BridgeApi, HomeActionResult } from '../shared/bridge'
import type { DashboardSummary, StageKey } from './types'
import IconSprite from './IconSprite.vue'
import PrismIcon from '../shared/components/PrismIcon.vue'
import HomeHero from './components/HomeHero.vue'
import ProgressBoard from './components/ProgressBoard.vue'
import MonthlyTasks from './components/MonthlyTasks.vue'
import ReleaseOverview from './components/ReleaseOverview.vue'
import QuickTools from './components/QuickTools.vue'
import CalendarTile from './components/CalendarTile.vue'
import StageTile from './components/StageTile.vue'
import DailyTile from './components/DailyTile.vue'
import RequirementDrawer from './components/RequirementDrawer.vue'

const props = defineProps<{
  state: {
    summary: DashboardSummary | null
    bridge: BridgeApi | null
    error?: string | null
    retry?: () => Promise<void>
  }
}>()

const summary = computed(() => props.state.summary)
const error = computed(() => props.state.error)

const isDemoMode = computed(() => Boolean(summary.value?.is_demo || summary.value?.stats?.is_demo))

const isHidden = ref(false)
// 「需要关注」切到看板时占满整行
const boardView = ref<'list' | 'board'>('list')

function onVisibilityChange() {
  if (typeof document !== 'undefined') {
    isHidden.value = document.hidden
    if (document.hidden) {
      document.body.classList.add('page-hidden')
    } else {
      document.body.classList.remove('page-hidden')
    }
  }
}

onMounted(() => {
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
    onVisibilityChange()
  }
})

onUnmounted(() => {
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', onVisibilityChange)
    document.body.classList.remove('page-hidden')
  }
})

function onNavClick(navIndex: number): void {
  props.state.bridge?.navigate(navIndex)
}

// ── 首页就地操作：抽屉查看/修改、看板拖动改阶段、快速新建（不跳转需求管理）──
const canAct = computed(() => typeof props.state.bridge?.homeAction === 'function')
const focus = ref<StageKey | null>(null)
const drawer = ref<{ open: boolean; mode: 'detail' | 'create'; id: string | null; group: StageKey | null }>({
  open: false,
  mode: 'detail',
  id: null,
  group: null,
})
const busyId = ref<string | null>(null)
const toast = ref('')
let toastTimer = 0
const boardRef = ref<{ $el?: HTMLElement } | null>(null)

function act(payload: Record<string, unknown>): Promise<HomeActionResult> {
  const bridge = props.state.bridge
  if (!bridge?.homeAction) return Promise.resolve({ ok: false, error: '当前版本不支持首页操作' })
  return bridge.homeAction(payload)
}

function showToast(text: string): void {
  toast.value = text
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.value = '' }, 2600)
}

function openDrawer(id: string): void {
  drawer.value = { open: true, mode: 'detail', id, group: null }
}

function closeDrawer(): void {
  drawer.value = { ...drawer.value, open: false }
}

async function onFocusStage(key: StageKey | null): Promise<void> {
  focus.value = key
  if (key) {
    await nextTick()
    boardRef.value?.$el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

async function onMove(id: string, group: StageKey): Promise<void> {
  const board = summary.value?.progress?.board
  const row = board?.find((item) => item.id === id)
  if (!row) return
  const previous = row.group
  row.group = group // 乐观更新，Python 推送新 summary 后自然校正
  busyId.value = id
  const result = await act({ action: 'move_group', id, group })
  busyId.value = null
  if (!result.ok) {
    row.group = previous
    showToast(result.error || '修改失败')
  } else {
    showToast(`已移到「${result.item?.group_label || group}」`)
  }
}

async function onInlineCreate(group: StageKey, title: string): Promise<void> {
  const result = await act({ action: 'create', group, title })
  if (result.ok && result.item?.id) {
    showToast('已新建，可以继续补充信息')
    openDrawer(String(result.item.id))
  } else {
    showToast(result.error || '新建失败')
  }
}

async function onRename(name: string): Promise<void> {
  if (!canAct.value || !summary.value) return
  const previous = summary.value.username
  summary.value.username = name // 乐观更新，Python 推送新 summary 后校正
  const result = await act({ action: 'set_username', name })
  if (!result.ok) {
    summary.value.username = previous
    showToast(result.error || '修改失败')
  } else {
    summary.value.username = result.name || name
    showToast('称呼已更新')
  }
}

function onCreateRequirement(): void {
  if (canAct.value) {
    const group = focus.value && focus.value !== 'online' ? focus.value : 'develop'
    drawer.value = { open: true, mode: 'create', id: null, group }
  } else if (props.state.bridge?.createRequirement) {
    props.state.bridge.createRequirement()
  } else {
    onNavClick(10)
  }
}

function onDrawerCreated(id: string): void {
  showToast('需求已创建')
  openDrawer(id)
}

function onDrawerNavigate(navIndex: number): void {
  closeDrawer()
  onNavClick(navIndex)
}

function onOpenInManager(id: string): void {
  closeDrawer()
  props.state.bridge?.openRequirement?.(id)
}

function onOpenRequirement(reqId?: string | null, fallbackNav = 10): void {
  if (!reqId || String(reqId).startsWith('demo-')) {
    return
  }
  if (canAct.value) {
    openDrawer(String(reqId))
  } else if (props.state.bridge?.openRequirement) {
    props.state.bridge.openRequirement(reqId)
  } else {
    onNavClick(fallbackNav)
  }
}

function onOpenRequirementSection(reqId?: string | null, section?: string | null): void {
  if (!reqId) {
    return
  }
  const idStr = String(reqId).trim()
  if (isDemoMode.value || !idStr || idStr.toLowerCase().startsWith('demo-')) {
    return
  }
  if (section !== 'completion' && section !== 'test_points') {
    return
  }
  if (props.state.bridge?.openRequirementSection) {
    props.state.bridge.openRequirementSection(idStr, section)
  }
}

function onWriteDaily(): void {
  onNavClick(9)
}

function onRetry(): void {
  if (props.state.retry) {
    props.state.retry()
  } else {
    onNavClick(0)
  }
}
</script>

<template>
  <IconSprite />

  <div v-if="summary" class="dashboard-root" :class="{ 'page-hidden': isHidden }">
    <!-- 首页便当格：主卡（问候+搜索）| 本月上线日历 → 阶段进度 | 需要关注 | 日报+常用工具 → 本月上线任务 | 上线总览 -->
    <div class="bento">
      <HomeHero
        class="home-hero b-hero"
        :greeting="summary.greeting"
        :username="summary.username"
        :date-line="summary.date_line"
        :stats="summary.stats"
        :release="summary.release"
        :progress="summary.progress"
        :tools="summary.tools"
        :focus="focus"
        :is-demo="isDemoMode"
        @write-daily="onWriteDaily"
        @create-requirement="onCreateRequirement"
        @focus-stage="onFocusStage"
        @rename="onRename"
        @open-requirement="onOpenRequirement"
        @navigate="onNavClick"
      />

      <CalendarTile
        class="b-cal"
        :board="summary.progress?.board"
        :tasks="summary.monthly_release_tasks"
        @open-requirement="onOpenRequirement"
      />

      <StageTile
        class="b-stage"
        :progress="summary.progress"
        :focus="focus"
        @focus-stage="onFocusStage"
      />

      <ProgressBoard
        ref="boardRef"
        class="home-board b-focus"
        :class="{ wide: boardView === 'board' }"
        :progress="summary.progress"
        :focus="focus"
        :busy-id="busyId"
        :is-demo="isDemoMode"
        hide-stage-tabs
        @open="onOpenRequirement"
        @move="onMove"
        @create="onInlineCreate"
        @focus-stage="onFocusStage"
        @navigate="onNavClick"
        @view-change="(view) => (boardView = view)"
      />

      <div class="b-side">
        <DailyTile :stats="summary.stats" @write-daily="onWriteDaily" />
        <QuickTools
          :tools="summary.tools"
          @navigate="onNavClick"
        />
      </div>
    </div>

    <div class="home-grid">
      <div class="home-left">
        <MonthlyTasks
          :tasks="summary.monthly_release_tasks"
          :recent="summary.recent"
          :is-demo="isDemoMode"
          @open-requirement="onOpenRequirement"
          @open-requirement-section="onOpenRequirementSection"
          @navigate="onNavClick"
        />
      </div>

      <aside class="home-right stack">
        <ReleaseOverview
          :release="summary.release"
          :stats="summary.stats"
          :tasks="summary.monthly_release_tasks"
          :is-demo="isDemoMode"
          @navigate="onNavClick"
        />
      </aside>
    </div>

    <RequirementDrawer
      :open="drawer.open"
      :mode="drawer.mode"
      :id="drawer.id"
      :create-group="drawer.group"
      :act="act"
      @close="closeDrawer"
      @created="onDrawerCreated"
      @navigate="onDrawerNavigate"
      @open-in-manager="onOpenInManager"
    />

    <Transition name="toast">
      <div v-if="toast" class="toast" role="status">{{ toast }}</div>
    </Transition>
  </div>

  <div v-else-if="error" class="error-view">
    <PrismIcon name="file" :size="32" />
    <p>{{ error }}</p>
    <button class="btn btn-primary" type="button" @click="onRetry">重试加载</button>
  </div>
</template>

<style>
html, body, #app {
  margin: 0;
  min-width: 0;
  width: 100%;
}
body {
  font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.5;
  color: var(--text);
  background: var(--app-bg);
}
button, input, select, textarea { font: inherit; }
*, *::before, *::after { box-sizing: border-box; }
</style>

<style scoped>
:root {
  --r-lg: 16px;
  --r-sm: 10px;
}

.dashboard-root {
  padding: 0 0 8px;
  min-width: 0;
  box-sizing: border-box;
  animation: enter 0.22s ease both;
}

@keyframes enter {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 权威 2 栏网格：C>=1200: R330; 1020<=C<1200: R300; C<1020: 单栏 (规范 7.1) */
.home-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 16px;
  min-width: 0;
}

@media (min-width: 1200px) {
  .home-grid {
    grid-template-columns: minmax(0, 1fr) 330px;
  }
}

@media (min-width: 1020px) and (max-width: 1199px) {
  .home-grid {
    grid-template-columns: minmax(0, 1fr) 300px;
  }
}

@media (max-width: 1019px) {
  .home-grid {
    grid-template-columns: 1fr;
  }

  .home-right {
    display: grid !important;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
}

@media (max-width: 700px) {
  .home-right {
    grid-template-columns: 1fr;
  }
}

/* 便当格：12 栏，卡片大小不一；窄屏逐级折叠 */
.bento {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 16px;
  min-width: 0;
}

.b-hero { grid-column: span 8; }
.b-cal { grid-column: span 4; }
.b-stage { grid-column: span 3; }
.b-focus { grid-column: span 6; }
.b-focus.wide { grid-column: span 12; order: 10; }
.b-side {
  grid-column: span 3;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.bento > * {
  min-width: 0;
  animation: section-in 520ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.bento > :nth-child(2) { animation-delay: 60ms; }
.bento > :nth-child(3) { animation-delay: 100ms; }
.bento > :nth-child(4) { animation-delay: 140ms; }
.bento > :nth-child(5) { animation-delay: 180ms; }

/* 便当格卡片的统一外观（子组件根节点带 .tile） */
.bento :deep(.tile) {
  padding: 16px 16px 14px;
  border-radius: 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: 0 1px 3px var(--shadow-l1);
  transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
}

.bento :deep(.tile:hover) {
  transform: translateY(-2px);
  box-shadow: 0 12px 28px var(--shadow-l1);
}

.bento :deep(.tile-head) {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.bento :deep(.tile-head h3) {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-strong);
}

.bento :deep(.tile-sub) {
  font-size: 12px;
  color: var(--text-muted);
}

.home-board {
  scroll-margin-top: 12px;
}

@media (max-width: 1199px) {
  .b-hero { grid-column: span 12; }
  .b-cal { grid-column: span 5; }
  .b-stage { grid-column: span 7; }
  .b-focus { grid-column: span 12; }
  .b-side {
    grid-column: span 12;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
  }
}

@media (max-width: 760px) {
  .b-cal,
  .b-stage { grid-column: span 12; }
  .b-side { grid-template-columns: 1fr; }
}

.toast {
  position: fixed;
  left: 50%;
  bottom: 24px;
  z-index: 60;
  transform: translateX(-50%);
  padding: 9px 16px;
  border-radius: 12px;
  background: var(--text-strong);
  color: var(--surface);
  font-size: 13px;
  box-shadow: 0 12px 28px var(--shadow-l2);
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 200ms ease, transform 200ms ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translate(-50%, 8px);
}

.home-left > *,
.home-right > * {
  animation: section-in 520ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.home-left > :nth-child(1) { animation-delay: 90ms; }
.home-left > :nth-child(2) { animation-delay: 170ms; }
.home-right > :nth-child(1) { animation-delay: 120ms; }
.home-right > :nth-child(2) { animation-delay: 200ms; }

@keyframes section-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.home-left {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.home-right {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.stack {
  display: flex;
  flex-direction: column;
}

.error-view {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 60px 20px;
  color: var(--text-muted);
}

/* 主题与语义 Token 契约锚点（纯 CSS 变量消费，零硬编码 Hex） */
.theme-contract-probe {
  background: linear-gradient(
    135deg,
    var(--aurora-start),
    var(--aurora-mid),
    var(--aurora-end)
  );
  color: var(--primary-grad-start);
  border-color: var(--primary-grad-end);
  box-shadow: 0 2px 8px var(--shadow-l1), 0 4px 14px var(--shadow-l2), 0 6px 16px var(--shadow-l2);
}

.theme-palette-anchor {
  background-color: var(--app-bg);
  color: var(--surface);
  border-color: var(--surface-soft);
  outline-color: var(--elevated-surface);
}

.theme-status-anchor {
  color: var(--warning);
  background-color: var(--success);
}

.theme-accent-anchor {
  color: var(--accent-indigo);
  background-color: var(--accent-cyan);
  border-color: var(--accent-rose);
  outline-color: var(--accent-emerald);
}

/* 全局动效契约 (规范 7.2) */
@media (prefers-reduced-motion: reduce) {
  .dashboard-root,
  .dashboard-root * {
    animation: none !important;
    transition: none !important;
  }
  .enter {
    opacity:1 !important;
    transform:none !important;
  }
}

:global(body.motion-disabled .dashboard-root),
:global(body.motion-disabled .dashboard-root *),
:global([data-motion="disabled"] .dashboard-root),
:global([data-motion="disabled"] .dashboard-root *){
  animation: none !important;
  transition: none !important;
}

:global(body.page-hidden .dashboard-root),
:global(body.page-hidden .dashboard-root *){
  animation-play-state: paused !important;
}
</style>
