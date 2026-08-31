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

const reqOpenText = computed(() => {
  const v = summary.value?.stats?.req_open
  return v != null ? String(v) : '–'
})

const dailyDoneText = computed(() => {
  const s = summary.value?.stats
  if (!s || s.daily_done == null) return '–'
  return `${s.daily_done}/${s.daily_total ?? 5}`
})

const releaseDaysText = computed(() => {
  const rel = summary.value?.release
  if (!rel || rel.days_left == null) return '–'
  return `D-${rel.days_left}`
})

const releaseTotalText = computed(() => {
  const rel = summary.value?.release
  return rel && rel.total != null ? String(rel.total) : '–'
})

const releaseItemsNote = computed(() => {
  const rel = summary.value?.release
  return `已完成 ${rel ? (rel.done ?? 0) : 0}`
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
        <button class="btn btn-primary" @click="onNavClick(10)">
          <svg><use href="#i-plus" /></svg>新建需求
        </button>
      </div>
    </div>

    <!-- 4 个统计指标卡片 -->
    <div class="stats enter">
      <div class="glass stat">
        <div class="ic c1"><svg><use href="#i-req" /></svg></div>
        <b>{{ reqOpenText }}</b>
        <div class="lbl">待办需求</div>
        <span v-if="summary.stats?.req_trend" class="trend up">{{ summary.stats.req_trend }}</span>
      </div>

      <div class="glass stat">
        <div class="ic c2"><svg><use href="#i-daily" /></svg></div>
        <b>{{ dailyDoneText }}</b>
        <div class="lbl">本周日报</div>
        <span v-if="summary.stats?.daily_note" class="trend">{{ summary.stats.daily_note }}</span>
      </div>

      <div class="glass stat">
        <div class="ic c3"><svg><use href="#i-rocket" /></svg></div>
        <b>{{ releaseDaysText }}</b>
        <div class="lbl">发版倒计时</div>
        <span v-if="summary.release?.version" class="trend hot">{{ summary.release.version }}</span>
      </div>

      <div class="glass stat">
        <div class="ic c4"><svg><use href="#i-db" /></svg></div>
        <b>{{ releaseTotalText }}</b>
        <div class="lbl">发版清单事项</div>
        <span class="trend">{{ releaseItemsNote }}</span>
      </div>
    </div>

    <!-- 主工作区栅格：最近需求 + 发版进度 -->
    <div class="grid enter">
      <!-- 最近需求 -->
      <div class="card pad">
        <div class="ph">
          <span class="tt">最近需求</span>
          <span class="sub">RECENT</span>
          <span class="sp"></span>
        </div>
        <div class="req-list">
          <template v-if="summary.recent && summary.recent.length > 0">
            <div
              v-for="(r, idx) in summary.recent"
              :key="idx"
              class="ck"
              @click="onNavClick(r.nav ?? 10)"
            >
              <span class="dot" :style="{ background: r.color || '#C9CCDD' }"></span>
              <span class="t">
                <b v-if="r.code">{{ r.code }}</b>&nbsp; {{ r.title || '未命名需求' }}
              </span>
              <span class="chip" :class="r.status || 'run'">
                <i></i>{{ statusLabel(r.status) }}
              </span>
            </div>
          </template>
          <div v-else class="note">暂无需求记录</div>
        </div>
      </div>

      <!-- 发版进度 -->
      <div class="card pad">
        <div class="ph">
          <span class="tt">发版进度</span>
          <span class="sub">RELEASE {{ summary.release?.version || '' }}</span>
          <span class="sp"></span>
        </div>
        <div class="rel">
          <div class="ric"><svg><use href="#i-rocket" /></svg></div>
          <div>
            <b>{{ (summary.release?.version || '–') }} 发版联动</b>
            <div class="rs">{{ summary.release?.date_text || '计划日期待定' }}</div>
          </div>
          <span class="dchip">{{ releaseDaysText }}</span>
        </div>

        <div class="progress">
          <div class="fill" :style="{ width: `${releasePercent}%` }"></div>
        </div>
        <div class="plabel">
          <span>整体进度</span>
          <span>
            <b>{{ releasePercent }}%</b> · 已完成 {{ summary.release?.done ?? 0 }} / {{ summary.release?.total ?? 0 }} 项
          </span>
        </div>

        <div class="checklist">
          <div
            v-for="(c, idx) in (summary.checklist || [])"
            :key="idx"
            class="ck"
          >
            <span class="dot" :style="{ background: c.color || '#C9CCDD' }"></span>
            <span class="t">{{ c.t }}</span>
            <span v-if="c.mini" class="mini">{{ c.mini }}</span>
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
  --ink:#232A4D; --ink-2:#5A6284; --ink-3:#9AA1BE;
  --glass:rgba(255,255,255,.62); --edge:rgba(35,42,77,.08);
  --c1:#6366F1; --ok:#10B981; --warn:#F59E0B;
  --grad:linear-gradient(115deg,#6366F1,#A855F7 50%,#EC4899);
  --r-lg:20px; --r-sm:12px;
  --font:"Segoe UI","Microsoft YaHei UI","Microsoft YaHei","PingFang SC",sans-serif;
}
* { margin:0; padding:0; box-sizing:border-box; }
html, body { min-height:100vh; }
#app { min-height:100vh; }
body {
  font-family:var(--font); color:var(--ink); min-height:100vh; -webkit-font-smoothing:antialiased;
  background:
    radial-gradient(520px 360px at -80px -80px, rgba(14,165,233,.30), transparent 70%),
    radial-gradient(560px 420px at 108% 112%, rgba(99,102,241,.30), transparent 70%),
    radial-gradient(420px 320px at 92% -60px, rgba(236,72,153,.20), transparent 70%),
    linear-gradient(180deg,#EEF1FB,#EAEEFB);
}
.content { padding:24px 28px 34px; min-width:0; }
.glass { background:var(--glass); border:1px solid rgba(255,255,255,.85); box-shadow:0 8px 28px rgba(80,90,180,.10); backdrop-filter:blur(20px) saturate(170%); border-radius:var(--r-lg); }
.card { background:rgba(255,255,255,.9); border:1px solid var(--edge); border-radius:var(--r-lg); box-shadow:0 8px 28px rgba(80,90,180,.08); }
.hero { display:flex; align-items:center; gap:18px; flex-wrap:wrap; padding:20px 24px; margin-bottom:16px; }
.hero h1 { font-size:22px; font-weight:800; }
.hero h1 em { font-style:normal; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; }
.hero p { font-size:12.5px; color:var(--ink-2); margin-top:7px; font-weight:600; }
.hero .acts { margin-left:auto; display:flex; gap:10px; flex-wrap:wrap; }
.btn { display:inline-flex; align-items:center; gap:7px; height:36px; padding:0 15px; border-radius:12px; font-size:12.5px; font-weight:700; font-family:var(--font); cursor:pointer; border:1px solid transparent; transition:.22s; }
.btn svg { width:15px; height:15px; }
.btn-primary { background:var(--grad); color:#fff; box-shadow:0 8px 22px rgba(168,85,247,.4); }
.btn-primary:hover { transform:translateY(-1.5px); box-shadow:0 12px 28px rgba(168,85,247,.5); }
.btn-ghost { background:transparent; color:var(--ink-2); } .btn-ghost:hover { color:var(--c1); background:rgba(99,102,241,.08); }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:16px; }
.stat { padding:17px 19px; position:relative; overflow:hidden; transition:.25s; }
.stat::before { content:""; position:absolute; top:0; left:12%; right:12%; height:2.5px; border-radius:99px; background:var(--grad); opacity:0; transition:.3s; }
.stat:hover { transform:translateY(-4px); box-shadow:0 18px 40px rgba(80,90,180,.16); }
.stat:hover::before { opacity:1; }
.stat .ic { width:37px; height:37px; border-radius:12px; display:grid; place-items:center; margin-bottom:12px; color:#fff; }
.stat .ic svg { width:18px; height:18px; }
.ic.c1 { background:linear-gradient(135deg,#6366F1,#818CF8); } .ic.c2 { background:linear-gradient(135deg,#0EA5E9,#38BDF8); }
.ic.c3 { background:linear-gradient(135deg,#EC4899,#F472B6); } .ic.c4 { background:linear-gradient(135deg,#10B981,#34D399); }
.stat b { font-size:25px; font-weight:800; font-variant-numeric:tabular-nums; }
.stat b em { font-style:normal; font-size:13px; color:var(--ink-3); }
.stat .lbl { font-size:11.5px; color:var(--ink-2); margin-top:3px; }
.trend { position:absolute; top:14px; right:14px; font-size:10px; font-weight:700; padding:3px 8px; border-radius:99px; color:var(--ink-3); background:rgba(90,98,132,.07); border:1px solid var(--edge); }
.trend.up { color:#047857; background:rgba(16,185,129,.1); border-color:rgba(16,185,129,.3); }
.trend.hot { color:#B45309; background:rgba(245,158,11,.1); border-color:rgba(245,158,11,.35); }
.grid { display:grid; grid-template-columns:1.35fr 1fr; gap:14px; align-items:stretch; margin-bottom:16px; }
.card.pad { padding:18px 20px; display:flex; flex-direction:column; }
.ph { display:flex; align-items:center; gap:9px; margin-bottom:12px; }
.ph .tt { font-size:14px; font-weight:800; } .ph .sub { font-size:9.5px; font-weight:800; letter-spacing:1px; color:var(--ink-3); }
.ph .sp { flex:1; }
.req-list, .checklist { display:flex; flex-direction:column; gap:2px; flex:1; }
.ck { display:flex; align-items:center; gap:10px; padding:9px 9px; border-radius:11px; font-size:12px; font-weight:600; color:var(--ink-2); cursor:pointer; transition:.18s; }
.ck:hover { background:#FAF8FF; color:var(--ink); transform:translateX(3px); }
.ck .dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.ck .t { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ck .t b { color:var(--c1); font-family:Consolas,monospace; font-size:10px; letter-spacing:.4px; }
.ck .mini { font-size:9.5px; color:var(--ink-3); font-weight:700; }
.chip { font-size:9.5px; font-weight:800; padding:3px 9px; border-radius:99px; display:inline-flex; align-items:center; gap:5px; flex-shrink:0; }
.chip i { width:5px; height:5px; border-radius:50%; }
.chip.run { color:#B45309; background:rgba(245,158,11,.12); border:1px solid rgba(245,158,11,.35); } .chip.run i { background:var(--warn); }
.chip.rev { color:#1D4ED8; background:rgba(59,130,246,.1); border:1px solid rgba(59,130,246,.3); } .chip.rev i { background:#3B82F6; }
.chip.ok { color:#047857; background:rgba(16,185,129,.1); border:1px solid rgba(16,185,129,.35); } .chip.ok i { background:var(--ok); }
.note { font-size:12px; color:var(--ink-3); padding:12px 8px; text-align:center; font-weight:600; }
.progress { height:9px; border-radius:99px; background:rgba(90,98,132,.12); overflow:hidden; }
.progress .fill { height:100%; border-radius:99px; background:var(--grad); position:relative; }
.progress .fill::after { content:""; position:absolute; inset:0; background:linear-gradient(105deg,transparent 30%,rgba(255,255,255,.55) 50%,transparent 70%); animation:shimmer 2.2s linear infinite; }
@keyframes shimmer { from { transform:translateX(-100%);} to { transform:translateX(220%);} }
.plabel { display:flex; justify-content:space-between; font-size:10.5px; color:var(--ink-3); margin:7px 2px 12px; font-weight:700; }
.plabel b { color:var(--ink); }
.rel { display:flex; align-items:center; gap:11px; padding:12px 14px; margin-bottom:13px; border-radius:14px; background:linear-gradient(115deg,rgba(99,102,241,.09),rgba(168,85,247,.09) 50%,rgba(236,72,153,.09)); border:1px solid rgba(168,85,247,.25); }
.rel .ric { width:33px; height:33px; border-radius:10px; background:var(--grad); display:grid; place-items:center; color:#fff; }
.rel .ric svg { width:17px; height:17px; }
.rel b { font-size:13px; } .rel .rs { font-size:10.5px; color:var(--ink-2); margin-top:2px; }
.dchip { margin-left:auto; font-family:Consolas,monospace; font-size:14px; font-weight:800; color:#B45309; padding:5px 11px; border-radius:10px; background:rgba(245,158,11,.12); border:1px solid rgba(245,158,11,.4); }
.tools { display:grid; grid-template-columns:repeat(4,1fr); gap:13px; }
.tool { padding:15px 16px; cursor:pointer; transition:.25s; }
.tool:hover { transform:translateY(-4px); box-shadow:0 16px 34px rgba(80,90,180,.16); }
.tool .ic { width:38px; height:38px; border-radius:12px; display:grid; place-items:center; color:#fff; margin-bottom:10px; transition:.3s cubic-bezier(.34,1.56,.64,1); }
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
