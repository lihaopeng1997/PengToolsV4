<script setup lang="ts">
/**
 * 首页右侧抽屉：就地查看与调整一个需求（或快速新建），不跳转需求管理。
 * 所有写入通过 props.act（= bridge.homeAction）完成，每次改动立即保存。
 */
import { computed, nextTick, ref, watch } from 'vue'
import type { HomeActionResult } from '../../shared/bridge'
import PrismIcon from '../../shared/components/PrismIcon.vue'
import { STAGE_DEFS, type RequirementDetail, type StageKey, type TestPoint } from '../types'

const props = defineProps<{
  open: boolean
  mode: 'detail' | 'create'
  id?: string | null
  createGroup?: StageKey | null
  act: (payload: Record<string, unknown>) => Promise<HomeActionResult>
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', id: string): void
  (e: 'navigate', navIndex: number): void
  (e: 'openInManager', id: string): void
}>()

const detail = ref<RequirementDetail | null>(null)
const statuses = ref<string[]>([])
const priorities = ref<string[]>(['普通', '重要', '紧急'])
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const savedTick = ref(0)
const newPoint = ref('')
const titleDraft = ref('')
const codeDraft = ref('')

const createForm = ref({ title: '', code: '', group: 'develop' as StageKey, date: '' })
const createTitle = ref<HTMLInputElement | null>(null)

const workStages = STAGE_DEFS.map((s) => (s.key === 'online' ? { ...s, label: '已上线' } : s))

const tpPercent = computed(() => {
  const d = detail.value
  return d && d.tp_total ? Math.round((d.tp_done / d.tp_total) * 100) : 0
})

function apply(result: HomeActionResult): boolean {
  if (!result.ok) {
    error.value = result.error || '操作失败'
    return false
  }
  error.value = ''
  if (result.item) {
    detail.value = result.item as RequirementDetail
    titleDraft.value = detail.value.title
    codeDraft.value = detail.value.code
  }
  if (result.statuses) statuses.value = result.statuses
  if (result.priorities) priorities.value = result.priorities
  return true
}

async function load(): Promise<void> {
  if (!props.id) return
  loading.value = true
  error.value = ''
  detail.value = null
  apply(await props.act({ action: 'detail', id: props.id }))
  loading.value = false
}

async function run(payload: Record<string, unknown>): Promise<void> {
  if (!detail.value) return
  saving.value = true
  const ok = apply(await props.act({ ...payload, id: detail.value.id }))
  saving.value = false
  if (ok) savedTick.value += 1
}

function setGroup(key: StageKey): void {
  if (detail.value && detail.value.group !== key) run({ action: 'move_group', group: key })
}

function setStatus(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  if (detail.value && value !== detail.value.status) run({ action: 'set_status', status: value })
}

function saveField(field: 'title' | 'code' | 'priority' | 'actual_release_date', value: string): void {
  if (!detail.value) return
  const current = String((detail.value as unknown as Record<string, unknown>)[field] ?? '')
  if (value.trim() === current) return
  if (field === 'title' && !value.trim()) {
    titleDraft.value = detail.value.title
    return
  }
  run({ action: 'update_fields', fields: { [field]: value.trim() } })
}

function savePoints(points: TestPoint[]): void {
  run({ action: 'save_test_points', points })
}

function togglePoint(point: TestPoint): void {
  if (!detail.value) return
  savePoints(detail.value.test_points.map((p) => (p.id === point.id ? { ...p, done: !p.done } : p)))
}

function removePoint(point: TestPoint): void {
  if (!detail.value) return
  savePoints(detail.value.test_points.filter((p) => p.id !== point.id))
}

function addPoint(): void {
  const text = newPoint.value.trim()
  if (!text || !detail.value) return
  newPoint.value = ''
  savePoints([...detail.value.test_points, { id: '', text, done: false }])
}

async function openDir(): Promise<void> {
  if (!detail.value) return
  const result = await props.act({ action: 'open_dir', id: detail.value.id })
  if (!result.ok) error.value = result.error || '无法打开目录'
}

async function submitCreate(): Promise<void> {
  const form = createForm.value
  if (!form.title.trim()) {
    error.value = '请填写需求标题'
    return
  }
  saving.value = true
  const result = await props.act({
    action: 'create',
    title: form.title,
    code: form.code,
    group: form.group,
    actual_release_date: form.date,
  })
  saving.value = false
  if (apply(result) && detail.value) emit('created', detail.value.id)
}

watch(
  () => [props.open, props.mode, props.id] as const,
  async ([open, mode]) => {
    if (!open) return
    error.value = ''
    if (mode === 'detail') {
      await load()
    } else {
      detail.value = null
      createForm.value = { title: '', code: '', group: props.createGroup || 'develop', date: '' }
      await nextTick()
      createTitle.value?.focus()
    }
  },
  { immediate: true },
)
</script>

<template>
  <Transition name="fade">
    <div v-if="props.open" class="scrim" @click="emit('close')" />
  </Transition>
  <Transition name="slide">
    <aside v-if="props.open" class="drawer" role="dialog" aria-modal="true" @keydown.esc="emit('close')">
      <header class="d-head">
        <span class="eyebrow">{{ props.mode === 'create' ? '新建需求' : detail?.kind || '需求' }}</span>
        <span v-if="saving" class="saving">保存中…</span>
        <span v-else-if="savedTick" :key="savedTick" class="saved"><PrismIcon name="check" :size="12" />已保存</span>
        <button class="icon-btn" type="button" aria-label="关闭" @click="emit('close')">
          <PrismIcon name="x" :size="16" />
        </button>
      </header>

      <p v-if="error" class="error">{{ error }}</p>

      <!-- 新建 -->
      <form v-if="props.mode === 'create'" class="d-body create" @submit.prevent="submitCreate">
        <label class="field">
          <span>标题</span>
          <input ref="createTitle" v-model="createForm.title" maxlength="200" placeholder="例如：车险承保规则调整" />
        </label>
        <label class="field">
          <span>需求编号 <em>可选</em></span>
          <input v-model="createForm.code" maxlength="80" placeholder="REQ-20261020-0001-SP" />
        </label>
        <div class="field">
          <span>阶段</span>
          <div class="seg">
            <button
              v-for="s in workStages"
              :key="s.key"
              type="button"
              :class="{ on: createForm.group === s.key }"
              :style="{ '--stage': s.color }"
              @click="createForm.group = s.key"
            >{{ s.label }}</button>
          </div>
        </div>
        <label class="field">
          <span>上线日期 <em>可选</em></span>
          <input v-model="createForm.date" type="date" />
        </label>
        <p class="note">SVN 地址、开发目录、SQL 等在需求管理里补充；这里先把需求记下来。</p>
        <div class="d-foot">
          <button class="btn ghost" type="button" @click="emit('close')">取消</button>
          <button class="btn primary" type="submit" :disabled="saving">创建</button>
        </div>
      </form>

      <!-- 详情 -->
      <div v-else-if="loading" class="d-body skeleton">
        <span /><span /><span />
      </div>
      <div v-else-if="detail" class="d-body">
        <input v-model="codeDraft" class="code-input" placeholder="需求编号" maxlength="80"
               @blur="saveField('code', codeDraft)" @keydown.enter="($event.target as HTMLInputElement).blur()" />
        <textarea v-model="titleDraft" class="title-input" rows="2" maxlength="200"
                  @blur="saveField('title', titleDraft)" @keydown.enter.prevent="($event.target as HTMLTextAreaElement).blur()" />
        <p v-if="detail.system" class="sys">{{ detail.system }}</p>

        <section class="block">
          <div class="block-head">
            <h3>阶段</h3>
            <select class="status-select" :value="detail.status" @change="setStatus">
              <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
            </select>
          </div>
          <div class="stepper">
            <button
              v-for="(s, i) in workStages"
              :key="s.key"
              type="button"
              class="step"
              :class="{ done: detail.stage > i, current: detail.group === s.key }"
              :style="{ '--stage': s.color }"
              @click="setGroup(s.key)"
            >
              <span class="step-bar" />
              <span class="step-label">{{ s.label }}</span>
            </button>
          </div>
          <p class="next"><PrismIcon name="arrow" :size="13" />{{ detail.next_action }}</p>
        </section>

        <section class="block grid2">
          <div class="field">
            <span>优先级</span>
            <div class="seg small">
              <button
                v-for="p in priorities"
                :key="p"
                type="button"
                :class="{ on: detail.priority === p, warn: p !== '普通' }"
                @click="saveField('priority', p)"
              >{{ p }}</button>
            </div>
          </div>
          <label class="field">
            <span>上线日期</span>
            <input type="date" :value="detail.actual_release_date"
                   @change="saveField('actual_release_date', ($event.target as HTMLInputElement).value)" />
          </label>
        </section>

        <section class="block">
          <div class="block-head">
            <h3>测试点</h3>
            <span class="muted">{{ detail.tp_done }}/{{ detail.tp_total }}</span>
          </div>
          <div v-if="detail.tp_total" class="tp-bar"><span :style="{ width: `${tpPercent}%` }" /></div>
          <ul class="points">
            <li v-for="p in detail.test_points" :key="p.id" :class="{ done: p.done }">
              <button class="check" type="button" :aria-pressed="p.done" @click="togglePoint(p)">
                <PrismIcon v-if="p.done" name="check" :size="12" />
              </button>
              <span class="pt-text">{{ p.text }}</span>
              <button class="pt-del" type="button" aria-label="删除测试点" @click="removePoint(p)">
                <PrismIcon name="trash" :size="13" />
              </button>
            </li>
          </ul>
          <form class="add-point" @submit.prevent="addPoint">
            <PrismIcon name="plus" :size="14" />
            <input v-model="newPoint" maxlength="160" placeholder="添加测试点，回车保存" />
          </form>
        </section>

        <section v-if="detail.description" class="block">
          <h3>需求说明</h3>
          <p class="desc">{{ detail.description }}</p>
        </section>

        <div class="d-foot">
          <button v-if="detail.has_local_dir" class="btn ghost" type="button" @click="openDir">
            <PrismIcon name="folder" :size="14" />开发目录
          </button>
          <button class="btn ghost" type="button" @click="emit('navigate', 9)">
            <PrismIcon name="calendar" :size="14" />写日报
          </button>
          <button class="btn ghost right" type="button" @click="emit('openInManager', detail.id)">
            <PrismIcon name="external" :size="14" />需求管理
          </button>
        </div>
      </div>
    </aside>
  </Transition>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: var(--overlay-bg);
  backdrop-filter: blur(2px);
}

.drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 41;
  width: min(460px, 92vw);
  display: flex;
  flex-direction: column;
  background: var(--elevated-surface);
  border-left: 1px solid var(--border);
  box-shadow: -18px 0 48px var(--shadow-l2);
}

.d-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 18px 6px 22px;
}

.eyebrow {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.4px;
  color: var(--primary);
}

.saving,
.saved {
  font-size: 12px;
  color: var(--text-muted);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.saved {
  color: var(--success);
  animation: fade-out 2.4s ease forwards;
}

.icon-btn {
  margin-left: auto;
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
}

.icon-btn:hover {
  background: var(--surface-soft);
  color: var(--text);
}

.error {
  margin: 4px 22px 0;
  padding: 8px 12px;
  border-radius: 10px;
  background: var(--danger-bg);
  color: var(--danger);
  font-size: 13px;
}

.d-body {
  flex: 1;
  overflow-y: auto;
  padding: 6px 22px 22px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.code-input {
  border: none;
  background: transparent;
  font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 12px;
  color: var(--text-muted);
  padding: 2px 0;
  outline: none;
}

.title-input {
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.35;
  color: var(--text-strong);
  resize: none;
  padding: 4px 6px;
  margin: -6px -6px 0;
  outline: none;
}

.title-input:hover,
.code-input:hover {
  background: var(--surface-soft);
}

.title-input:focus {
  border-color: var(--primary);
  background: var(--input-bg);
}

.sys {
  margin: -8px 0 0;
  font-size: 13px;
  color: var(--text-muted);
}

.block {
  padding: 14px;
  border-radius: 14px;
  background: var(--surface);
  border: 1px solid var(--border);
}

.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
}

.muted {
  font-size: 12px;
  color: var(--text-muted);
}

.status-select {
  height: 28px;
  padding: 0 8px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--input-bg);
  color: var(--text);
  font-size: 12px;
}

.stepper {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 4px;
}

.step {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
  text-align: left;
}

.step-bar {
  height: 6px;
  border-radius: 3px;
  background: var(--loading-track);
  transition: background 200ms ease;
}

.step.done .step-bar,
.step.current .step-bar {
  background: var(--stage);
}

.step.done .step-bar {
  opacity: 0.45;
}

.step-label {
  font-size: 12px;
  color: var(--text-muted);
}

.step.current .step-label {
  color: var(--text-strong);
  font-weight: 700;
}

.step:hover .step-bar {
  background: var(--stage);
  opacity: 0.7;
}

.next {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--primary);
}

.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}

.field em {
  font-style: normal;
  opacity: 0.7;
}

.field input {
  height: 34px;
  padding: 0 10px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--input-bg);
  color: var(--text);
  font-size: 13px;
  outline: none;
}

.field input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-soft);
}

.seg {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.seg button {
  height: 30px;
  padding: 0 12px;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: var(--input-bg);
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
}

.seg.small button {
  height: 28px;
  padding: 0 10px;
}

.seg button.on {
  border-color: var(--stage, var(--primary));
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}

.seg button.on.warn {
  border-color: var(--warning);
  background: var(--warning-bg);
  color: var(--warning);
}

.tp-bar {
  height: 6px;
  border-radius: 3px;
  background: var(--loading-track);
  overflow: hidden;
  margin-bottom: 8px;
}

.tp-bar span {
  display: block;
  height: 100%;
  background: var(--success);
  transition: width 300ms ease;
}

.points {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.points li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 7px 4px;
  border-radius: 8px;
}

.points li:hover {
  background: var(--surface-soft);
}

.check {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  margin-top: 1px;
  display: grid;
  place-items: center;
  border-radius: 5px;
  border: 1.5px solid var(--border-strong);
  background: var(--input-bg);
  color: var(--on-primary);
  cursor: pointer;
}

.points li.done .check {
  background: var(--success);
  border-color: var(--success);
}

.pt-text {
  flex: 1;
  font-size: 13px;
  color: var(--text);
  line-height: 1.45;
}

.points li.done .pt-text {
  color: var(--text-muted);
  text-decoration: line-through;
}

.pt-del {
  opacity: 0;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
}

.points li:hover .pt-del {
  opacity: 1;
}

.pt-del:hover {
  color: var(--danger);
}

.add-point {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  padding: 0 8px;
  height: 34px;
  border-radius: 10px;
  border: 1px dashed var(--border-strong);
  color: var(--text-muted);
}

.add-point input {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--text);
  outline: none;
  font-size: 13px;
}

.desc {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
  white-space: pre-wrap;
  max-height: 180px;
  overflow-y: auto;
}

.note {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}

.d-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: auto;
  padding-top: 6px;
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 14px;
  border-radius: 10px;
  font-size: 13px;
  cursor: pointer;
}

.btn.ghost {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
}

.btn.ghost:hover {
  border-color: var(--primary);
  color: var(--primary);
}

.btn.primary {
  margin-left: auto;
  border: 1px solid var(--primary);
  background: var(--primary);
  color: var(--on-primary);
  font-weight: 600;
}

.btn.right {
  margin-left: auto;
}

.skeleton span {
  display: block;
  height: 18px;
  border-radius: 8px;
  background: var(--loading-track);
  animation: shimmer 1.2s ease-in-out infinite;
}

.skeleton span:nth-child(1) { width: 40%; }
.skeleton span:nth-child(2) { width: 90%; height: 28px; }
.skeleton span:nth-child(3) { width: 100%; height: 120px; }

.fade-enter-active,
.fade-leave-active {
  transition: opacity 220ms ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-enter-active,
.slide-leave-active {
  transition: transform 300ms cubic-bezier(0.2, 0.8, 0.2, 1), opacity 300ms ease;
}

.slide-enter-from,
.slide-leave-to {
  transform: translateX(40px);
  opacity: 0;
}

@keyframes shimmer {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}

@keyframes fade-out {
  0%, 70% { opacity: 1; }
  100% { opacity: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .slide-enter-active, .slide-leave-active, .fade-enter-active, .fade-leave-active { transition: none; }
}
</style>
