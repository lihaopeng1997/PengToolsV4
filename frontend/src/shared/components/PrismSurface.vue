<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    tone?: 'surface' | 'soft' | 'paper' | 'glass'
    padding?: boolean | number | string
    radius?: number | string
  }>(),
  {
    tone: 'surface',
    padding: false,
    radius: '16px'
  }
)
</script>

<template>
  <div
    class="prism-surface"
    :class="[
      `tone-${props.tone}`,
      { 'has-padding': Boolean(props.padding) }
    ]"
    :style="{
      borderRadius: typeof props.radius === 'number' ? `${props.radius}px` : props.radius,
      padding: typeof props.padding === 'number' ? `${props.padding}px` : (typeof props.padding === 'string' ? props.padding : undefined)
    }"
  >
    <slot />
  </div>
</template>

<style scoped>
.prism-surface {
  position: relative;
  border: 1px solid var(--border);
  transition: background 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
  min-width: 0;
  box-sizing: border-box;
}

.tone-surface {
  background: var(--surface);
}

.tone-soft {
  background: var(--surface-soft);
}

.tone-paper {
  background: var(--elevated-surface);
}

.tone-glass {
  background: var(--glass-bg);
  border-color: var(--glass-border);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
}

.has-padding {
  padding: 20px;
}
</style>
