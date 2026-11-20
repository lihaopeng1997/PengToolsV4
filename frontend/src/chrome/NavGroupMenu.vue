<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { DisplayNavGroup, NavChild } from './nav'
import NavIcon from './NavIcon.vue'

const props = defineProps<{
  group: DisplayNavGroup | null
  open: boolean
  anchor: HTMLElement | null
  current: number
  language?: 'zh' | 'en'
}>()

const emit = defineEmits<{
  close: []
  select: [item: NavChild]
}>()

const menuRef = ref<HTMLElement | null>(null)
const menuStyle = ref<Record<string, string>>({ left: '10px', top: '10px' })
const menuId = ref('prism-group-menu')

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-').replace(/^-|-$/g, '') || 'group'
}

function updatePosition(): void {
  const menu = menuRef.value
  const anchor = props.anchor
  if (!menu || !anchor) return

  const rect = anchor.getBoundingClientRect()
  const width = menu.offsetWidth || 248
  const height = menu.offsetHeight || 180
  const margin = 10
  let left = rect.right + margin
  let top = rect.top

  if (left + width > window.innerWidth - margin) {
    left = rect.left - width - margin
  }
  if (top + height > window.innerHeight - margin) {
    top = window.innerHeight - height - margin
  }

  const maxLeft = Math.max(margin, window.innerWidth - width - margin)
  const maxTop = Math.max(margin, window.innerHeight - height - margin)
  menuStyle.value = {
    left: `${Math.min(Math.max(margin, left), maxLeft)}px`,
    top: `${Math.min(Math.max(margin, top), maxTop)}px`,
  }
}

function focusFirst(): void {
  menuRef.value?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
}

function onDocumentPointerDown(event: PointerEvent): void {
  const target = event.target as Node | null
  if (target && (menuRef.value?.contains(target) || props.anchor?.contains(target))) return
  emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  const menu = menuRef.value
  if (!menu) return
  const items = [...menu.querySelectorAll<HTMLElement>('[role="menuitem"]')]
  const currentIndex = items.indexOf(document.activeElement as HTMLElement)

  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const direction = event.key === 'ArrowDown' ? 1 : -1
    const next = (currentIndex + direction + items.length) % items.length
    items[next]?.focus()
    return
  }
  if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    items[event.key === 'Home' ? 0 : items.length - 1]?.focus()
    return
  }
  if (event.key === 'Tab') {
    // Keep the menu in the page's natural focus order. Closing before Tab
    // prevents focus from landing on a detached/hidden teleported element.
    emit('close')
  }
}

function onSelect(item: NavChild): void {
  emit('select', item)
}

function refreshMenu(): void {
  if (!props.open) return
  menuId.value = `prism-group-menu-${slug(props.group?.key ?? 'group')}`
  void nextTick(() => {
    updatePosition()
    focusFirst()
  })
}

watch(() => props.open, (open) => {
  if (open) {
    document.addEventListener('pointerdown', onDocumentPointerDown, true)
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    refreshMenu()
  } else {
    document.removeEventListener('pointerdown', onDocumentPointerDown, true)
    window.removeEventListener('resize', updatePosition)
    window.removeEventListener('scroll', updatePosition, true)
  }
})
watch(() => props.group?.key, refreshMenu)

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown, true)
  window.removeEventListener('resize', updatePosition)
  window.removeEventListener('scroll', updatePosition, true)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open && group"
      ref="menuRef"
      class="group-menu sub"
      :id="menuId"
      role="menu"
      :aria-label="`${group.zh} · ${group.en}`"
      :style="menuStyle"
      tabindex="-1"
      @keydown="onKeydown"
    >
      <div class="group-menu-title" aria-hidden="true">
        <span>{{ group.zh }}</span>
        <small>{{ group.en }}</small>
      </div>
      <button
        v-for="item in group.items"
        :key="item.i"
        class="group-menu-item nav-item"
        :class="{ active: current === item.i }"
        type="button"
        role="menuitem"
        :aria-current="current === item.i ? 'page' : undefined"
        :title="item.tip || (language === 'en' ? (item.en || item.zh) : item.zh)"
        @click="onSelect(item)"
      >
        <NavIcon :name="item.icon" />
        <span>{{ language === 'en' ? (item.en || item.zh) : item.zh }}</span>
        <small v-if="item.dia">{{ item.dia }}</small>
        <span v-if="current === item.i" class="group-menu-check" aria-hidden="true">✓</span>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.group-menu {
  position: fixed;
  z-index: 40;
  width: 248px;
  max-width: calc(100vw - 20px);
  max-height: min(75vh, calc(100vh - 20px));
  overflow: auto;
  padding: 10px;
  color: var(--sidebar-text, var(--text-nav));
  background: var(--elevated-surface, var(--surface));
  border: 1px solid var(--elevated-border, var(--sidebar-border));
  border-radius: 15px;
  box-shadow: 0 16px 55px var(--shadow-l2, rgba(107, 86, 131, 0.2));
  backdrop-filter: blur(24px);
  animation: group-menu-enter var(--motion-enter, 180ms) var(--ease-out, ease-out) both;
  outline: none;
}

.group-menu-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 8px 10px 11px;
  color: var(--sidebar-text-muted, var(--text-muted));
  font-size: 11px;
  font-weight: 600;
}

.group-menu-title small {
  font-size: 9px;
  font-weight: 500;
  letter-spacing: 0.08em;
  opacity: 0.72;
  text-transform: uppercase;
}

.group-menu-item {
  display: flex;
  align-items: center;
  width: 100%;
  height: auto;
  min-height: 38px;
  gap: 10px;
  padding: 7px 10px;
  margin-bottom: 2px;
  color: var(--text-nav, var(--sidebar-text));
  background: transparent;
  border: 1px solid transparent;
  border-radius: 10px;
  font: inherit;
  font-size: 12.5px;
  line-height: 20px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  outline: none;
  transition: background 180ms ease, border-color 180ms ease, color 160ms ease, transform 160ms cubic-bezier(0.22, 1, 0.36, 1);
}

.group-menu-item:hover,
.group-menu-item:focus-visible {
  color: #4A35B8;
  background: rgba(108, 88, 217, 0.09);
  border-color: rgba(124, 106, 230, 0.25);
  transform: translateX(2px);
}

.group-menu-item.active {
  color: #4A35B8;
  font-weight: 700;
  background: linear-gradient(135deg, #F1ECFF 0%, #FFFFFF 100%);
  border-color: rgba(124, 106, 230, 0.45);
  box-shadow: 0 3px 10px rgba(108, 88, 217, 0.14);
}

.group-menu-item .ic {
  width: 24px;
  height: 24px;
  padding: 4px;
  border-radius: 7px;
  background: rgba(108, 88, 217, 0.08);
  flex: 0 0 24px;
  color: #6C58D9;
  opacity: 0.94;
}

.group-menu-item.active .ic {
  background: linear-gradient(135deg, #8472F0 0%, #6852D6 100%);
  color: #FFFFFF;
}

.group-menu-item > span:nth-child(2) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.group-menu-item > small {
  margin-left: auto;
  color: var(--sidebar-text-muted, var(--text-muted));
  font-size: 9px;
}

.group-menu-check {
  margin-left: 0 !important;
  color: var(--primary);
  font-size: 12px;
  font-weight: 700;
}

@keyframes group-menu-enter {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
  .group-menu { animation: none; }
}

body.motion-disabled .group-menu,
[data-motion="disabled"] .group-menu {
  animation: none;
}
</style>
