<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    name: string
    size?: number | string
    decorative?: boolean
    label?: string
  }>(),
  {
    size: 18,
    decorative: true
  }
)

const sizeStyle = computed(() => {
  return typeof props.size === 'number' ? `${props.size}px` : props.size
})

const iconHref = computed(() => `#i-${props.name}`)
</script>

<template>
  <svg
    class="prism-icon"
    :style="{ width: sizeStyle, height: sizeStyle }"
    :aria-hidden="props.decorative ? 'true' : undefined"
    :aria-label="!props.decorative ? props.label : undefined"
    role="img"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    <use :href="iconHref" />
  </svg>
</template>

<style scoped>
.prism-icon {
  display: inline-block;
  vertical-align: middle;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}
</style>
