<script setup lang="ts">
/**
 * 首页便当格 · 主卡：问候（称呼可就地改）、一句话现状、全局搜索（需求 / 工具 / 常用命令），
 * 新建需求 + 写日报，以及 4 个紧凑指标。动效只用 CSS，关闭动效或页面隐藏时暂停。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { DashboardProgress, DashboardRelease, DashboardStats, DashboardToolItem, StageKey } from '../types'
import { STAGE_DEFS } from '../types'
import PrismIcon from '../../shared/components/PrismIcon.vue'
import { useCountUp } from '../composables/useCountUp'

const props = defineProps<{
  greeting?: string | null
  username?: string | null
  dateLine?: string | null
  stats?: DashboardStats | null
  release?: DashboardRelease | null
  progress?: DashboardProgress | null
  tools?: DashboardToolItem[] | null
  focus?: StageKey | null
  isDemo?: boolean | null
}>()

const emit = defineEmits<{
  (e: 'createRequirement'): void
  (e: 'writeDaily'): void
  (e: 'focusStage', key: StageKey | null): void
  (e: 'rename', name: string): void
  (e: 'openRequirement', id: string): void
  (e: 'navigate', navIndex: number): void
}>()

// ── 称呼就地修改：点名字进入编辑，回车保存、Esc 取消 ──
const editingName = ref(false)
const nameDraft = ref('')
const nameInput = ref<HTMLInputElement | null>(null)

async function startRename(): Promise<void> {
  nameDraft.value = props.username || ''
  editingName.value = true
  await nextTick()
  nameInput.value?.focus()
  nameInput.value?.select()
}

function commitRename(): void {
  if (!editingName.value) return
  editingName.value = false
  const name = nameDraft.value.trim()
  if (name && name !== (props.username || '')) emit('rename', name)
}

function cancelRename(): void {
  editingName.value = false
}

// ── 指标 ──
function stageCount(key: string): number {
  return props.progress?.stages?.find((stage) => stage.key === key)?.count ?? 0
}

const testPoints = computed(() => {
  const rows = (props.progress?.board ?? []).filter((row) => row.group !== 'online')
  const total = rows.reduce((sum, row) => sum + (row.tp_total ?? 0), 0)
  const done = rows.reduce((sum, row) => sum + (row.tp_done ?? 0), 0)
  return { total, done, percent: total ? Math.round((done / total) * 100) : 0 }
})

const activeTotal = useCountUp(computed(() => props.progress?.active_total ?? 0))
const releaseDone = useCountUp(computed(() => props.release?.done ?? 0))
const releaseTotal = computed(() => props.release?.total ?? 0)
const dailyDone = useCountUp(computed(() => props.stats?.daily_done ?? 0))
const dailyTotal = computed(() => props.stats?.daily_total ?? 5)
const tpPercent = useCountUp(computed(() => testPoints.value.percent))
const dailyPending = computed(() => props.stats?.daily_note === '今日未填写')

/** 一句话说明现状：只用真实统计拼句，不编造。 */
const summary = computed(() => {
  const develop = stageCount('develop')
  const testing = stageCount('testing')
  const release = stageCount('release')
  const parts: string[] = []
  if (develop) parts.push(`${develop} 个在开发`)
  if (testing) parts.push(`${testing} 个在测试`)
  if (release) parts.push(`${release} 个待上线`)
  if (!parts.length) {
    const online = props.progress?.online_month ?? 0
    return online
      ? `手上的需求都推进完了，本月已上线 ${online} 个。新需求可以直接在下方加。`
      : '手上暂时没有在推进的需求。新需求可以直接在下方加。'
  }
  const left = Math.max(0, (props.release?.total ?? 0) - (props.release?.done ?? 0))
  const tail = left ? `，本月还有 ${left} 个要上线` : ''
  return `现在 ${parts.join('、')}${tail}。`
})

// ── 全局搜索：需求 / 工具 / 常用命令，↑↓ 选择，回车打开 ──
type Hit = { kind: 'req' | 'tool' | 'cmd'; key: string; label: string; sub: string; run: () => void }

const query = ref('')
const searchOpen = ref(false)
const activeHit = ref(0)
const searchInput = ref<HTMLInputElement | null>(null)

const commands = computed<Hit[]>(() => [
  { kind: 'cmd', key: 'cmd-new', label: '新建需求', sub: '命令', run: () => emit('createRequirement') },
  { kind: 'cmd', key: 'cmd-daily', label: '写日报', sub: '命令', run: () => emit('writeDaily') },
])

const hits = computed<Hit[]>(() => {
  const q = query.value.trim().toLowerCase()
  const reqs: Hit[] = (props.progress?.board ?? [])
    .filter((item) => item.id && (!q || `${item.title || ''} ${item.code || ''} ${item.system || ''}`.toLowerCase().includes(q)))
    .slice(0, 6)
    .map((item) => ({
      kind: 'req',
      key: `req-${item.id}`,
      label: String(item.title || item.code || ''),
      sub: STAGE_DEFS.find((s) => s.key === item.group)?.label || String(item.status || ''),
      run: () => emit('openRequirement', String(item.id)),
    }))
  const tools: Hit[] = (props.tools ?? [])
    .filter((tool) => !q || `${tool.zh} ${tool.ds || ''}`.toLowerCase().includes(q))
    .slice(0, 5)
    .map((tool) => ({ kind: 'tool', key: `tool-${tool.i}`, label: tool.zh, sub: '工具', run: () => emit('navigate', tool.i) }))
  const cmds = commands.value.filter((cmd) => !q || cmd.label.includes(q))
  return q ? [...reqs, ...tools, ...cmds] : [...cmds, ...tools.slice(0, 4), ...reqs.slice(0, 3)]
})

function openSearch(): void {
  searchOpen.value = true
  activeHit.value = 0
}

function closeSearch(): void {
  window.setTimeout(() => { searchOpen.value = false }, 120)
}

function runHit(hit?: Hit): void {
  if (!hit) return
  hit.run()
  query.value = ''
  searchOpen.value = false
  searchInput.value?.blur()
}

function onSearchKey(event: KeyboardEvent): void {
  const count = hits.value.length
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    activeHit.value = count ? (activeHit.value + 1) % count : 0
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    activeHit.value = count ? (activeHit.value - 1 + count) % count : 0
  } else if (event.key === 'Enter') {
    event.preventDefault()
    runHit(hits.value[activeHit.value])
  } else if (event.key === 'Escape') {
    query.value = ''
    searchOpen.value = false
    searchInput.value?.blur()
  }
}

// 按 / 直接聚焦搜索（输入框里打字时不抢）
function onGlobalKey(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  const typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
  if (event.key === '/' && !typing) {
    event.preventDefault()
    searchInput.value?.focus()
  }
}

onMounted(() => window.addEventListener('keydown', onGlobalKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onGlobalKey))
</script>

<template>
  <section class="hero" data-testid="home-hero">
    <div class="hero-bg" aria-hidden="true">
      <span class="aurora a1"></span>
      <span class="aurora a2"></span>
      <span class="hero-grid"></span>
    </div>

    <p class="hero-eyebrow">
      {{ props.dateLine }}
      <span v-if="props.isDemo" class="hero-demo">示例数据</span>
    </p>
    <h1 class="hero-title">
      {{ props.greeting || '你好' }}，<input
        v-if="editingName"
        ref="nameInput"
        v-model="nameDraft"
        class="hero-name-input"
        maxlength="20"
        aria-label="修改称呼"
        @keydown.enter.prevent="commitRename"
        @keydown.esc.prevent="cancelRename"
        @blur="commitRename"
      /><button
        v-else
        class="hero-name"
        type="button"
        title="点击修改称呼（也可在 设置 → 外观 中修改）"
        data-action="rename"
        @click="startRename"
      >{{ props.username || 'Lihp' }}<PrismIcon class="hero-name-pen" name="edit" :size="16" /></button>
    </h1>
    <p class="hero-summary">{{ summary }}</p>

    <div class="search" :class="{ open: searchOpen }" data-testid="home-search">
      <PrismIcon class="search-icon" name="magnifier" :size="16" />
      <input
        ref="searchInput"
        v-model="query"
        class="search-input"
        type="text"
        placeholder="搜需求、工具或命令…"
        aria-label="搜索需求、工具或命令"
        @focus="openSearch"
        @blur="closeSearch"
        @input="activeHit = 0"
        @keydown="onSearchKey"
      />
      <kbd class="search-key">/</kbd>
      <ul v-if="searchOpen && hits.length" class="search-pop" role="listbox">
        <li
          v-for="(hit, index) in hits"
          :key="hit.key"
          class="search-hit"
          :class="{ on: index === activeHit, [hit.kind]: true }"
          role="option"
          :aria-selected="index === activeHit"
          @mousedown.prevent="runHit(hit)"
          @mouseenter="activeHit = index"
        >
          <span class="hit-kind">{{ hit.kind === 'req' ? '需求' : hit.kind === 'tool' ? '工具' : '命令' }}</span>
          <span class="hit-label">{{ hit.label }}</span>
          <span class="hit-sub">{{ hit.sub }}</span>
        </li>
      </ul>
    </div>

    <div class="hero-foot">
      <div class="hero-actions">
        <button class="hero-primary" type="button" data-action="create-requirement" @click="emit('createRequirement')">
          <PrismIcon name="plus" :size="15" />
          <span>新建需求</span>
        </button>
        <button
          class="hero-ghost"
          :class="{ pending: dailyPending }"
          type="button"
          data-action="write-daily"
          @click="emit('writeDaily')"
        >
          <PrismIcon name="calendar" :size="15" />
          <span>{{ dailyPending ? '今天还没写日报' : '写日报' }}</span>
        </button>
      </div>

      <div class="kpis">
        <button class="kpi" type="button" :class="{ on: !props.focus }" @click="emit('focusStage', null)">
          <strong>{{ activeTotal }}</strong><span>在推进</span>
        </button>
        <button class="kpi" type="button" :class="{ on: props.focus === 'online' }" @click="emit('focusStage', 'online')">
          <strong>{{ releaseDone }}<small>/{{ releaseTotal }}</small></strong><span>本月上线</span>
        </button>
        <div class="kpi static" :title="`测试点 ${testPoints.done}/${testPoints.total}`">
          <strong>{{ testPoints.total ? tpPercent : '—' }}<small v-if="testPoints.total">%</small></strong><span>测试点</span>
        </div>
        <button class="kpi" type="button" :class="{ warn: dailyPending }" :title="props.stats?.daily_note || ''" @click="emit('writeDaily')">
          <strong>{{ dailyDone }}<small>/{{ dailyTotal }}</small></strong><span>本周日报</span>
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.hero {
  position: relative;
  isolation: isolate;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 26px 28px 22px;
  border-radius: 24px;
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  box-shadow: 0 1px 3px var(--shadow-l1), 0 18px 40px var(--shadow-l1);
  min-width: 0;
}

.hero-bg {
  position: absolute;
  inset: 0;
  z-index: -1;
  overflow: hidden;
  border-radius: inherit;
  pointer-events: none;
}

.aurora {
  position: absolute;
  border-radius: 50%;
  filter: blur(60px);
}

.a1 {
  width: 480px;
  height: 320px;
  left: -140px;
  top: -190px;
  background: var(--aurora-end);
  opacity: 0.42;
  animation: drift-a 22s ease-in-out infinite alternate;
}

.a2 {
  width: 520px;
  height: 300px;
  right: -120px;
  bottom: -200px;
  background: var(--aurora-mid);
  opacity: 0.16;
  animation: drift-b 26s ease-in-out infinite alternate;
}

.hero-grid {
  position: absolute;
  inset: 0;
  background-image: radial-gradient(var(--border-strong) 1px, transparent 1.2px);
  background-size: 22px 22px;
  mask-image: radial-gradient(ellipse at 85% 30%, var(--text) 0%, transparent 60%);
  -webkit-mask-image: radial-gradient(ellipse at 85% 30%, var(--text) 0%, transparent 60%);
  opacity: 0.45;
}

.hero-eyebrow {
  margin: 0;
  font-size: 12px;
  letter-spacing: 0.4px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 8px;
}

.hero-demo {
  font-size: 11px;
  font-weight: 600;
  color: var(--primary);
  background: var(--primary-soft);
  border-radius: 6px;
  padding: 1px 6px;
}

.hero-title {
  margin: 0;
  font-size: 34px;
  line-height: 1.15;
  font-weight: 800;
  letter-spacing: -0.6px;
  color: var(--text-strong);
}

.hero-name {
  font: inherit;
  color: inherit;
  letter-spacing: inherit;
  background: none;
  border: none;
  padding: 0 4px;
  margin: 0 -4px;
  border-radius: 10px;
  cursor: text;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.hero-name:hover {
  background: var(--primary-soft);
}

.hero-name-pen {
  opacity: 0;
  color: var(--text-muted);
  transition: opacity 0.15s ease;
}

.hero-name:hover .hero-name-pen,
.hero-name:focus-visible .hero-name-pen {
  opacity: 1;
}

.hero-name-input {
  font: inherit;
  color: inherit;
  letter-spacing: inherit;
  width: 7em;
  max-width: 60%;
  padding: 0 6px;
  border: 2px solid var(--primary);
  border-radius: 10px;
  background: var(--surface);
  outline: none;
}

.hero-summary {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-muted);
}

/* 全局搜索 */
.search {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 12px 0 14px;
  margin-top: 4px;
  border-radius: 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: 0 1px 2px var(--shadow-l1);
  transition: border-color 160ms ease, box-shadow 160ms ease;
  z-index: 5;
}

.search.open {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-soft);
}

.search-icon {
  color: var(--text-muted);
  flex: none;
}

.search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 14px;
  color: var(--text-strong);
}

.search-key {
  flex: none;
  min-width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  border: 1px solid var(--border);
  font-size: 11px;
  color: var(--text-muted);
  background: var(--surface-soft);
}

.search-pop {
  position: absolute;
  left: 0;
  right: 0;
  top: calc(100% + 6px);
  margin: 0;
  padding: 6px;
  list-style: none;
  border-radius: 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: 0 16px 36px var(--shadow-l2);
  max-height: 320px;
  overflow-y: auto;
}

.search-hit {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 10px;
  cursor: pointer;
  font-size: 13px;
}

.search-hit.on {
  background: var(--primary-soft);
}

.hit-kind {
  font-size: 11px;
  font-weight: 700;
  color: var(--primary);
}

.search-hit.tool .hit-kind {
  color: var(--accent-cyan);
}

.search-hit.cmd .hit-kind {
  color: var(--success);
}

.hit-label {
  color: var(--text-strong);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.hit-sub {
  font-size: 11px;
  color: var(--text-muted);
}

.hero-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 6px;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.hero-primary,
.hero-ghost {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 36px;
  padding: 0 16px;
  border-radius: 11px;
  font-size: 13px;
  cursor: pointer;
  transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
}

.hero-primary {
  background: linear-gradient(115deg, var(--primary-grad-start), var(--primary-grad-end));
  color: var(--on-primary);
  border: 1px solid var(--primary);
  font-weight: 600;
  box-shadow: 0 8px 20px var(--shadow-l2);
}

.hero-primary:hover {
  transform: translateY(-1px);
}

.hero-ghost {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
}

.hero-ghost:hover {
  border-color: var(--primary);
}

.hero-ghost.pending {
  color: var(--warning);
  border-color: var(--warning-border);
  background: var(--warning-bg);
}

.kpis {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.kpi {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--surface);
  cursor: pointer;
  transition: border-color 160ms ease;
  line-height: 34px;
}

.kpi strong {
  font-size: 17px;
  font-weight: 800;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}

.kpi strong small {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-muted);
}

.kpi span {
  font-size: 12px;
  color: var(--text-muted);
}

.kpi:not(.static):hover,
.kpi.on {
  border-color: var(--primary);
}

.kpi.static {
  cursor: default;
}

.kpi.warn strong {
  color: var(--warning);
}

@keyframes drift-a {
  from { transform: translate(0, 0) scale(1); }
  to { transform: translate(80px, 40px) scale(1.1); }
}

@keyframes drift-b {
  from { transform: translate(0, 0) scale(1.06); }
  to { transform: translate(-90px, -20px) scale(0.95); }
}

@media (max-width: 640px) {
  .hero-title {
    font-size: 28px;
  }
}
</style>
