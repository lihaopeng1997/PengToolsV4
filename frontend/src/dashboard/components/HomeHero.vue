<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { DashboardProgress, DashboardRelease, DashboardStats, StageKey } from '../types'
import PrismIcon from '../../shared/components/PrismIcon.vue'
import ParticleSphere from './ParticleSphere.vue'
import { useCountUp } from '../composables/useCountUp'

const props = defineProps<{
  greeting?: string | null
  username?: string | null
  dateLine?: string | null
  stats?: DashboardStats | null
  release?: DashboardRelease | null
  progress?: DashboardProgress | null
  focus?: StageKey | null
  isDemo?: boolean | null
}>()

const emit = defineEmits<{
  (e: 'createRequirement'): void
  (e: 'writeDaily'): void
  (e: 'focusStage', key: StageKey | null): void
  (e: 'rename', name: string): void
}>()

// 称呼就地修改：点名字进入编辑，回车保存、Esc 取消
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

function stageCount(key: string): number {
  return props.progress?.stages?.find((stage) => stage.key === key)?.count ?? 0
}

const counts = computed<Partial<Record<StageKey, number>>>(() => ({
  analysis: stageCount('analysis'),
  develop: stageCount('develop'),
  testing: stageCount('testing'),
  release: stageCount('release'),
  online: props.progress?.online_month ?? 0,
}))

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

/** 一句话说清今天的局面：只用真实统计拼句，不编造。 */
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
      ? `手上的需求都已推进完，本月已上线 ${online} 个。新需求可以直接在下方看板里加。`
      : '手上暂时没有在推进的需求。新需求可以直接在下方看板里加。'
  }
  const left = Math.max(0, (props.release?.total ?? 0) - (props.release?.done ?? 0))
  const tail = left ? `，本月还有 ${left} 项要上线` : ''
  return `今天 ${parts.join('、')}${tail}。`
})
</script>

<template>
  <section class="hero" data-testid="home-hero">
    <span class="aurora a1" aria-hidden="true"></span>
    <span class="aurora a2" aria-hidden="true"></span>
    <span class="hero-grid" aria-hidden="true"></span>

    <div class="hero-main">
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
          title="点击修改称呼"
          data-action="rename"
          @click="startRename"
        >{{ props.username || 'Lihp' }}<PrismIcon class="hero-name-pen" name="edit" :size="16" /></button>
      </h1>
      <p class="hero-summary">{{ summary }}</p>

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
          <span class="kpi-label">正在推进</span>
          <strong class="kpi-value">{{ activeTotal }}</strong>
        </button>
        <button class="kpi" type="button" :class="{ on: props.focus === 'online' }" @click="emit('focusStage', 'online')">
          <span class="kpi-label">本月上线</span>
          <strong class="kpi-value">{{ releaseDone }}<small>/{{ releaseTotal }}</small></strong>
        </button>
        <div class="kpi static" :title="`测试点 ${testPoints.done}/${testPoints.total}`">
          <span class="kpi-label">测试点完成</span>
          <strong class="kpi-value">{{ testPoints.total ? tpPercent : '—' }}<small v-if="testPoints.total">%</small></strong>
        </div>
        <button class="kpi" type="button" :class="{ warn: dailyPending }" :title="props.stats?.daily_note || ''" @click="emit('writeDaily')">
          <span class="kpi-label">本周日报</span>
          <strong class="kpi-value">{{ dailyDone }}<small>/{{ dailyTotal }}</small></strong>
        </button>
      </div>
    </div>

    <ParticleSphere
      class="hero-visual"
      :counts="counts"
      :focus="props.focus"
      @focus-stage="(key) => emit('focusStage', key)"
    />
  </section>
</template>

<style scoped>
.hero {
  position: relative;
  overflow: hidden;
  isolation: isolate;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(340px, 600px);
  align-items: center;
  gap: 24px;
  min-height: 340px;
  padding: 32px 28px 26px 40px;
  border-radius: 28px;
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  box-shadow: 0 2px 8px var(--shadow-l1), 0 24px 60px var(--shadow-l1);
}

.aurora {
  position: absolute;
  z-index: -2;
  border-radius: 50%;
  filter: blur(60px);
  pointer-events: none;
}

.a1 {
  width: 520px;
  height: 360px;
  left: -160px;
  top: -200px;
  background: var(--aurora-end);
  opacity: 0.45;
  animation: drift-a 20s ease-in-out infinite alternate;
}

.a2 {
  width: 560px;
  height: 340px;
  right: -40px;
  bottom: -220px;
  background: var(--aurora-mid);
  opacity: 0.16;
  animation: drift-b 24s ease-in-out infinite alternate;
}

.hero-grid {
  position: absolute;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  background-image: radial-gradient(var(--border-strong) 1px, transparent 1.2px);
  background-size: 24px 24px;
  mask-image: radial-gradient(ellipse at 72% 50%, var(--text) 0%, transparent 65%);
  -webkit-mask-image: radial-gradient(ellipse at 72% 50%, var(--text) 0%, transparent 65%);
  opacity: 0.5;
}

.hero-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
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
  font-size: 38px;
  line-height: 1.12;
  font-weight: 800;
  letter-spacing: -0.8px;
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
  max-width: 520px;
  font-size: 15px;
  line-height: 1.7;
  color: var(--text-muted);
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 4px;
}

.hero-primary,
.hero-ghost {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 40px;
  padding: 0 18px;
  border-radius: 12px;
  font-size: 14px;
  cursor: pointer;
  transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease, border-color 160ms ease;
}

.hero-primary {
  background: linear-gradient(115deg, var(--primary-grad-start), var(--primary-grad-end));
  color: var(--on-primary);
  border: 1px solid var(--primary);
  font-weight: 600;
  box-shadow: 0 10px 24px var(--shadow-l2);
}

.hero-primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 14px 28px var(--shadow-l2);
}

.hero-ghost {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
}

.hero-ghost:hover {
  border-color: var(--primary);
  transform: translateY(-1px);
}

.hero-ghost.pending {
  color: var(--warning);
  border-color: var(--warning-border);
  background: var(--warning-bg);
}

.hero-primary:active,
.hero-ghost:active {
  transform: translateY(0) scale(0.98);
}

.kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
  max-width: 560px;
}

.kpi {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: 14px;
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms ease, transform 160ms ease, box-shadow 160ms ease;
}

.kpi:not(.static):hover {
  border-color: var(--primary);
  transform: translateY(-1px);
  box-shadow: 0 6px 16px var(--shadow-l1);
}

.kpi.static {
  cursor: default;
}

.kpi.on {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-soft);
}

.kpi.warn .kpi-value {
  color: var(--warning);
}

.kpi-label {
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
}

.kpi-value {
  font-size: 24px;
  font-weight: 750;
  line-height: 1.15;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}

.kpi-value small {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-muted);
  margin-left: 1px;
}

@keyframes drift-a {
  from { transform: translate(0, 0) scale(1); }
  to { transform: translate(90px, 50px) scale(1.12); }
}

@keyframes drift-b {
  from { transform: translate(0, 0) scale(1.08); }
  to { transform: translate(-100px, -30px) scale(0.95); }
}

@media (max-width: 1100px) {
  .hero {
    grid-template-columns: 1fr;
    padding: 28px;
  }

  .hero-visual {
    justify-self: center;
  }
}

@media (max-width: 640px) {
  .kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .hero-title {
    font-size: 30px;
  }
}
</style>
