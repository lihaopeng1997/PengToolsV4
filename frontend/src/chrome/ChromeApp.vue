<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { BridgeApi } from '../shared/bridge'
import type { DisplayNavGroup, NavChild, NavItem, NavModel } from './nav'
import { deriveDisplayNav, displayGroupContains } from './nav'
import IconSprite from './IconSprite.vue'
import NavIcon from './NavIcon.vue'
import NavGroupMenu from './NavGroupMenu.vue'
import PrismLogo from '../shared/components/PrismLogo.vue'
import { installWebRipples } from '../shared/ripple'

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

// ── 侧栏减负：常用置顶 + 可折叠分组 + 精简/全部模式（仅视觉组织，不改变权威导航索引）──
const MODE_KEY = 'pengtools.sidebar.mode'
const PINS_KEY = 'pengtools.sidebar.pins'
const FOLDS_KEY = 'pengtools.sidebar.folds'
const LOW_FREQ_GROUPS = new Set(['ops', 'devtools', 'personal'])

function readStorage<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function writeStorage(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* ignore */
  }
}

const sidebarMode = ref<'focus' | 'all'>(
  readStorage<string>(MODE_KEY, 'focus') === 'all' ? 'all' : 'focus',
)
const pinnedIds = ref<number[]>(
  Array.isArray(readStorage<unknown>(PINS_KEY, []))
    ? (readStorage<unknown[]>(PINS_KEY, []).filter((v): v is number => typeof v === 'number'))
    : [],
)
const customFolds = ref<Record<string, boolean>>(readStorage<Record<string, boolean>>(FOLDS_KEY, {}))

const allNavItems = computed<NavChild[]>(() => {
  const list: NavChild[] = []
  for (const g of displayNav.value.groups) {
    for (const item of g.items) list.push(item)
  }
  return list
})

const pinnedItems = computed<NavChild[]>(() => {
  const byId = new Map(allNavItems.value.map((item) => [item.i, item]))
  return pinnedIds.value
    .map((id) => byId.get(id))
    .filter((item): item is NavChild => Boolean(item))
})

function isPinned(navIndex: number): boolean {
  return pinnedIds.value.includes(navIndex)
}

function togglePin(navIndex: number, event: MouseEvent): void {
  event.stopPropagation()
  const next = isPinned(navIndex)
    ? pinnedIds.value.filter((id) => id !== navIndex)
    : [...pinnedIds.value, navIndex]
  pinnedIds.value = next
  writeStorage(PINS_KEY, next)
}

function setSidebarMode(mode: 'focus' | 'all'): void {
  sidebarMode.value = mode
  customFolds.value = {}
  writeStorage(MODE_KEY, mode)
  writeStorage(FOLDS_KEY, {})
}

function isGroupFolded(groupKey: string): boolean {
  if (activeGroupKey.value === groupKey) return false
  if (Object.prototype.hasOwnProperty.call(customFolds.value, groupKey)) {
    return Boolean(customFolds.value[groupKey])
  }
  return sidebarMode.value === 'focus' && LOW_FREQ_GROUPS.has(groupKey)
}

function toggleGroupFold(groupKey: string): void {
  const currentlyFolded = isGroupFolded(groupKey)
  customFolds.value = { ...customFolds.value, [groupKey]: !currentlyFolded }
  writeStorage(FOLDS_KEY, customFolds.value)
}

let collapsedMedia: MediaQueryList | null = null
let removeWebRipples: (() => void) | null = null

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
  removeWebRipples = installWebRipples()
})

onBeforeUnmount(() => {
  collapsedMedia?.removeEventListener('change', onCollapsedMediaChange)
  window.removeEventListener('resize', syncCollapsed)
  removeWebRipples?.()
  removeWebRipples = null
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

    <div v-if="!collapsed" class="mode-bar" role="tablist" :aria-label="language === 'en' ? 'Sidebar density' : '侧栏显示模式'">
      <button
        type="button"
        class="mode-btn"
        :class="{ on: sidebarMode === 'focus' }"
        :title="language === 'en' ? 'Fold low-frequency groups; click any group to expand' : '折叠低频分组，界面更清爽，点击任意分组可随时展开'"
        @click="setSidebarMode('focus')"
      >{{ language === 'en' ? 'Focus' : '精简' }}</button>
      <button
        type="button"
        class="mode-btn"
        :class="{ on: sidebarMode === 'all' }"
        :title="language === 'en' ? 'Expand all groups' : '展开全部 16 个模块'"
        @click="setSidebarMode('all')"
      >{{ language === 'en' ? 'All' : '全部' }}</button>
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

      <div v-if="!collapsed && pinnedItems.length > 0" class="group pinned-group">
        <div class="g-label pinned-label">
          <span>{{ language === 'en' ? 'PINNED' : '常用置顶' }}</span>
          <span class="g-count">{{ pinnedItems.length }}</span>
        </div>
        <button
          v-for="item in pinnedItems"
          :key="`pin-${item.i}`"
          class="nav-item"
          :class="{ active: active.current === item.i }"
          type="button"
          :title="item.tip || textOf(item)"
          :aria-current="active.current === item.i ? 'page' : undefined"
          @click="onNavClick(item)"
        >
          <NavIcon :name="item.icon" />
          <span class="nav-text">{{ textOf(item) }}</span>
          <span
            class="pin-btn pinned"
            role="button"
            tabindex="-1"
            :title="language === 'en' ? 'Unpin' : '取消置顶'"
            @click="togglePin(item.i, $event)"
          >★</span>
        </button>
      </div>

      <div v-if="collapsed" class="rail-divider" aria-hidden="true"></div>

      <template v-for="g in displayNav.groups" :key="g.key">
        <div v-if="collapsed && g.dividerBefore" class="rail-divider" aria-hidden="true"></div>
        <div class="group" :class="{ folded: !collapsed && isGroupFolded(g.key) }">
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
            <button
              type="button"
              class="g-label g-toggle"
              :class="{ folded: isGroupFolded(g.key), 'has-active': activeGroupKey === g.key }"
              :aria-expanded="!isGroupFolded(g.key)"
              :title="isGroupFolded(g.key) ? `展开「${textOf(g)}」` : `收起「${textOf(g)}」`"
              @click="toggleGroupFold(g.key)"
            >
              <span class="g-title">{{ textOf(g) }}</span>
              <span v-if="isGroupFolded(g.key)" class="g-count">{{ g.items.length }}</span>
              <span class="g-line" aria-hidden="true"></span>
              <span class="g-chevron" aria-hidden="true">{{ isGroupFolded(g.key) ? '▸' : '▾' }}</span>
            </button>
            <template v-if="!isGroupFolded(g.key)">
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
                <span
                  class="pin-btn"
                  :class="{ pinned: isPinned(item.i) }"
                  role="button"
                  tabindex="-1"
                  :title="isPinned(item.i) ? (language === 'en' ? 'Unpin' : '取消置顶') : (language === 'en' ? 'Pin to top' : '置顶到常用')"
                  @click="togglePin(item.i, $event)"
                >{{ isPinned(item.i) ? '★' : '☆' }}</span>
              </button>
            </template>
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
  opacity: 0.16;
}

svg.ic {
  width: 22px;
  height: 22px;
  padding: 3.5px;
  border-radius: 6px;
  background: rgba(108, 88, 217, 0.08);
  color: #6C58D9;
  flex-shrink: 0;
  opacity: 0.92;
  transition:
    opacity 160ms ease,
    background 200ms cubic-bezier(0.22, 1, 0.36, 1),
    color 180ms ease,
    box-shadow 200ms cubic-bezier(0.22, 1, 0.36, 1),
    transform 200ms cubic-bezier(0.22, 1, 0.36, 1);
}

/* 晴空棱镜紫 · 悬浮圆角侧轨（高通透纯白表面替代重模糊，流畅且保留完整导航） */
.sidebar {
  position: relative;
  z-index: 1;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 0 10px 10px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(206, 196, 238, 0.85);
  box-shadow:
    0 10px 28px rgba(60, 44, 124, 0.10),
    0 2px 6px rgba(60, 44, 124, 0.04),
    inset 0 1px 0 rgba(255, 255, 255, 0.98);
}

.brand {
  height: 52px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 4px;
}

.mode-bar {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px;
  padding: 2px;
  margin: 0 2px 8px;
  border-radius: 9px;
  background: rgba(242, 238, 252, 0.9);
  border: 1px solid rgba(212, 206, 230, 0.75);
}

.mode-btn {
  height: 24px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--ink-3);
  font: inherit;
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  transition: background 140ms ease, color 140ms ease, box-shadow 140ms ease;
}

.mode-btn:hover:not(.on) { color: var(--ink); }

.mode-btn.on {
  background: #FFFFFF;
  color: var(--c1);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(60, 44, 124, 0.10);
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

.brand:hover .logo { transform: translateY(-1.5px) scale(1.04); }
.brand-name { font-size: 15px; font-weight: 700; letter-spacing: 0.2px; color: var(--ink); }

.nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  margin: 0 -4px;
  padding: 0 4px;
}

.nav::-webkit-scrollbar { width: 4px; }
.nav::-webkit-scrollbar-thumb { background: transparent; border-radius: 999px; transition: background 200ms ease; }
.nav:hover::-webkit-scrollbar-thumb { background: rgba(108, 88, 217, 0.28); }
.group { margin-bottom: 3px; }

.g-label {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 5px 8px 3px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--ink-3);
  font: inherit;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  line-height: 16px;
  text-align: left;
}

.g-toggle {
  cursor: pointer;
  transition: background 140ms ease, color 140ms ease;
}

.g-toggle:hover {
  background: rgba(108, 88, 217, 0.06);
  color: var(--ink);
}

.g-toggle.folded {
  padding: 6px 8px;
  margin-bottom: 1px;
}

.g-line { flex: 1; height: 1px; background: var(--edge); opacity: 0.55; }
.g-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 17px;
  height: 16px;
  padding: 0 5px;
  border-radius: 999px;
  background: rgba(108, 88, 217, 0.09);
  color: var(--c1);
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.g-chevron { font-size: 10px; opacity: 0.72; }
.pinned-label { color: var(--c1); }
.pinned-label::after { content: ""; flex: 1; height: 1px; background: rgba(108, 88, 217, 0.22); }
.nav-text { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pin-btn {
  opacity: 0;
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  font-size: 12px;
  color: var(--ink-3);
  transition: opacity 140ms ease, color 140ms ease, background 140ms ease;
}
.nav-item:hover .pin-btn,
.pin-btn.pinned { opacity: 1; }
.pin-btn.pinned { color: #D97706; }
.pin-btn:hover { background: rgba(108, 88, 217, 0.10); color: #D97706; }
.rail-divider { height: 1px; margin: 6px 4px; background: var(--edge); opacity: 0.78; }

.nav-item {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  width: 100%;
  height: 34px;
  gap: 9px;
  padding: 0 10px 0 8px;
  margin-bottom: 2px;
  border: 1px solid transparent;
  border-radius: 10px;
  color: var(--ink-2);
  background: transparent;
  font: inherit;
  font-size: 13px;
  line-height: 18px;
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
  background: rgba(108, 88, 217, 0.08);
  border-color: rgba(124, 106, 230, 0.16);
  color: var(--ink);
  transform: translateX(2px);
}
.nav-item:hover svg.ic {
  opacity: 1;
  background: rgba(108, 88, 217, 0.15);
  transform: scale(1.05);
}
.nav-item:focus-visible { outline: 2px solid var(--c1); outline-offset: 1px; }
.nav-item:active { transform: scale(0.975); }
.nav-item.active {
  background: linear-gradient(135deg, #F1ECFF 0%, #FFFFFF 100%);
  color: #4A35B8;
  border-color: rgba(124, 106, 230, 0.42);
  box-shadow: 0 3px 10px rgba(108, 88, 217, 0.15), inset 0 1px 0 rgba(255, 255, 255, 0.96);
  font-weight: 700;
  transform: none;
}
.nav-item.active svg.ic {
  opacity: 1;
  background: linear-gradient(135deg, #8472F0 0%, #6852D6 100%);
  color: #FFFFFF;
  box-shadow: 0 2px 8px rgba(108, 88, 217, 0.32);
  transform: none;
}
.group-trigger.is-open { background: var(--nav-hover-bg); }

.foot { padding: 6px 2px 2px; border-top: 1px solid var(--edge); }
.foot-actions { display: flex; flex-direction: column; }
.palette-button { color: var(--c1); }
.palette-button .ic { color: var(--c1); }
.foot .meta { display: flex; align-items: center; justify-content: space-between; padding: 4px 8px 2px; color: var(--ink-3); font-size: 10px; font-weight: 600; }
.shortcut-hint { color: var(--c1); font-size: 9.5px; }

@keyframes fadeUp { from { opacity: 0; transform: translateY(2px); } to { opacity: 1; transform: none; } }
.sidebar { animation: fadeUp var(--motion-enter) var(--ease-out); }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}

/* Icon-only responsive mode (Compact 84px / Narrow 72px). */
@media (max-width: 120px) {
  .sidebar { padding: 0 0 12px; align-items: center; }
  .brand { justify-content: center; padding: 0; height: 64px; }
  .brand-name, .g-label, .mode-bar, .pinned-group, .pin-btn { display: none; }
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
