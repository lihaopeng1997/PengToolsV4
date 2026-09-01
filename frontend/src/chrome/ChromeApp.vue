<script setup lang="ts">
import { reactive, watch } from 'vue'
import type { BridgeApi } from '../shared/bridge'
import type { NavChild, NavItem, NavModel } from './nav'
import IconSprite from './IconSprite.vue'
import NavIcon from './NavIcon.vue'

// 视觉/行为基准 = legacy resources/webui/chrome.html（V2 白昼玻璃），本组件只做
// imperative DOM → Vue reactive 的迁移，不重新设计。
const props = defineProps<{
  model: NavModel | null
  active: { current: number }
  bridge: BridgeApi | null
  bridgeError?: string | null
}>()

// 父菜单展开状态：legacy 渲染时所有 parent/sub 初始即 open，点击切换；reactive Set 承载。
// legacy 行为基准：【所有存在 children 的父菜单初始全部展开】；ensureActiveVisible 仅负责
// 后续 activeChanged 指向已折叠父级的 child 时自动重新展开。
function initialOpenParents(model: NavModel | null): number[] {
  if (!model) return []
  return model.groups.flatMap(group =>
    group.items
      .filter(item => Boolean(item.children))
      .map(item => item.i),
  )
}

const openParents = reactive(
  new Set<number>(initialOpenParents(props.model)),
)

function ensureActiveVisible(current: number): void {
  if (!props.model) return
  for (const group of props.model.groups) {
    for (const item of group.items) {
      if (item.children?.some((c: NavChild) => c.i === current)) {
        openParents.add(item.i)
      }
    }
  }
}

ensureActiveVisible(props.active.current)
watch(
  () => props.active.current,
  (idx) => ensureActiveVisible(idx),
)

function onNavClick(item: NavItem): void {
  props.bridge?.navigate(item.i)
}

function onParentClick(item: NavItem): void {
  // parent 点击只展开/折叠 children，绝不导航（与 legacy 一致）
  if (openParents.has(item.i)) {
    openParents.delete(item.i)
  } else {
    openParents.add(item.i)
  }
}

function onPaletteClick(): void {
  props.bridge?.openPalette()
}
</script>

<template>
  <IconSprite />

  <div v-if="model" class="sidebar">
    <div class="brand">
      <div class="logo"><svg class="ic" style="width:21px;height:21px"><use href="#i-logo" /></svg></div>
      <div class="brand-name">PengToolsHub</div>
    </div>

    <nav class="nav">
      <div v-for="(g, gi) in model.groups" :key="g.key ?? gi" class="group">
        <div class="g-label">{{ g.zh }} · {{ g.en }}</div>
        <template v-for="it in g.items" :key="it.i">
          <template v-if="it.children">
            <div
              class="nav-item parent"
              :class="{ open: openParents.has(it.i) }"
              @click="onParentClick(it)"
            >
              <NavIcon :name="it.icon" />{{ it.zh }}
              <svg class="ic chev"><use href="#i-chev" /></svg>
            </div>
            <div class="sub" :class="{ open: openParents.has(it.i) }">
              <div
                v-for="c in it.children"
                :key="c.i"
                class="nav-item"
                :class="{ active: active.current === c.i }"
                :title="c.tip || ''"
                @click="onNavClick(c)"
              >
                <NavIcon :name="c.icon" />{{ c.zh }}
              </div>
            </div>
          </template>
          <div
            v-else
            class="nav-item"
            :class="{ active: active.current === it.i }"
            :title="it.tip || ''"
            @click="onNavClick(it)"
          >
            <NavIcon :name="it.icon" />{{ it.zh }}
          </div>
        </template>
      </div>
    </nav>

    <div class="foot">
      <div id="foot-settings">
        <div
          v-if="model.settings"
          class="nav-item"
          :class="{ active: active.current === model.settings.i }"
          :title="model.settings.tip || ''"
          @click="onNavClick(model.settings)"
        >
          <NavIcon :name="model.settings.icon" />{{ model.settings.zh }}
        </div>
      </div>
      <div class="meta">
        <span>Author · Lihp</span>
        <span class="kbd" title="快速面板" @click="onPaletteClick">Ctrl+Shift+P</span>
      </div>
    </div>
  </div>

  <!-- 离线/等待 fallback（无 Qt bridge）：Qt 环境加载真实 navModel -->
  <div v-else class="dev-fallback">
    <p>PengToolsHub</p>
    <p class="hint">{{ bridgeError ? `连接异常 · ${bridgeError}` : '正在载入工作台…' }}</p>
  </div>
</template>

<!-- 非 scoped：样式从 legacy chrome.html 原样迁移（含 html/body 背景与高度），保持 V2 白昼玻璃视觉 -->
<style>
:root {
  --ink: var(--text-strong, #232A4D);
  --ink-2: var(--text, #5A6284);
  --ink-3: var(--text-muted, #9AA1BE);
  --glass: var(--surface, rgba(255,255,255,.62));
  --glass-strong: var(--surface-strong, rgba(255,255,255,.85));
  --edge: var(--border, rgba(35,42,77,.08));
  --c1: var(--primary, #5B5FC7);
  --grad: linear-gradient(115deg, var(--primary, #5B5FC7), var(--primary-active, #7A7ED9) 50%, var(--primary-soft, #9BA0EC));
  --r-sm: 12px;
  --font: "Segoe UI","Microsoft YaHei UI","Microsoft YaHei","PingFang SC",sans-serif;
}
html[data-theme="clear"] {
  --ink: var(--text-strong, #161D26); --ink-2: var(--text, #38424E); --ink-3: var(--text-muted, #667486);
  --c1: var(--primary, #3A5770); --grad: linear-gradient(115deg, var(--primary, #3A5770), var(--primary-active, #4F708B) 50%, var(--primary-soft, #688BA8));
}
html[data-theme="warm"] {
  --ink: var(--text-strong, #241C16); --ink-2: var(--text, #4A3B30); --ink-3: var(--text-muted, #7A6858);
  --c1: var(--primary, #8B5E3C); --grad: linear-gradient(115deg, var(--primary, #8B5E3C), var(--primary-active, #A67C52) 50%, var(--primary-soft, #C49A6C));
}
html[data-theme="black"], html.dark {
  --ink: var(--text-strong, #F4F4F5); --ink-2: var(--text, #C8C8CC); --ink-3: var(--text-muted, #8A8A90);
  --glass: var(--surface, rgba(17,17,20,.78)); --glass-strong: var(--surface-strong, rgba(22,22,24,.92));
  --edge: var(--border, rgba(255,255,255,.08));
  --c1: var(--primary, #8FBB9E); --grad: linear-gradient(115deg, var(--primary-active, #7AAB8B), var(--primary, #8FBB9E) 50%, var(--primary-soft, #A8CDB4));
}
* { margin:0; padding:0; box-sizing:border-box; }
html,body { height:100%; }
#app { height:100%; }
body {
  font-family:var(--font); color:var(--ink); overflow:hidden; -webkit-font-smoothing:antialiased;
  background:
    radial-gradient(420px 300px at -60px -40px, rgba(14,165,233,.28), transparent 70%),
    radial-gradient(380px 320px at 110% 108%, rgba(99,102,241,.26), transparent 70%),
    linear-gradient(180deg, #EEF1FB, #E9EDFA);
  border-right:1px solid rgba(35,42,77,.07);
}
html[data-theme="black"] body, html.dark body {
  background:
    radial-gradient(420px 300px at -60px -40px, rgba(143,187,158,.15), transparent 70%),
    radial-gradient(380px 320px at 110% 108%, rgba(122,171,139,.15), transparent 70%),
    linear-gradient(180deg, #09090B, #111114);
  border-right:1px solid rgba(255,255,255,.07);
}
svg.ic { width:17px; height:17px; flex-shrink:0; opacity:.8; transition:.2s; }
.sidebar { height:100%; display:flex; flex-direction:column; padding:16px 12px 12px; background:var(--glass); backdrop-filter:blur(22px) saturate(170%); }
.brand { display:flex; align-items:center; gap:10px; padding:2px 8px 14px; }
.logo { width:36px; height:36px; border-radius:11px; background:var(--grad); display:grid; place-items:center; color:#fff; box-shadow:0 6px 16px rgba(91,95,199,.4), inset 0 1px 0 rgba(255,255,255,.4); transition:transform .35s cubic-bezier(.34,1.56,.64,1); }
.logo svg { width:21px; height:21px; }
.brand:hover .logo { transform:rotate(-8deg) scale(1.06); }
.brand-name { font-size:15px; font-weight:800; letter-spacing:.2px; }
.nav { flex:1; overflow-y:auto; margin:0 -4px; padding:0 4px; }
.nav::-webkit-scrollbar { width:6px; } .nav::-webkit-scrollbar-thumb { background:rgba(90,98,132,.22); border-radius:6px; }
.group { margin-bottom:12px; }
.g-label { font-size:9.5px; font-weight:800; letter-spacing:1.6px; color:var(--ink-3); padding:0 9px 6px; text-transform:uppercase; display:flex; align-items:center; gap:7px; }
.g-label::after { content:""; flex:1; height:1px; background:linear-gradient(90deg,rgba(90,98,132,.18),transparent); }
.nav-item { display:flex; align-items:center; gap:10px; padding:8px 10px; margin-bottom:2px; border-radius:var(--r-sm); color:var(--ink-2); font-size:12.5px; font-weight:600; cursor:pointer; user-select:none; transition:background .2s,color .2s,transform .2s,box-shadow .2s; }
.nav-item:hover { background:rgba(255,255,255,.92); color:var(--ink); transform:translateX(3px); box-shadow:0 4px 12px rgba(80,90,180,.12); }
html[data-theme="black"] .nav-item:hover, html.dark .nav-item:hover { background:rgba(30,30,34,.88); color:#F4F4F5; box-shadow:0 4px 12px rgba(0,0,0,.3); }
.nav-item.active { background:var(--grad); color:#fff; font-weight:700; box-shadow:0 8px 18px rgba(91,95,199,.38), inset 0 1px 0 rgba(255,255,255,.25); }
.nav-item.active svg { opacity:1; color:#fff; }
.parent .chev { margin-left:auto; width:13px !important; height:13px !important; opacity:.55 !important; transition:transform .25s; }
.parent.open .chev { transform:rotate(90deg); }
.sub { max-height:0; overflow:hidden; transition:max-height .3s ease; }
.sub.open { max-height:320px; }
.sub .nav-item { padding-left:30px; font-size:12px; }
.sub .nav-item svg { width:14px; height:14px; }
.foot { border-top:1px solid var(--edge); padding-top:10px; }
.foot .meta { display:flex; justify-content:space-between; align-items:center; padding:6px 9px 0; font-size:10px; color:var(--ink-3); font-weight:600; }
.kbd { font-size:9px; font-weight:700; color:var(--c1); background:rgba(255,255,255,.85); border:1px solid var(--edge); border-bottom-width:2px; padding:2px 6px; border-radius:6px; cursor:pointer; }
html[data-theme="black"] .kbd, html.dark .kbd { background:rgba(30,30,34,.85); color:#8FBB9E; border-color:rgba(255,255,255,.12); }
@keyframes fadeUp { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
.sidebar { animation:fadeUp .5s cubic-bezier(.22,.9,.32,1); }
@media (prefers-reduced-motion: reduce) { * { animation:none !important; transition:none !important; } }

/* 开发 fallback（无 Qt bridge）最小占位样式 */
.dev-fallback { height:100%; display:grid; place-items:center; text-align:center; padding:2rem; }
.dev-fallback .hint { opacity:.6; font-size:.85rem; }
</style>
