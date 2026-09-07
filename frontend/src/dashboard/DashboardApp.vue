<script setup lang="ts">
import { computed } from 'vue'
import type { BridgeApi } from '../shared/bridge'
import type { DashboardSummary } from './types'
import IconSprite from './IconSprite.vue'

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
    <!-- 顶部欢迎栏 -->
    <div class="glass hero enter">
      <div style="flex:1;min-width:280px">
        <h1>
          <span>{{ summary.greeting || '下午好' }}</span>，<em>{{ summary.username || 'Lihp' }}</em> 👋
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

    <!-- 4 个统计指标卡片 -->
    <div class="stats enter">
      <div class="glass stat clickable" @click="onNavClick(10)">
        <div class="ic c1"><svg><use href="#i-req" /></svg></div>
        <b>{{ reqOpenText }}</b>
        <div class="lbl">待办需求</div>
        <span v-if="summary.stats?.req_trend" class="trend up">{{ summary.stats.req_trend }}</span>
      </div>

      <div class="glass stat clickable" @click="onNavClick(9)">
        <div class="ic c2"><svg><use href="#i-daily" /></svg></div>
        <b>{{ dailyDoneText }}</b>
        <div class="lbl">本周日报</div>
        <span v-if="summary.stats?.daily_note" class="trend">{{ summary.stats.daily_note }}</span>
      </div>

      <div class="glass stat clickable" @click="onNavClick(10)">
        <div class="ic c3"><svg><use href="#i-rocket" /></svg></div>
        <b>{{ monthlyReleaseText }}</b>
        <div class="lbl">{{ isDemoMode ? '本月上线 (示例)' : '本月上线任务' }}</div>
        <span class="trend hot">{{ monthlyReleaseNote }}</span>
      </div>

      <div class="glass stat clickable" @click="onNavClick(10)">
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
              @click="!r.is_demo && onOpenRequirement(r.id, r.nav ?? 10)"
            >
              <span class="dot" :style="{ background: r.color || 'var(--edge-strong)' }"></span>
              <span v-if="r.code" class="req-id-badge" :title="r.code">{{ r.code }}</span>
              <span class="t">
                <span class="req-title">{{ r.title || '未命名需求' }}</span>
                <span v-if="r.system" class="meta-inline"> · {{ r.system }}</span>
                <span v-if="r.actual_release_date" class="meta-inline"> · 上线: {{ r.actual_release_date }}</span>
              </span>
              <span v-if="r.test_points" class="test-points-badge" :title="'测试点: ' + r.test_points">
                <svg class="ic-xs"><use href="#i-check" /></svg>{{ r.test_points }}
              </span>
              <span class="chip" :class="r.status || 'run'">
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
            <b>{{ isDemoMode ? '本月上线任务 (示例)' : '本月上线任务' }}</b>
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

        <div class="checklist">
          <div
            v-for="(c, idx) in (summary.checklist || [])"
            :key="idx"
            class="ck"
            @click="onNavClick(10)"
          >
            <span class="dot" :style="{ background: c.color || 'var(--edge-strong)' }"></span>
            <span class="t">{{ c.t }}</span>
            <span v-if="c.mini" class="mini">{{ c.mini }}</span>
          </div>
        </div>
        <div v-if="summary.monthly_release_tasks && summary.monthly_release_tasks.length" class="req-list" style="margin-top:12px">
          <div
            v-for="(task, idx) in summary.monthly_release_tasks"
            :key="task.id || task.code || task.title || idx"
            class="ck"
            :class="{ 'is-demo': task.is_demo }"
            @click="!task.is_demo && onOpenRequirement(task.id, task.nav ?? 10)"
          >
            <span v-if="task.code" class="req-id-badge" :title="task.code">{{ task.code }}</span>
            <span class="t">
              <span class="req-title">{{ task.title }}</span>
              <span v-if="task.system" class="meta-inline"> · {{ task.system }}</span>
              <span v-if="task.actual_release_date" class="meta-inline"> · 上线: {{ task.actual_release_date }}</span>
            </span>
            <span v-if="task.test_points" class="test-points-badge" :title="'测试点: ' + task.test_points">
              <svg class="ic-xs"><use href="#i-check" /></svg>{{ task.test_points }}
            </span>
            <span class="chip" :class="task.done ? 'ok' : 'run'">
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
        @click="onNavClick(t.i)"
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

<!-- 非 scoped：原样迁移 legacy 样式与主题 token，保持 V2 白昼玻璃质感与统一响应式 -->
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
  --r-lg: 20px;
  --r-sm: 12px;
  --font: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
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
  opacity:0.22;
}
html[data-theme="black"] body::before, html.dark body::before {
  opacity:0.16;
}
.content { padding:24px 28px 34px; min-width:0; }
.glass {
  background:var(--glass-bg);
  border:1px solid var(--glass-border);
  box-shadow:0 0 0 1px var(--glass-highlight) inset, 0 8px 28px var(--shadow-l2);
  backdrop-filter:blur(20px) saturate(170%);
  border-radius:var(--r-lg);
}
.card {
  background:var(--surface);
  border:1px solid var(--edge);
  border-radius:var(--r-lg);
  box-shadow:0 2px 8px var(--shadow-l1);
}
.hero { display:flex; align-items:center; gap:18px; flex-wrap:wrap; padding:20px 24px; margin-bottom:16px; }
.hero h1 { font-size:22px; font-weight:800; }
.hero h1 em { font-style:normal; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; }
.hero p { font-size:12.5px; color:var(--ink-2); margin-top:7px; font-weight:600; }
.hero .acts { margin-left:auto; display:flex; gap:10px; flex-wrap:wrap; }
.btn { display:inline-flex; align-items:center; gap:7px; height:36px; padding:0 15px; border-radius:12px; font-size:12.5px; font-weight:700; font-family:var(--font); cursor:pointer; border:1px solid transparent; transition:.22s; }
.btn svg { width:15px; height:15px; }
.btn-primary { background:var(--grad); color:var(--on-primary); box-shadow:0 8px 22px var(--shadow-l2); }
.btn-primary:hover { transform:translateY(-1.5px); box-shadow:0 12px 28px var(--shadow-l4); }
.btn-ghost { background:transparent; color:var(--ink-2); }
.btn-ghost:hover { color:var(--primary); background:var(--primary-soft); }
.btn-xs { height:26px; padding:0 10px; font-size:11.5px; border-radius:8px; }
.clickable { cursor:pointer; }
.meta-inline { font-size:11px; color:var(--ink-3); font-weight:normal; }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:16px; }
.stat { padding:17px 19px; position:relative; overflow:hidden; transition:.25s; }
.stat::before { content:""; position:absolute; top:0; left:12%; right:12%; height:2.5px; border-radius:99px; background:var(--grad); opacity:0; transition:.3s; }
.stat:hover { transform:translateY(-4px); box-shadow:0 12px 28px var(--shadow-l2); }
.stat:hover::before { opacity:1; }
.stat .ic { width:37px; height:37px; border-radius:12px; display:grid; place-items:center; margin-bottom:12px; color:var(--on-primary); }
.stat .ic svg { width:18px; height:18px; }
.ic.c1 { background:linear-gradient(135deg, var(--accent-indigo), var(--primary-grad-end)); }
.ic.c2 { background:linear-gradient(135deg, var(--accent-cyan), var(--cyan)); }
.ic.c3 { background:linear-gradient(135deg, var(--accent-rose), var(--danger)); }
.ic.c4 { background:linear-gradient(135deg, var(--accent-emerald), var(--success)); }
.stat b { font-size:25px; font-weight:800; font-variant-numeric:tabular-nums; }
.stat b em { font-style:normal; font-size:13px; color:var(--ink-3); }
.stat .lbl { font-size:11.5px; color:var(--ink-2); margin-top:3px; }
.trend { position:absolute; top:14px; right:14px; font-size:10px; font-weight:700; padding:3px 8px; border-radius:99px; color:var(--ink-3); background:var(--surface-soft); border:1px solid var(--edge); }
.trend.up { color:var(--success); background:var(--success-bg); border-color:var(--success-border); }
.trend.hot { color:var(--warning); background:var(--warning-bg); border-color:var(--warning-border); }
.grid { display:grid; grid-template-columns:1.35fr 1fr; gap:14px; align-items:stretch; margin-bottom:16px; }
.card.pad { padding:18px 20px; display:flex; flex-direction:column; }
.ph { display:flex; align-items:center; gap:9px; margin-bottom:12px; }
.ph .tt { font-size:14px; font-weight:800; } .ph .sub { font-size:9.5px; font-weight:800; letter-spacing:1px; color:var(--ink-3); }
.ph .sp { flex:1; }
.req-list, .checklist { display:flex; flex-direction:column; gap:2px; flex:1; }
.ck { display:flex; align-items:center; gap:10px; padding:9px 9px; border-radius:11px; font-size:12px; font-weight:600; color:var(--ink-2); cursor:pointer; transition:.18s; }
.ck:hover { background:var(--surface-soft); color:var(--ink); transform:translateX(3px); }
.ck .dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.ck .t { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.req-id-badge {
  display:inline-flex;
  align-items:center;
  font-family:Consolas, "Courier New", monospace;
  font-size:13px;
  font-weight:600;
  line-height:1.25;
  letter-spacing:.3px;
  white-space:nowrap;
  padding:2px 8px;
  border-radius:6px;
  background:var(--primary-soft);
  color:var(--primary);
  border:1px solid var(--edge);
  flex-shrink:0;
  max-width:220px;
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
.ck .mini { font-size:9.5px; color:var(--ink-3); font-weight:700; }
.chip { font-size:9.5px; font-weight:800; padding:3px 9px; border-radius:99px; display:inline-flex; align-items:center; gap:5px; flex-shrink:0; }
.chip i { width:5px; height:5px; border-radius:50%; }
.chip.run { color:var(--warning); background:var(--warning-bg); border:1px solid var(--warning-border); }
.chip.run i { background:var(--warning); }
.chip.rev { color:var(--cyan); background:var(--info-bg); border:1px solid var(--info-border); }
.chip.rev i { background:var(--cyan); }
.chip.ok { color:var(--success); background:var(--success-bg); border:1px solid var(--success-border); }
.chip.ok i { background:var(--success); }
.test-points-badge { font-size:9.5px; font-weight:700; color:var(--ink-2); background:var(--surface-tech, var(--surface-soft)); border:1px solid var(--edge); border-radius:6px; padding:2px 6px; display:inline-flex; align-items:center; gap:3px; flex-shrink:0; }
.ic-xs { width:10px; height:10px; }
.row-act-btn { opacity:0.85; margin-left:4px; flex-shrink:0; }
.row-act-btn:hover { opacity:1; }
.ck.is-demo { opacity:0.88; cursor:default; }
.demo-badge { font-size:10px; font-weight:700; color:var(--ink-3); background:var(--surface-soft); border:1px dashed var(--edge-strong); border-radius:6px; padding:2px 7px; margin-left:4px; flex-shrink:0; }
.note { font-size:12px; color:var(--ink-3); padding:12px 8px; text-align:center; font-weight:600; }
.progress { height:9px; border-radius:99px; background:var(--surface-tech, var(--surface-soft)); overflow:hidden; }
.progress .fill { height:100%; border-radius:99px; background:var(--grad); position:relative; }
.progress .fill::after { content:""; position:absolute; inset:0; background:linear-gradient(105deg,transparent 30%,var(--glass-highlight) 50%,transparent 70%); animation:shimmer 2.2s linear infinite; }
@keyframes shimmer { from { transform:translateX(-100%);} to { transform:translateX(220%);} }
.plabel { display:flex; justify-content:space-between; font-size:10.5px; color:var(--ink-3); margin:7px 2px 12px; font-weight:700; }
.plabel b { color:var(--ink); }
.rel { display:flex; align-items:center; gap:11px; padding:12px 14px; margin-bottom:13px; border-radius:14px; background:linear-gradient(115deg,var(--surface-soft),var(--primary-soft)); border:1px solid var(--edge-strong); }
.rel .ric { width:33px; height:33px; border-radius:10px; background:var(--grad); display:grid; place-items:center; color:var(--on-primary); }
.rel .ric svg { width:17px; height:17px; }
.rel b { font-size:13px; } .rel .rs { font-size:10.5px; color:var(--ink-2); margin-top:2px; }
.dchip { margin-left:auto; font-family:Consolas,monospace; font-size:14px; font-weight:800; color:var(--warning); padding:5px 11px; border-radius:10px; background:var(--warning-bg); border:1px solid var(--warning-border); }
.tools { display:grid; grid-template-columns:repeat(4,1fr); gap:13px; }
.tool { padding:15px 16px; cursor:pointer; transition:.25s; }
.tool:hover { transform:translateY(-4px); box-shadow:0 12px 28px var(--shadow-l2); }
.tool .ic { width:38px; height:38px; border-radius:12px; display:grid; place-items:center; color:var(--on-primary); margin-bottom:10px; transition:.3s cubic-bezier(.34,1.56,.64,1); }
.tool:hover .ic { transform:scale(1.12) rotate(-6deg); }
.tool .ic svg { width:18px; height:18px; }
.tool .nm { font-size:12.5px; font-weight:800; } .tool .ds { font-size:10px; color:var(--ink-3); margin-top:2px; }
.ph-row { display:flex; align-items:center; gap:9px; margin:0 2px 12px; }
.ph-row .tt { font-size:14.5px; font-weight:800; } .ph-row .sub { font-size:9.5px; font-weight:800; letter-spacing:1px; color:var(--ink-3); }
@keyframes fadeUp { from { opacity:0; transform:translateY(12px); } to { opacity:1; transform:none; } }
.enter { opacity:0; animation:fadeUp .5s cubic-bezier(.22,.9,.32,1) .05s forwards; }
@media (max-width:1180px) { .stats { grid-template-columns:repeat(2,1fr); } .grid { grid-template-columns:1fr; } .tools { grid-template-columns:repeat(2,1fr); } }
@media (prefers-reduced-motion: reduce) {
  * { animation:none !important; transition:none !important; }
  .enter { opacity:1 !important; transform:none !important; }
}

/* 开发 fallback 样式 */
.dev-fallback { min-height:100vh; display:grid; place-items:center; text-align:center; padding:2rem; }
.dev-fallback .hint { opacity:.6; font-size:.85rem; margin-top:8px; }
</style>
