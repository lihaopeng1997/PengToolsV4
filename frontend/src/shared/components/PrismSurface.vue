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
  border: 1px solid var(--border, rgba(119, 123, 163, 0.13));
  transition: background 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
  min-width: 0;
  box-sizing: border-box;
}

.tone-surface {
  background: var(--surface, #fdfdff);
}

.tone-soft {
  background: var(--surface-soft, #f0ecfa);
}

.tone-paper {
  background: var(--paper, rgba(255, 255, 255, 0.85));
}

.tone-glass {
  background: var(--paper, rgba(255, 255, 255, 0.72));
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
}

.has-padding {
  padding: 20px;
}
</style>
