<script setup lang="ts">
import { computed } from 'vue'
import { useCountUp } from '../composables/useCountUp'

const props = defineProps<{ value?: number | null }>()
const display = useCountUp(computed(() => Number(props.value ?? 0)))
</script>

<template>
  <span class="count-up">{{ display }}</span>
</template>

<style scoped>
.count-up {
  font-variant-numeric: tabular-nums;
}
</style>
