<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { BridgeApi } from '../shared/bridge'
import type { NavChild, NavItem, NavModel } from './nav'
import { deriveDisplayNav, displayGroupContains } from './nav'
import IconSprite from './IconSprite.vue'
import NavIcon from './NavIcon.vue'
import NavGroupMenu from './NavGroupMenu.vue'

// Prism 晴空棱镜 Web 主侧栏。
// 导航索引、权限和叶子动作严格来自 Python navModel；这里仅派生原型分组布局。
const props = defineProps<{
  model: NavModel | null
  active: { current: number }
  bridge: BridgeApi | null
  nativePopupAvailable?: boolean
  bridgeError?: string | null
}>()

const displayNav = computed(() => deriveDisplayNav(props.model))
const activeGroupKey = computed(() => {
  const current = props.active.current
  return displayNav.value.groups.find((group) => displayGroupContains(group, current))?.key ?? null
})
const openGroupKey = ref<string | null>(null)
const groupAnchor = ref<HTMLElement | null>(null)
const openGroup = computed(() =>
  displayNav.value.groups.find((group) => group.key === openGroupKey.value) ?? null,
)

function closeGroup(restoreFocus = true): void {
  const anchor = groupAnchor.value
  openGroupKey.value = null
  groupAnchor.value = null
  if (restoreFocus) {
    void nextTick(() => anchor?.focus())
  }
}

function toggleGroup(group: typeof displayNav.value.groups[number], event: Event): void {
  const anchor = event.currentTarget as HTMLElement | null
  // A production QWebEngine sidebar is only 72/84/220/248px wide. Delegate
  // every real-shell group click to the native popup; the Teleport menu stays
  // available for an isolated browser preview whose viewport is wider than
  // the real sidebar, or for an older bridge without the new slot.
  if (props.nativePopupAvailable && window.innerWidth <= 300 && props.bridge?.openNavGroup && anchor) {
    const rect = anchor.getBoundingClientRect()
    closeGroup(false)
    props.bridge.openNavGroup(group.key, JSON.stringify({
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
    }))
    return
  }
  if (openGroupKey.value === group.key) {
    closeGroup(true)
    return
  }
  groupAnchor.value = anchor
  openGroupKey.value = group.key
}

function onNavClick(item: Pick<NavItem, 'i'> | Pick<NavChild, 'i'>): void {
  closeGroup(false)
  props.bridge?.navigate(item.i)
}

function onGroupSelect(item: NavChild): void {
  onNavClick(item)
}

function onPaletteClick(): void {
  props.bridge?.openPalette()
}

function onGroupTriggerKeydown(event: KeyboardEvent, group: typeof displayNav.value.groups[number]): void {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    toggleGroup(group, event)
  }
}

watch(() => props.active.current, () => closeGroup(false))
watch(() => props.model, () => closeGroup(false), { deep: true })
</script>

<template>
  <IconSprite />

  <div v-if="model" class="sidebar">
    <div class="brand">
      <div class="logo" aria-hidden="true"><svg class="ic-logo" style="width:36px;height:36px"><use href="#i-logo" /></svg></div>
      <div class="brand-name">PengToolsHub</div>
    </div>

    <nav class="nav" aria-label="主导航">
      <button
        v-if="displayNav.home"
        class="nav-item home-item"
        :class="{ active: active.current === displayNav.home.i }"
        type="button"
        :title="displayNav.home.tip || displayNav.home.zh"
        :aria-current="active.current === displayNav.home.i ? 'page' : undefined"
        @click="onNavClick(displayNav.home)"
      >
        <NavIcon :name="displayNav.home.icon" />
        <span class="nav-text">{{ displayNav.home.zh }}</span>
      </button>

      <div class="rail-divider" aria-hidden="true"></div>

      <template v-for="g in displayNav.groups" :key="g.key">
        <div v-if="g.dividerBefore" class="rail-divider" aria-hidden="true"></div>
        <div class="group">
          <div class="g-label">{{ g.zh }} · {{ g.en }}</div>
          <button
            class="nav-item parent group-trigger"
            :class="{ active: activeGroupKey === g.key, 'is-open': openGroupKey === g.key }"
            type="button"
            role="button"
            aria-haspopup="menu"
            :aria-expanded="openGroupKey === g.key"
            :aria-current="activeGroupKey === g.key ? 'true' : undefined"
            :title="`${g.zh} · ${g.en}`"
            @click="toggleGroup(g, $event)"
            @keydown="onGroupTriggerKeydown($event, g)"
          >
            <NavIcon :name="g.icon" />
            <span class="nav-text">{{ g.zh }}</span>
          </button>
        </div>
      </template>
    </nav>

    <div class="foot">
      <div id="foot-settings" class="foot-actions">
        <button
          v-if="model.settings"
          class="nav-item"
          :class="{ active: active.current === model.settings.i }"
          type="button"
          :title="model.settings.tip || model.settings.zh"
          :aria-current="active.current === model.settings.i ? 'page' : undefined"
          @click="onNavClick(model.settings)"
        >
          <NavIcon :name="model.settings.icon" />
          <span class="nav-text">{{ model.settings.zh }}</span>
        </button>
        <button
          class="nav-item palette-button kbd"
          type="button"
          title="打开悬浮工具栏 / 快速面板"
          aria-label="打开悬浮工具栏 / 快速面板"
          @click="onPaletteClick"
        >
          <NavIcon name="layers" />
          <span class="nav-text">悬浮工具栏</span>
        </button>
      </div>
      <div class="meta">
        <span>Author · Lihp</span>
        <span class="shortcut-hint">Ctrl+Shift+P</span>
      </div>
    </div>
  </div>

  <NavGroupMenu
    :group="openGroup"
    :open="Boolean(openGroup)"
    :anchor="groupAnchor"
    :current="active.current"
    @close="closeGroup(true)"
    @select="onGroupSelect"
  />

  <!-- 离线/等待 fallback（无 Qt bridge）：Qt 环境加载真实 navModel -->
  <div v-if="!model" class="dev-fallback">
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
  --nav-active-fill: var(--nav-active-bg);
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

.brand:hover .logo { transform: translateY(-1px); }
.brand-name { font-size: 15px; font-weight: 700; letter-spacing: 0.2px; color: var(--ink); }

.nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  margin: 0 -4px;
  padding: 0 4px;
}

.nav::-webkit-scrollbar { width: 6px; }
.nav::-webkit-scrollbar-thumb { background: var(--scroll-handle); border-radius: 6px; }
.group { margin-bottom: 4px; }

.g-label {
  /* Keep the source marker for compatibility/diagnostics; the prototype rail
     presents the group name on the button and in the popup title only. */
  display: none;
  align-items: center;
  gap: 8px;
  padding: 0 8px 5px;
  color: var(--ink-3);
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  letter-spacing: 1px;
  text-transform: uppercase;
}

.g-label::after { content: ""; flex: 1; height: 1px; background: var(--edge); opacity: 0.5; }
.rail-divider { height: 1px; margin: 8px 4px; background: var(--edge); opacity: 0.78; }

.nav-item {
  display: flex;
  align-items: center;
  width: 100%;
  height: 40px;
  gap: 10px;
  padding: 0 10px;
  margin-bottom: 2px;
  border: 0;
  border-radius: var(--r-sm);
  color: var(--ink-2);
  background: transparent;
  font: inherit;
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  user-select: none;
  transition: background var(--motion-fast) var(--ease-out), color var(--motion-fast) var(--ease-out), transform var(--motion-fast) var(--ease-out);
  outline: none;
}

.nav-item:hover,
.nav-item:focus-visible { background: var(--nav-hover-bg); color: var(--ink); transform: translateX(1px); }
.nav-item:focus-visible { outline: 2px solid var(--c1); outline-offset: 1px; }
.nav-item:active { transform: translateX(1px) scale(0.99); }
.nav-item.active { background: var(--nav-active-fill); color: var(--nav-active-text); font-weight: 600; transform: none; }
.nav-item.active svg.ic { opacity: 1; color: var(--nav-active-text); }
.group-trigger.is-open { background: var(--parent-open-bg); color: var(--ink); }
.group-trigger.active { background: var(--nav-active-fill); color: var(--nav-active-text); }

.foot { padding-top: 8px; border-top: 1px solid var(--edge); }
.foot-actions { display: flex; flex-direction: column; }
.palette-button { color: var(--c1); }
.palette-button .ic { color: var(--c1); }
.foot .meta { display: flex; align-items: center; justify-content: space-between; padding: 6px 8px 0; color: var(--ink-3); font-size: 10px; font-weight: 600; }
.shortcut-hint { color: var(--c1); font-size: 9px; }

@keyframes fadeUp { from { opacity: 0; transform: translateY(2px); } to { opacity: 1; transform: none; } }
.sidebar { animation: fadeUp var(--motion-enter) var(--ease-out); }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}

/* Icon-only responsive mode (Compact 84px / Narrow 72px). */
@media (max-width: 120px) {
  .sidebar { padding: 0 0 12px; align-items: center; }
  .brand { justify-content: center; padding: 0; height: 64px; }
  .brand-name, .g-label { display: none; }
  .nav { width: 100%; margin: 0; padding: 0; display: flex; flex-direction: column; align-items: center; }
  .group { width: 100%; display: flex; flex-direction: column; align-items: center; margin-bottom: 8px; }
  .nav-item { width: 44px; height: 44px; padding: 0; justify-content: center; gap: 0; }
  .nav-item .nav-text { display: none; }
  .rail-divider { width: 44px; margin: 6px auto; }
  .foot { width: 100%; display: flex; flex-direction: column; align-items: center; padding-top: 8px; }
  .foot-actions { width: 100%; align-items: center; }
  .foot-actions .nav-item { width: 44px; height: 44px; padding: 0; justify-content: center; }
  .foot .meta { display: none; }
}

.dev-fallback { height: 100%; display: grid; place-items: center; text-align: center; padding: 2rem; }
.dev-fallback .hint { opacity: .6; font-size: .85rem; }
</style>
