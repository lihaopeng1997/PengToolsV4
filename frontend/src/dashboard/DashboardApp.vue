<script setup lang="ts">
import { computed } from 'vue'
import type { BridgeApi } from '../shared/bridge'
import type { DashboardSummary } from './types'
import IconSprite from './IconSprite.vue'
import { getDailyQuote } from './dailyQuotes'

const props = defineProps<{
  state: {
    summary: DashboardSummary | null
    bridge: BridgeApi | null
    error?: string | null
  }
}>()

const summary = computed(() => props.state.summary)
const error = computed(() => props.state.error)

const CHIP_MAP: Record<string, string> = {
  run: '进行中',
  rev: '待评审',
  ok: '已完成',
}

function statusLabel(status?: string | null): string {
  if (!status) return '进行中'
  return CHIP_MAP[status] || status
}

function statusChipClass(status?: string | null): string {
  if (status === 'ok') return 'ok'
  if (status === 'rev') return 'rev'
  return 'run'
}

function toolIconHref(icon?: string | null): string {
  return `#i-${icon || 'db'}`
}

function onNavClick(navIndex: number): void {
  props.state.bridge?.navigate(navIndex)
}

function onCreateRequirement(): void {
  if (props.state.bridge?.createRequirement) {
    props.state.bridge.createRequirement()
  } else {
    onNavClick(10)
  }
}

function onOpenRequirement(reqId?: string | null, fallbackNav = 10): void {
  if (!reqId || String(reqId).startsWith('demo-')) {
    return
  }
  if (props.state.bridge?.openRequirement) {
    props.state.bridge.openRequirement(reqId)
  } else {
    onNavClick(fallbackNav)
  }
}

const isDemoMode = computed(() => Boolean(summary.value?.is_demo || summary.value?.stats?.is_demo))

const quote = computed(() => getDailyQuote())

const reqOpenText = computed(() => {
  const v = summary.value?.stats?.req_open
  return v != null ? String(v) : '–'
})

const dailyDoneText = computed(() => {
  const s = summary.value?.stats
  if (!s || s.daily_done == null) return '–'
  return `${s.daily_done}/${s.daily_total ?? 5}`
})

const monthlyReleaseText = computed(() => {
  const v = summary.value?.stats?.monthly_release_total ?? summary.value?.release?.total
  return v != null ? String(v) : '0'
})

const monthlyReleaseNote = computed(() => {
  const done = summary.value?.stats?.monthly_release_done ?? summary.value?.release?.done ?? 0
  return isDemoMode.value ? `已完成 ${done} 项 · 示例` : `已完成 ${done} 项`
})

const completedTotalText = computed(() => {
  const v = summary.value?.stats?.completed_total
  return v != null ? String(v) : '0'
})

const completedNote = computed(() => {
  return isDemoMode.value ? '累计已结项 (示例)' : '累计已结项'
})

const releasePercent = computed(() => {
  const p = summary.value?.release?.percent
  return p != null ? Math.min(100, Math.max(0, p)) : 0
})
</script>

<template>
  <IconSprite />

  <div v-if="summary" class="content">
    <!-- 顶部欢迎栏（仅此处使用 Glass 材质） -->
    <div class="glass hero enter">
      <div class="hero-top">
        <div class="hero-text">
          <h1>
            <span>{{ summary.greeting || '下午好' }}</span>，<em>{{ summary.username || 'Lihp' }}</em> 👋
            <span v-if="isDemoMode" class="demo-tag">示例数据</span>
          </h1>
          <p>{{ summary.date_line || '本地数据已同步' }}</p>
        </div>
        <div class="acts">
          <button class="btn btn-ghost" @click="onNavClick(9)">
            <svg><use href="#i-daily" /></svg>写日报
          </button>
          <button class="btn btn-primary" @click="onCreateRequirement">
            <svg><use href="#i-plus" /></svg>新建需求
          </button>
        </div>
      </div>

      <!-- 每日经典句（本地确定性无网络公版诗文） -->
      <div class="daily-classic">
        <div class="dc-icon"><svg><use href="#i-quote" /></svg></div>
        <div class="dc-body">
          <span class="dc-tag">每日经典句</span>
          <span class="dc-text">“{{ quote.text }}”</span>
          <span class="dc-meta">{{ quote.author }} · {{ quote.source }}</span>
        </div>
      </div>
    </div>

    <!-- 4 个统计指标卡片（纯 Surface 实体卡片，非 Glass） -->
    <div class="stats enter">
      <div class="card stat clickable" tabindex="0" role="button" @click="onNavClick(10)" @keydown.enter="onNavClick(10)" @keydown.space.prevent="onNavClick(10)">
        <div class="ic c1"><svg><use href="#i-req" /></svg></div>
        <b>{{ reqOpenText }}</b>
        <div class="lbl">待办需求</div>
        <span v-if="summary.stats?.req_trend" class="trend up">{{ summary.stats.req_trend }}</span>
      </div>

      <div class="card stat clickable" tabindex="0" role="button" @click="onNavClick(9)" @keydown.enter="onNavClick(9)" @keydown.space.prevent="onNavClick(9)">
        <div class="ic c2"><svg><use href="#i-daily" /></svg></div>
        <b>{{ dailyDoneText }}</b>
        <div class="lbl">本周日报</div>
        <span v-if="summary.stats?.daily_note" class="trend">{{ summary.stats.daily_note }}</span>
      </div>

      <div class="card stat clickable" tabindex="0" role="button" @click="onNavClick(10)" @keydown.enter="onNavClick(10)" @keydown.space.prevent="onNavClick(10)">
        <div class="ic c3"><svg><use href="#i-rocket" /></svg></div>
        <b>{{ monthlyReleaseText }}</b>
        <div class="lbl">本月上线任务</div>
        <span class="trend hot">{{ monthlyReleaseNote }}</span>
      </div>

      <div class="card stat clickable" tabindex="0" role="button" @click="onNavClick(10)" @keydown.enter="onNavClick(10)" @keydown.space.prevent="onNavClick(10)">
        <div class="ic c4"><svg><use href="#i-db" /></svg></div>
        <b>{{ completedTotalText }}</b>
        <div class="lbl">已完成事项</div>
        <span class="trend">{{ completedNote }}</span>
      </div>
    </div>

    <!-- 主工作区栅格：最近需求 + 本月上线任务 -->
    <div class="grid enter">
      <!-- 最近需求 -->
      <div class="card pad">
        <div class="ph">
          <span class="tt">最近需求</span>
          <span class="sub">{{ isDemoMode ? '示例数据 · DEMO' : 'RECENT' }}</span>
          <span class="sp"></span>
          <button class="btn btn-ghost btn-xs" @click="onNavClick(10)">
            进入需求管理 →
          </button>
        </div>
        <div class="req-list">
          <template v-if="summary.recent && summary.recent.length > 0">
            <div
              v-for="(r, idx) in summary.recent"
              :key="r.id || r.code || idx"
              class="ck"
              :class="{ 'is-demo': r.is_demo }"
              :tabindex="r.is_demo ? undefined : 0"
              :role="r.is_demo ? undefined : 'button'"
              @click="!r.is_demo && onOpenRequirement(r.id, r.nav ?? 10)"
              @keydown.enter.self="!r.is_demo && onOpenRequirement(r.id, r.nav ?? 10)"
              @keydown.space.self.prevent="!r.is_demo && onOpenRequirement(r.id, r.nav ?? 10)"
            >
              <span class="dot" :class="statusChipClass(r.status)"></span>
              <span v-if="r.code" class="req-id-badge" :title="r.code">{{ r.code }}</span>
              <span class="t">
                <span class="req-title">{{ r.title || '未命名需求' }}</span>
                <span v-if="r.system" class="meta-inline"> · {{ r.system }}</span>
                <span v-if="r.actual_release_date" class="meta-inline"> · 上线: {{ r.actual_release_date }}</span>
              </span>
              <span v-if="r.test_points" class="test-points-badge" :title="'测试点: ' + r.test_points">
                <svg class="ic-xs"><use href="#i-check" /></svg>{{ r.test_points }}
              </span>
              <span class="chip" :class="statusChipClass(r.status)">
                <i></i>{{ r.status_label || statusLabel(r.status) }}
              </span>
              <button v-if="!r.is_demo" class="btn btn-ghost btn-xs row-act-btn" @click.stop="onOpenRequirement(r.id, r.nav ?? 10)">
                查看
              </button>
              <span v-else class="demo-badge">示例</span>
            </div>
          </template>
          <div v-else class="note">暂无需求记录</div>
        </div>
      </div>

      <!-- 本月上线任务 -->
      <div class="card pad">
        <div class="ph">
          <span class="tt">本月上线任务</span>
          <span class="sub">{{ isDemoMode ? '示例数据 · DEMO' : 'MONTHLY RELEASE' }}</span>
          <span class="sp"></span>
          <button class="btn btn-ghost btn-xs" @click="onNavClick(10)">
            查看全部 →
          </button>
        </div>
        <div class="rel">
          <div class="ric"><svg><use href="#i-rocket" /></svg></div>
          <div>
            <b>本月上线任务</b>
            <div class="rs">{{ isDemoMode ? '当前无真实需求，展示示例数据' : (summary.release?.date_text || '按实际上线日期归档') }}</div>
          </div>
          <span class="dchip">{{ monthlyReleaseText }} 项</span>
        </div>

        <div class="progress">
          <div class="fill" :style="{ width: `${releasePercent}%` }"></div>
        </div>
        <div class="plabel">
          <span>整体进度</span>
          <span>
            <b>{{ releasePercent }}%</b> · 已完成 {{ summary.release?.done ?? 0 }} / {{ summary.release?.total ?? 0 }} 项<template v-if="isDemoMode"> (示例)</template>
          </span>
        </div>

        <!-- 任务明细列表（已移除与 header/progress 重复的冗余单行 checklist） -->
        <div v-if="summary.monthly_release_tasks && summary.monthly_release_tasks.length" class="req-list">
          <div
            v-for="(task, idx) in summary.monthly_release_tasks"
            :key="task.id || task.code || task.title || idx"
            class="ck"
            :class="{ 'is-demo': task.is_demo }"
            :tabindex="task.is_demo ? undefined : 0"
            :role="task.is_demo ? undefined : 'button'"
            @click="!task.is_demo && onOpenRequirement(task.id, task.nav ?? 10)"
            @keydown.enter.self="!task.is_demo && onOpenRequirement(task.id, task.nav ?? 10)"
            @keydown.space.self.prevent="!task.is_demo && onOpenRequirement(task.id, task.nav ?? 10)"
          >
            <span class="dot" :class="task.done ? 'ok' : statusChipClass(task.status)"></span>
            <span v-if="task.code" class="req-id-badge" :title="task.code">{{ task.code }}</span>
            <span class="t">
              <span class="req-title">{{ task.title }}</span>
              <span v-if="task.system" class="meta-inline"> · {{ task.system }}</span>
              <span v-if="task.actual_release_date" class="meta-inline"> · 上线: {{ task.actual_release_date }}</span>
            </span>
            <span v-if="task.test_points" class="test-points-badge" :title="'测试点: ' + task.test_points">
              <svg class="ic-xs"><use href="#i-check" /></svg>{{ task.test_points }}
            </span>
            <span class="chip" :class="task.done ? 'ok' : statusChipClass(task.status)">
              <i></i>{{ task.done ? '已完成' : (task.status || '进行中') }}
            </span>
            <button v-if="!task.is_demo" class="btn btn-ghost btn-xs row-act-btn" @click.stop="onOpenRequirement(task.id, task.nav ?? 10)">
              查看
            </button>
            <span v-else class="demo-badge">示例</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 常用工具 -->
    <div class="ph-row enter">
      <span class="tt">常用工具</span>
      <span class="sub">QUICK TOOLS</span>
    </div>
    <div class="tools enter">
      <div
        v-for="t in (summary.tools || [])"
        :key="t.i"
        class="card tool"
        tabindex="0"
        role="button"
        @click="onNavClick(t.i)"
        @keydown.enter="onNavClick(t.i)"
        @keydown.space.prevent="onNavClick(t.i)"
      >
        <div class="ic" :class="t.grad || 'c1'">
          <svg><use :href="toolIconHref(t.icon)" /></svg>
        </div>
        <div class="nm">{{ t.zh }}</div>
        <div class="ds">{{ t.ds || '' }}</div>
      </div>
    </div>
  </div>

  <!-- 开发 / 离线 fallback -->
  <div v-else class="dev-fallback">
    <p>PengToolsHub · 首页</p>
    <p class="hint">{{ error ? `Bridge unavailable outside Qt · ${error}` : '加载中…' }}</p>
  </div>
</template>

<!-- 非 scoped：纯消费 ThemeManager 权威 Prism tokens，消除二次外边距与假功能 -->
<style>
:root {
  --ink: var(--text-strong);
  --ink-2: var(--text);
  --ink-3: var(--text-muted);
  --page-bg: var(--app-bg);
  --elevated: var(--elevated-surface);
  --edge: var(--border);
  --edge-strong: var(--elevated-border);
  --ok: var(--success);
  --warn: var(--warning);
  --c1: var(--primary);
  --grad: linear-gradient(115deg, var(--primary-grad-start), var(--primary-grad-end));
  --r-lg: 16px;
  --r-sm: 10px;
  --font: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
  --motion-fast: 100ms;
  --motion-standard: 150ms;
  --motion-enter: 180ms;
  --ease-out: cubic-bezier(.2, .8, .2, 1);
}

* { margin:0; padding:0; box-sizing:border-box; }
html, body { min-height:100vh; }
#app { min-height:100vh; }

body {
  font-family:var(--font); color:var(--ink); min-height:100vh; -webkit-font-smoothing:antialiased;
  background:var(--page-bg);
  position:relative;
}

body::before {
  content:"";
  position:fixed;
  inset:0;
  z-index:-1;
  pointer-events:none;
  background:
    radial-gradient(520px 360px at -80px -80px, var(--aurora-start), transparent 70%),
    radial-gradient(560px 420px at 108% 112%, var(--aurora-mid), transparent 70%),
    radial-gradient(420px 320px at 92% -60px, var(--aurora-end), transparent 70%);
  opacity:0.07;
}

html[data-theme="black"] body::before, html.dark body::before {
  opacity:0.09;
}

/* 取消 Dashboard 自身冗余的大外边距（已由 MainWindow 提供统一内容外边距） */
.content {
  padding:0 0 8px;
  min-width:0;
  overflow-x:hidden;
}

/* Glass 仅允许在 Hero 上使用 */
.glass {
  background:var(--glass-bg);
  border:1px solid var(--glass-border);
  box-shadow:0 0 0 1px var(--glass-highlight) inset, 0 4px 16px var(--shadow-l1);
  backdrop-filter:blur(14px) saturate(160%);
  border-radius:var(--r-lg);
}

/* Surface 实体卡片：统计指标卡、主工作区、常用工具全部使用 */
.card {
  background:var(--surface);
  border:1px solid var(--edge);
  border-radius:var(--r-lg);
  box-shadow:0 2px 8px var(--shadow-l1);
  min-width:0;
}

.hero {
  padding:18px 22px 16px;
  margin-bottom:16px;
  display:flex;
  flex-direction:column;
  gap:12px;
}

.hero-top {
  display:flex;
  align-items:center;
  gap:16px;
  flex-wrap:wrap;
  width:100%;
}

.hero-text {
  flex:1;
  min-width:240px;
}

.hero h1 {
  font-size:20px;
  font-weight:800;
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
}

.hero h1 em {
  font-style:normal;
  background:var(--grad);
  -webkit-background-clip:text;
  background-clip:text;
  color:transparent;
}

.demo-tag {
  font-size:10.5px;
  font-weight:700;
  padding:2px 7px;
  border-radius:6px;
  color:var(--warning);
  background:var(--warning-bg);
  border:1px solid var(--warning-border);
}

.hero p {
  font-size:12px;
  color:var(--ink-2);
  margin-top:5px;
  font-weight:600;
}

.hero .acts {
  margin-left:auto;
  display:flex;
  gap:10px;
  flex-wrap:wrap;
}

/* 每日经典句视觉呈现 */
.daily-classic {
  display:flex;
  align-items:center;
  gap:10px;
  width:100%;
  padding-top:10px;
  border-top:1px solid var(--edge);
}

.dc-icon {
  width:26px;
  height:26px;
  border-radius:8px;
  background:var(--primary-soft);
  color:var(--primary);
  display:grid;
  place-items:center;
  flex-shrink:0;
}

.dc-icon svg {
  width:14px;
  height:14px;
}

.dc-body {
  display:flex;
  align-items:baseline;
  gap:8px;
  flex-wrap:wrap;
  font-size:12px;
  min-width:0;
}

.dc-tag {
  font-size:10px;
  font-weight:700;
  color:var(--primary);
  background:var(--primary-soft);
  padding:1px 6px;
  border-radius:4px;
  letter-spacing:0.3px;
  flex-shrink:0;
}

.dc-text {
  font-weight:600;
  color:var(--ink);
}

.dc-meta {
  font-size:11px;
  color:var(--ink-3);
}

.btn {
  display:inline-flex;
  align-items:center;
  gap:7px;
  height:36px;
  padding:0 14px;
  border-radius:10px;
  font-size:12.5px;
  font-weight:700;
  font-family:var(--font);
  cursor:pointer;
  border:1px solid transparent;
  transition:background var(--motion-fast) var(--ease-out), color var(--motion-fast) var(--ease-out), transform var(--motion-fast) var(--ease-out), box-shadow var(--motion-fast) var(--ease-out);
  outline:none;
}

.btn:focus-visible { outline:2px solid var(--c1); outline-offset:2px; }
.btn:disabled { opacity:0.5; pointer-events:none; }
.btn svg { width:15px; height:15px; }

.btn-primary { background:var(--grad); color:var(--on-primary); box-shadow:0 4px 14px var(--shadow-l2); }
.btn-primary:hover { transform:translateY(-1px); box-shadow:0 6px 18px var(--shadow-l3); }
.btn-primary:active { transform:translateY(0) scale(0.98); box-shadow:0 2px 8px var(--shadow-l1); }

.btn-ghost { background:transparent; color:var(--ink-2); }
.btn-ghost:hover { color:var(--primary); background:var(--primary-soft); }
.btn-ghost:active { transform:scale(0.98); background:var(--surface-soft); }

.btn-xs { height:26px; padding:0 10px; font-size:11.5px; border-radius:7px; }
.clickable { cursor:pointer; }
.meta-inline { font-size:11px; color:var(--ink-3); font-weight:normal; }

/* 统计卡：4 列，实体 Surface */
.stats {
  display:grid;
  grid-template-columns:repeat(4, 1fr);
  gap:14px;
  margin-bottom:16px;
}

.stat {
  padding:16px 18px;
  position:relative;
  overflow:hidden;
  transition:transform var(--motion-fast) var(--ease-out), box-shadow var(--motion-fast) var(--ease-out);
  outline:none;
}

.stat:hover {
  transform:translateY(-2px);
  box-shadow:0 6px 16px var(--shadow-l2);
}

.stat:active { transform:translateY(0); }
.stat:focus-visible { outline:2px solid var(--c1); outline-offset:2px; }

.stat .ic {
  width:36px;
  height:36px;
  border-radius:10px;
  display:grid;
  place-items:center;
  margin-bottom:10px;
  color:var(--on-primary);
}

.stat .ic svg { width:18px; height:18px; }

.ic.c1 { background:linear-gradient(135deg, var(--accent-indigo), var(--primary-grad-end)); }
.ic.c2 { background:linear-gradient(135deg, var(--accent-cyan), var(--cyan)); }
.ic.c3 { background:linear-gradient(135deg, var(--accent-rose), var(--danger)); }
.ic.c4 { background:linear-gradient(135deg, var(--accent-emerald), var(--success)); }

.stat b { font-size:24px; font-weight:800; font-variant-numeric:tabular-nums; }
.stat .lbl { font-size:12px; color:var(--ink-2); margin-top:3px; }

.trend {
  position:absolute;
  top:14px;
  right:14px;
  font-size:10px;
  font-weight:700;
  padding:2px 7px;
  border-radius:99px;
  color:var(--ink-3);
  background:var(--surface-soft);
  border:1px solid var(--edge);
}

.trend.up { color:var(--success); background:var(--success-bg); border-color:var(--success-border); }
.trend.hot { color:var(--warning); background:var(--warning-bg); border-color:var(--warning-border); }

/* 主栅格：宽屏 1.25fr / 1fr */
.grid {
  display:grid;
  grid-template-columns:1.25fr 1fr;
  gap:14px;
  align-items:stretch;
  margin-bottom:16px;
}

.card.pad {
  padding:16px 18px;
  display:flex;
  flex-direction:column;
}

.ph {
  display:flex;
  align-items:center;
  gap:9px;
  margin-bottom:12px;
}

.ph .tt { font-size:14px; font-weight:800; }
.ph .sub { font-size:9.5px; font-weight:800; letter-spacing:1px; color:var(--ink-3); }
.ph .sp { flex:1; }

.req-list {
  display:flex;
  flex-direction:column;
  gap:3px;
  flex:1;
  max-height:240px;
  overflow-y:auto;
  padding-right:2px;
}

.req-list::-webkit-scrollbar { width:5px; }
.req-list::-webkit-scrollbar-thumb { background:var(--scroll-handle); border-radius:5px; }

.ck {
  display:flex;
  align-items:center;
  gap:8px;
  min-height:40px;
  padding:6px 8px;
  border-radius:8px;
  font-size:12px;
  font-weight:600;
  color:var(--ink-2);
  cursor:pointer;
  transition:background var(--motion-fast) var(--ease-out), color var(--motion-fast) var(--ease-out), transform var(--motion-fast) var(--ease-out);
  outline:none;
}

.ck:not(.is-demo):hover {
  background:var(--surface-soft);
  color:var(--ink);
  transform:translateX(2px);
}

.ck:not(.is-demo):active { transform:translateX(1px); }
.ck:not(.is-demo):focus-visible { outline:2px solid var(--c1); outline-offset:1px; }

/* 状态小圆点（纯 CSS 语义驱动，不使用内联颜色） */
.ck .dot {
  width:7px;
  height:7px;
  border-radius:50%;
  flex-shrink:0;
  background:var(--edge-strong);
}

.ck .dot.run { background:var(--warning); }
.ck .dot.rev { background:var(--cyan); }
.ck .dot.ok { background:var(--success); }

.ck .t {
  flex:1;
  min-width:0;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}

.req-id-badge {
  display:inline-flex;
  align-items:center;
  font-family:Consolas, "Courier New", monospace;
  font-size:12px;
  font-weight:600;
  line-height:1.2;
  letter-spacing:.3px;
  white-space:nowrap;
  padding:2px 6px;
  border-radius:5px;
  background:var(--primary-soft);
  color:var(--primary);
  border:1px solid var(--edge);
  flex-shrink:0;
  max-width:200px;
  overflow:hidden;
  text-overflow:ellipsis;
}

html[data-theme="black"] .req-id-badge,
html.dark .req-id-badge {
  background:var(--surface-soft);
  color:var(--primary-active, var(--primary));
  border-color:var(--edge-strong);
}

.req-title { color:var(--ink); font-weight:700; font-size:12.5px; }

.chip {
  font-size:9.5px;
  font-weight:800;
  padding:2px 8px;
  border-radius:99px;
  display:inline-flex;
  align-items:center;
  gap:5px;
  flex-shrink:0;
}

.chip i { width:5px; height:5px; border-radius:50%; }

.chip.run { color:var(--warning); background:var(--warning-bg); border:1px solid var(--warning-border); }
.chip.run i { background:var(--warning); }

.chip.rev { color:var(--cyan); background:var(--info-bg); border:1px solid var(--info-border); }
.chip.rev i { background:var(--cyan); }

.chip.ok { color:var(--success); background:var(--success-bg); border:1px solid var(--success-border); }
.chip.ok i { background:var(--success); }

.test-points-badge {
  font-size:9.5px;
  font-weight:700;
  color:var(--ink-2);
  background:var(--surface-tech, var(--surface-soft));
  border:1px solid var(--edge);
  border-radius:5px;
  padding:2px 5px;
  display:inline-flex;
  align-items:center;
  gap:3px;
  flex-shrink:0;
}

.ic-xs { width:10px; height:10px; }
.row-act-btn { opacity:0.85; margin-left:4px; flex-shrink:0; }
.row-act-btn:hover { opacity:1; }

.ck.is-demo { opacity:0.85; cursor:default; }
.ck.is-demo:hover, .ck.is-demo:active { background:transparent !important; transform:none !important; }

.demo-badge {
  font-size:10px;
  font-weight:700;
  color:var(--ink-3);
  background:var(--surface-soft);
  border:1px dashed var(--edge-strong);
  border-radius:5px;
  padding:1px 6px;
  margin-left:4px;
  flex-shrink:0;
}

.note { font-size:12px; color:var(--ink-3); padding:12px 8px; text-align:center; font-weight:600; }

/* 进度条：静态渐变，纯业务展示 */
.progress {
  height:8px;
  border-radius:99px;
  background:var(--surface-tech, var(--surface-soft));
  overflow:hidden;
}

.progress .fill {
  height:100%;
  border-radius:99px;
  background:var(--grad);
}

.plabel {
  display:flex;
  justify-content:space-between;
  font-size:10.5px;
  color:var(--ink-3);
  margin:6px 2px 10px;
  font-weight:700;
}

.plabel b { color:var(--ink); }

.rel {
  display:flex;
  align-items:center;
  gap:10px;
  padding:10px 12px;
  margin-bottom:11px;
  border-radius:12px;
  background:linear-gradient(115deg, var(--surface-soft), var(--primary-soft));
  border:1px solid var(--edge-strong);
}

.rel .ric {
  width:32px;
  height:32px;
  border-radius:8px;
  background:var(--grad);
  display:grid;
  place-items:center;
  color:var(--on-primary);
}

.rel .ric svg { width:16px; height:16px; }
.rel b { font-size:13px; }
.rel .rs { font-size:10.5px; color:var(--ink-2); margin-top:2px; }

.dchip {
  margin-left:auto;
  font-family:Consolas, monospace;
  font-size:13.5px;
  font-weight:800;
  color:var(--warning);
  padding:4px 9px;
  border-radius:8px;
  background:var(--warning-bg);
  border:1px solid var(--warning-border);
}

/* 常用工具：4 列 */
.tools {
  display:grid;
  grid-template-columns:repeat(4, 1fr);
  gap:12px;
}

.tool {
  padding:14px 15px;
  cursor:pointer;
  transition:transform var(--motion-fast) var(--ease-out), box-shadow var(--motion-fast) var(--ease-out);
  outline:none;
}

.tool:hover {
  transform:translateY(-2px);
  box-shadow:0 6px 16px var(--shadow-l2);
}

.tool:active { transform:translateY(0) scale(0.99); }
.tool:focus-visible { outline:2px solid var(--c1); outline-offset:2px; }

.tool .ic {
  width:36px;
  height:36px;
  border-radius:10px;
  display:grid;
  place-items:center;
  color:var(--on-primary);
  margin-bottom:8px;
  transition:transform var(--motion-fast) var(--ease-out);
}

.tool:hover .ic { transform:scale(1.04); }
.tool .ic svg { width:18px; height:18px; }
.tool .nm { font-size:13px; font-weight:800; }
.tool .ds { font-size:10.5px; color:var(--ink-3); margin-top:2px; }

.ph-row { display:flex; align-items:center; gap:9px; margin:0 2px 10px; }
.ph-row .tt { font-size:14px; font-weight:800; }
.ph-row .sub { font-size:9.5px; font-weight:800; letter-spacing:1px; color:var(--ink-3); }

@keyframes fadeUp {
  from { opacity:0; transform:translateY(3px); }
  to { opacity:1; transform:none; }
}

.enter {
  opacity:0;
  animation:fadeUp var(--motion-enter) var(--ease-out) forwards;
}

/* 响应式断言（严格基于实际 WebView 宽度，1100 窗口为 2x2 stats + 单列卡片） */
@media (min-width: 1040px) {
  .stats { grid-template-columns:repeat(4, minmax(0, 1fr)); }
  .grid { grid-template-columns:minmax(0, 1.25fr) minmax(0, 1fr); }
  .tools { grid-template-columns:repeat(4, minmax(0, 1fr)); }
}

@media (max-width: 1039px) {
  .stats { grid-template-columns:repeat(2, minmax(0, 1fr)); }
  .grid { grid-template-columns:minmax(0, 1fr); }
  .tools { grid-template-columns:repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 639px) {
  .stats { grid-template-columns:minmax(0, 1fr); }
  .tools { grid-template-columns:minmax(0, 1fr); }
  .hero-top { flex-direction:column; align-items:flex-start; }
  .hero .acts { margin-left:0; margin-top:8px; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation:none !important;
    transition:none !important;
  }
  .enter { opacity:1 !important; transform:none !important; }
}

/* 开发 fallback 样式 */
.dev-fallback { min-height:100vh; display:grid; place-items:center; text-align:center; padding:2rem; }
.dev-fallback .hint { opacity:.6; font-size:.85rem; margin-top:8px; }
</style>
