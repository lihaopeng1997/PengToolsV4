<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { BridgeApi } from '../shared/bridge'
import type { DisplayNavGroup, NavChild, NavItem, NavModel } from './nav'
import { deriveDisplayNav, displayGroupContains } from './nav'
import IconSprite from './IconSprite.vue'
import NavIcon from './NavIcon.vue'
import NavGroupMenu from './NavGroupMenu.vue'
import PrismLogo from '../shared/components/PrismLogo.vue'

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
const language = computed(() => (props.model?.language === 'en' ? 'en' : 'zh'))
const activeGroupKey = computed(() => (
  displayNav.value.groups.find((group) => displayGroupContains(group, props.active.current))?.key ?? null
))
const collapsed = ref(window.innerWidth <= 120)
const openGroupKey = ref<string | null>(null)
const groupAnchor = ref<HTMLElement | null>(null)
const openGroup = computed(() => (
  displayNav.value.groups.find((group) => group.key === openGroupKey.value) ?? null
))
// 品牌只在侧栏出现一次（原生标题栏不再重复显示产品名）
const workspaceLabel = 'PengToolsHub'

let collapsedMedia: MediaQueryList | null = null

function syncCollapsed(): void {
  collapsed.value = collapsedMedia?.matches ?? window.innerWidth <= 120
}

function onCollapsedMediaChange(event: MediaQueryListEvent): void {
  collapsed.value = event.matches
}

onMounted(() => {
  collapsedMedia = window.matchMedia('(max-width: 120px)')
  syncCollapsed()
  collapsedMedia.addEventListener('change', onCollapsedMediaChange)
  window.addEventListener('resize', syncCollapsed)
})

onBeforeUnmount(() => {
  collapsedMedia?.removeEventListener('change', onCollapsedMediaChange)
  window.removeEventListener('resize', syncCollapsed)
})

function textOf(item: { zh: string; en: string } | null | undefined): string {
  if (!item) return ''
  return language.value === 'en' ? (item.en || item.zh) : item.zh
}

function onNavClick(item: Pick<NavItem, 'i'> | Pick<NavChild, 'i'>): void {
  closeGroup(false)
  props.bridge?.navigate(item.i)
}

function closeGroup(restoreFocus = true): void {
  const anchor = groupAnchor.value
  openGroupKey.value = null
  groupAnchor.value = null
  if (restoreFocus) {
    void nextTick(() => anchor?.focus())
  }
}

function toggleGroup(group: DisplayNavGroup, event: MouseEvent): void {
  const anchor = event.currentTarget as HTMLElement | null
  if (
    props.nativePopupAvailable
    && window.innerWidth <= 300
    && props.bridge?.openNavGroup
    && anchor
  ) {
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

function onGroupSelect(item: NavChild): void {
  onNavClick(item)
}

function onPaletteClick(): void {
  props.bridge?.openPalette()
}

watch(() => props.active.current, () => closeGroup(false))
watch(collapsed, () => closeGroup(false))
</script>

<template>
  <IconSprite />

  <div v-if="model" class="sidebar">
    <div class="brand">
      <div class="logo" aria-hidden="true"><PrismLogo :size="36" :halo="false" /></div>
      <div class="brand-name">{{ workspaceLabel }}</div>
    </div>

    <nav class="nav" aria-label="主导航">
      <button
        v-if="displayNav.home"
        class="nav-item home-item"
        :class="{ active: active.current === displayNav.home.i }"
        type="button"
        :title="displayNav.home.tip || textOf(displayNav.home)"
        :aria-current="active.current === displayNav.home.i ? 'page' : undefined"
        @click="onNavClick(displayNav.home)"
      >
        <NavIcon :name="displayNav.home.icon" />
        <span class="nav-text">{{ textOf(displayNav.home) }}</span>
      </button>

      <div v-if="collapsed" class="rail-divider" aria-hidden="true"></div>

      <template v-for="g in displayNav.groups" :key="g.key">
        <div v-if="collapsed && g.dividerBefore" class="rail-divider" aria-hidden="true"></div>
        <div class="group">
          <button
            v-if="collapsed"
            class="nav-item group-trigger"
            :class="{ active: activeGroupKey === g.key, 'is-open': openGroupKey === g.key }"
            type="button"
            aria-haspopup="menu"
            :aria-expanded="openGroupKey === g.key"
            :aria-current="activeGroupKey === g.key ? 'true' : undefined"
            :title="`${g.zh} · ${g.en}`"
            @click="toggleGroup(g, $event)"
          >
            <NavIcon :name="g.icon" />
            <span class="nav-text">{{ textOf(g) }}</span>
          </button>
          <template v-else>
            <div class="g-label" :title="`${g.zh} · ${g.en}`">{{ textOf(g) }}</div>
            <button
              v-for="item in g.items"
              :key="item.i"
              class="nav-item"
              :class="{ active: active.current === item.i }"
              type="button"
              :title="item.tip || textOf(item)"
              :aria-current="active.current === item.i ? 'page' : undefined"
              @click="onNavClick(item)"
            >
              <NavIcon :name="item.icon" />
              <span class="nav-text">{{ textOf(item) }}</span>
            </button>
          </template>
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
          :title="model.settings.tip || textOf(model.settings)"
          :aria-current="active.current === model.settings.i ? 'page' : undefined"
          @click="onNavClick(model.settings)"
        >
          <NavIcon :name="model.settings.icon" />
          <span class="nav-text">{{ textOf(model.settings) }}</span>
        </button>
        <button
          class="nav-item palette-button kbd"
          type="button"
          :title="language === 'en' ? 'Open floating toolbar' : '打开悬浮工具栏 / 快速面板'"
          :aria-label="language === 'en' ? 'Open floating toolbar' : '打开悬浮工具栏 / 快速面板'"
          @click="onPaletteClick"
        >
          <NavIcon name="layers" />
          <span class="nav-text">{{ language === 'en' ? 'Floating toolbar' : '悬浮工具栏' }}</span>
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
    :language="language"
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
  background: var(--app-bg, var(--sidebar-bg));
  padding: 8px 4px 8px 8px;
}

body::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(420px 300px at -40px -30px, var(--aurora-start), transparent 72%),
    radial-gradient(380px 320px at 110% 108%, var(--aurora-end), transparent 72%);
  opacity: 0.14;
}

svg.ic {
  width: 19px;
  height: 19px;
  flex-shrink: 0;
  opacity: 0.85;
  transition: opacity var(--motion-fast) var(--ease-out), transform var(--motion-standard) var(--ease-out);
}

/* Airy Frost: 悬浮圆角磨砂侧轨（保留完整全部导航入口） */
.sidebar {
  position: relative;
  z-index: 1;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 0 10px 10px;
  border-radius: 18px;
  background: var(--glass-bg, rgba(255, 255, 255, 0.86));
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  border: 1px solid rgba(255, 255, 255, 0.94);
  box-shadow: 0 8px 26px var(--shadow-l1, rgba(13, 34, 53, 0.07)), inset 0 1px 0 rgba(255, 255, 255, 0.96);
}

.brand {
  height: 58px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 4px;
}

.logo {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1);
}

.brand:hover .logo { transform: translateY(-1.5px) scale(1.03); }
.brand-name { font-size: 15px; font-weight: 700; letter-spacing: 0.2px; color: var(--ink); }

.nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  margin: 0 -4px;
  padding: 0 4px;
}

.nav::-webkit-scrollbar { width: 5px; }
.nav::-webkit-scrollbar-thumb { background: var(--scroll-handle); border-radius: 999px; }
.group { margin-bottom: 4px; }

.g-label {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px 3px;
  color: var(--ink-3);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.4px;
  line-height: 16px;
}

.g-label::after { content: ""; flex: 1; height: 1px; background: var(--edge); opacity: 0.55; }
.rail-divider { height: 1px; margin: 7px 4px; background: var(--edge); opacity: 0.78; }

.nav-item {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  width: 100%;
  height: 35px;
  gap: 10px;
  padding: 0 10px;
  margin-bottom: 2px;
  border: 1px solid transparent;
  border-radius: 12px;
  color: var(--ink-2);
  background: transparent;
  font: inherit;
  font-size: 13.5px;
  line-height: 19px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  user-select: none;
  transition:
    background 200ms cubic-bezier(0.22, 1, 0.36, 1),
    color 180ms cubic-bezier(0.22, 1, 0.36, 1),
    border-color 200ms cubic-bezier(0.22, 1, 0.36, 1),
    box-shadow 200ms cubic-bezier(0.22, 1, 0.36, 1),
    transform 180ms cubic-bezier(0.22, 1, 0.36, 1);
  outline: none;
}

.nav-item:hover,
.nav-item:focus-visible {
  background: var(--nav-hover-bg);
  color: var(--ink);
  transform: translateX(2px);
}
.nav-item:focus-visible { outline: 2px solid var(--c1); outline-offset: 1px; }
.nav-item:active { transform: scale(0.975); }
.nav-item.active {
  background: linear-gradient(135deg, var(--nav-active-fill) 0%, rgba(255, 255, 255, 0.96) 100%);
  color: var(--nav-active-text);
  border-color: rgba(125, 211, 252, 0.72);
  box-shadow: 0 3px 12px rgba(14, 165, 233, 0.14);
  font-weight: 600;
  transform: none;
}
.nav-item.active svg.ic { opacity: 1; color: var(--c1); }
.group-trigger.is-open { background: var(--nav-hover-bg); }

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
