<script setup lang="ts">
import { iconId } from './icons'

defineProps<{ name: string }>()
</script>

<template>
  <svg class="ic"><use :href="'#' + iconId(name)" /></svg>
</template>
