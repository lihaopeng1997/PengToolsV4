<script setup lang="ts">
import { reactive, watch } from 'vue'
import type { BridgeApi } from '../shared/bridge'
import type { NavChild, NavItem, NavModel } from './nav'
import IconSprite from './IconSprite.vue'
import NavIcon from './NavIcon.vue'

// 视觉/行为基准：Prism 晴空棱镜 Web 主侧栏（Vue 3 + QWebChannel）。
// 导航索引、父子展开行为、QuickPanel 严格保持原有契约不变。
const props = defineProps<{
  model: NavModel | null
  active: { current: number }
  bridge: BridgeApi | null
  bridgeError?: string | null
}>()

// 父菜单展开状态：所有存在 children 的父菜单初始展开，点击仅切换展开/折叠，不导航。
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
  // parent 点击只展开/折叠 children，绝不导航
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
      <div class="logo"><svg class="ic-logo" style="width:36px;height:36px"><use href="#i-logo" /></svg></div>
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
              :title="it.tip || it.zh"
              tabindex="0"
              role="button"
              @click="onParentClick(it)"
              @keydown.enter.prevent="onParentClick(it)"
              @keydown.space.prevent="onParentClick(it)"
            >
              <NavIcon :name="it.icon" /><span class="nav-text">{{ it.zh }}</span>
              <svg class="ic chev"><use href="#i-chev" /></svg>
            </div>
            <div class="sub" :class="{ open: openParents.has(it.i) }">
              <div
                v-for="c in it.children"
                :key="c.i"
                class="nav-item sub-item"
                :class="{ active: active.current === c.i }"
                :title="c.tip || c.zh"
                tabindex="0"
                role="button"
                @click="onNavClick(c)"
                @keydown.enter.prevent="onNavClick(c)"
                @keydown.space.prevent="onNavClick(c)"
              >
                <NavIcon :name="c.icon" /><span class="nav-text">{{ c.zh }}</span>
              </div>
            </div>
          </template>
          <div
            v-else
            class="nav-item"
            :class="{ active: active.current === it.i }"
            :title="it.tip || it.zh"
            tabindex="0"
            role="button"
            @click="onNavClick(it)"
            @keydown.enter.prevent="onNavClick(it)"
            @keydown.space.prevent="onNavClick(it)"
          >
            <NavIcon :name="it.icon" /><span class="nav-text">{{ it.zh }}</span>
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
          :title="model.settings.tip || model.settings.zh"
          tabindex="0"
          role="button"
          @click="onNavClick(model.settings)"
          @keydown.enter.prevent="onNavClick(model.settings)"
          @keydown.space.prevent="onNavClick(model.settings)"
        >
          <NavIcon :name="model.settings.icon" /><span class="nav-text">{{ model.settings.zh }}</span>
        </div>
      </div>
      <div class="meta">
        <span>Author · Lihp</span>
        <span class="kbd" title="快速面板 (Ctrl+Shift+P)" tabindex="0" role="button" @click="onPaletteClick" @keydown.enter.prevent="onPaletteClick" @keydown.space.prevent="onPaletteClick">Ctrl+Shift+P</span>
      </div>
    </div>
  </div>

  <!-- 离线/等待 fallback（无 Qt bridge）：Qt 环境加载真实 navModel -->
  <div v-else class="dev-fallback">
    <p>PengToolsHub</p>
    <p class="hint">{{ bridgeError ? `连接异常 · ${bridgeError}` : '正在载入工作台…' }}</p>
  </div>
</template>

<!-- 非 scoped：消费 ThemeManager 权威 Prism tokens，并保障全屏高度与背景 -->
<style>
:root {
  --ink: var(--sidebar-text);
  --ink-2: var(--text-nav);
  --ink-3: var(--sidebar-text-muted);
  --edge: var(--sidebar-border);
  --c1: var(--primary);
  --grad: linear-gradient(115deg, var(--primary-grad-start), var(--primary-grad-end));
  --nav-hover-bg: var(--nav-hover);
  --nav-active-bg: var(--nav-active-bg);
  --nav-active-text: var(--nav-active-text);
  --parent-open-bg: var(--sidebar-highlight);
  --r-sm: 10px;
  --font: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
  --motion-fast: 100ms;
  --motion-standard: 150ms;
  --motion-enter: 180ms;
  --ease-out: cubic-bezier(.2, .8, .2, 1);
}

* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; }
#app { height: 100%; }

body {
  position: relative;
  font-family: var(--font);
  color: var(--ink);
  overflow: hidden;
  -webkit-font-smoothing: antialiased;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--edge);
}

body::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(420px 300px at -60px -40px, var(--aurora-mid), transparent 70%),
    radial-gradient(380px 320px at 110% 108%, var(--aurora-start), transparent 70%);
  opacity: 0.06;
}

html[data-theme="black"] body::before, html.dark body::before {
  opacity: 0.08;
}

svg.ic {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  opacity: 0.85;
  transition: opacity var(--motion-fast) var(--ease-out);
}

.sidebar {
  position: relative;
  z-index: 1;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 0 12px 12px;
  background: transparent;
}

.brand {
  height: 64px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 4px;
}

.logo {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform var(--motion-standard) var(--ease-out);
}

.brand:hover .logo {
  transform: translateY(-1px);
}

.brand-name {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.2px;
  color: var(--ink);
}

.nav {
  flex: 1;
  overflow-y: auto;
  margin: 0 -4px;
  padding: 0 4px;
}

.nav::-webkit-scrollbar { width: 6px; }
.nav::-webkit-scrollbar-thumb { background: var(--scroll-handle); border-radius: 6px; }

.group {
  margin-bottom: 16px;
}

.g-label {
  font-size: 11px;
  font-weight: 700;
  line-height: 16px;
  letter-spacing: 1.2px;
  color: var(--ink-3);
  padding: 0 8px 6px;
  text-transform: uppercase;
  display: flex;
  align-items: center;
  gap: 8px;
}

.g-label::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--edge);
  opacity: 0.5;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;
  padding: 0 10px;
  margin-bottom: 2px;
  border-radius: var(--r-sm);
  color: var(--ink-2);
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  cursor: pointer;
  user-select: none;
  background: transparent;
  transition: background var(--motion-fast) var(--ease-out),
              color var(--motion-fast) var(--ease-out),
              transform var(--motion-fast) var(--ease-out);
  outline: none;
}

.nav-item:hover {
  background: var(--nav-hover-bg);
  color: var(--ink);
  transform: translateX(1px);
}

.nav-item:active {
  transform: translateX(1px) scale(0.99);
}

.nav-item:focus-visible {
  outline: 2px solid var(--c1);
  outline-offset: 1px;
}

.nav-item.active {
  background: var(--nav-active-bg);
  color: var(--nav-active-text);
  font-weight: 600;
  box-shadow: none;
  transform: none;
}

.nav-item.active svg.ic {
  opacity: 1;
  color: var(--nav-active-text);
}

.parent .chev {
  margin-left: auto;
  width: 14px !important;
  height: 14px !important;
  opacity: 0.6 !important;
  transition: transform var(--motion-standard) var(--ease-out);
}

.parent.open .chev {
  transform: rotate(90deg);
}

.nav-item.parent.open {
  background: var(--parent-open-bg);
  color: var(--ink);
}

.sub {
  max-height: 0;
  overflow: hidden;
  transition: max-height var(--motion-standard) var(--ease-out);
}

.sub.open {
  max-height: 320px;
}

.sub .nav-item {
  padding-left: 20px;
  height: 36px;
  font-size: 12.5px;
}

.sub .nav-item svg.ic {
  width: 16px;
  height: 16px;
}

.foot {
  border-top: 1px solid var(--edge);
  padding-top: 8px;
}

.foot .meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 8px 0;
  font-size: 10px;
  color: var(--ink-3);
  font-weight: 600;
}

.kbd {
  font-size: 9px;
  font-weight: 700;
  color: var(--c1);
  background: var(--sidebar-highlight);
  border: 1px solid var(--edge);
  border-bottom-width: 2px;
  padding: 2px 6px;
  border-radius: 6px;
  cursor: pointer;
  transition: background var(--motion-fast) var(--ease-out);
  outline: none;
}

.kbd:hover {
  background: var(--nav-hover-bg);
}

.kbd:focus-visible {
  outline: 2px solid var(--c1);
  outline-offset: 1px;
}

@keyframes fadeUp {
  from { opacity: 0; transform: translateY(2px); }
  to { opacity: 1; transform: none; }
}

.sidebar {
  animation: fadeUp var(--motion-enter) var(--ease-out);
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation: none !important;
    transition: none !important;
  }
}

/* Icon-only responsive mode (Compact 84px / Narrow 72px) */
@media (max-width: 120px) {
  .sidebar {
    padding: 0 0 12px;
    align-items: center;
  }
  .brand {
    justify-content: center;
    padding: 0;
    height: 64px;
  }
  .brand-name {
    display: none;
  }
  .g-label {
    display: none;
  }
  .nav {
    width: 100%;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
  }
  .group {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    margin-bottom: 8px;
  }
  .nav-item {
    width: 44px;
    height: 44px;
    padding: 0;
    justify-content: center;
    gap: 0;
  }
  .nav-item .nav-text {
    display: none;
  }
  .parent .chev {
    display: none;
  }
  .sub {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
  }
  .sub .nav-item {
    padding-left: 0;
    width: 44px;
    height: 44px;
    justify-content: center;
  }
  .foot {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding-top: 8px;
  }
  .foot #foot-settings {
    width: 100%;
    display: flex;
    justify-content: center;
  }
  .foot #foot-settings .nav-item {
    width: 44px;
    height: 44px;
    padding: 0;
    justify-content: center;
  }
  .foot .meta {
    display: none;
  }
}

/* 开发 fallback（无 Qt bridge）最小占位样式 */
.dev-fallback { height: 100%; display: grid; place-items: center; text-align: center; padding: 2rem; }
.dev-fallback .hint { opacity: .6; font-size: .85rem; }
</style>
