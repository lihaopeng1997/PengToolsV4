<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    busy?: boolean
    progress?: number | null
    label?: string | null
    variant?: 'ring' | 'dots' | 'bar' | 'overlay'
    reducedMotion?: boolean
  }>(),
  {
    busy: true,
    progress: null,
    label: '',
    variant: 'ring',
    reducedMotion: false
  }
)

const hasProgress = computed(() => props.progress != null && props.progress >= 0)
const progressClamped = computed(() => {
  if (props.progress == null) return 0
  return Math.max(0, Math.min(100, props.progress))
})
const displayLabel = computed(() => props.label || (props.variant === 'dots' ? '正在等待回复…' : '正在处理…'))
</script>

<template>
  <div
    v-if="props.busy"
    class="prism-loading"
    :class="[
      `variant-${props.variant}`,
      { 'has-progress': hasProgress, 'motion-reduced': props.reducedMotion }
    ]"
    role="status"
    aria-live="polite"
  >
    <!-- 1. Ring 变体 (LD-02 24x24 棱镜环) -->
    <template v-if="props.variant === 'ring' || props.variant === 'overlay'">
      <div class="ring-wrapper" aria-hidden="true">
        <svg class="spinner-svg" viewBox="0 0 24 24" width="24" height="24">
          <!-- 背景浅色轨道 -->
          <circle
            class="spinner-track"
            cx="12"
            cy="12"
            r="10"
            fill="none"
            stroke-width="2"
          />
          <!-- 前景 90 度旋转弧 (900ms 循环) -->
          <circle
            class="spinner-arc"
            cx="12"
            cy="12"
            r="10"
            fill="none"
            stroke-width="2"
            stroke-dasharray="15.7 47.1"
            stroke-linecap="round"
          />
        </svg>
        <!-- 中心单菱形静置 -->
        <span class="ring-gem" />
      </div>

      <div class="text-content">
        <span class="loading-label">{{ displayLabel }}</span>
        <div v-if="hasProgress" class="progress-track-wrapper">
          <div class="progress-track">
            <div class="progress-fill" :style="{ width: `${progressClamped}%` }" />
          </div>
          <span class="progress-pct">{{ progressClamped }}%</span>
        </div>
      </div>
    </template>

    <!-- 2. Dots 变体 (LD-05 3 菱形点) -->
    <template v-else-if="props.variant === 'dots'">
      <div class="dots-wrapper" aria-hidden="true">
        <span class="diamond-dot dot-1" />
        <span class="diamond-dot dot-2" />
        <span class="diamond-dot dot-3" />
      </div>
      <span class="loading-label dots-label">{{ displayLabel }}</span>
    </template>

    <!-- 3. Bar 变体 (LD-01 往复细光带) -->
    <template v-else-if="props.variant === 'bar'">
      <div class="bar-wrapper">
        <div class="bar-track">
          <div
            v-if="hasProgress"
            class="bar-fill"
            :style="{ width: `${progressClamped}%` }"
          />
          <div v-else class="bar-shimmer" />
        </div>
        <span v-if="displayLabel" class="bar-label">{{ displayLabel }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.prism-loading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  font-family: inherit;
  color: var(--text-strong, #262438);
}

/* Ring 变体 */
.ring-wrapper {
  position: relative;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.spinner-svg {
  width: 24px;
  height: 24px;
  animation: prismSpin 0.9s linear infinite;
  transform-origin: center center;
}

.spinner-track {
  stroke: var(--surface-soft, rgba(108, 88, 217, 0.12));
}

.spinner-arc {
  stroke: var(--primary, #6c58d9);
}

.ring-gem {
  position: absolute;
  width: 5px;
  height: 5px;
  background: var(--primary, #6c58d9);
  transform: rotate(45deg);
  border-radius: 1px;
}

.text-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.loading-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-strong, #262438);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.progress-track-wrapper {
  display: flex;
  align-items: center;
  gap: 6px;
}

.progress-track {
  width: 120px;
  height: 4px;
  background: var(--surface-soft, rgba(108, 88, 217, 0.12));
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--primary-grad-start, #7c6ae6), var(--primary-grad-end, #6c58d9));
  border-radius: 2px;
  transition: width 0.15s ease;
}

.progress-pct {
  font-size: 11px;
  font-weight: 600;
  color: var(--primary, #6c58d9);
}

/* Dots 变体 (LD-05 6x6 菱形，间距 5px，1.2s 纯透明度脉动) */
.dots-wrapper {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
}

.diamond-dot {
  width: 6px;
  height: 6px;
  background: var(--primary, #6c58d9);
  transform: rotate(45deg);
  border-radius: 1px;
  opacity: 0.35;
  animation: diamondPulse 1.2s ease-in-out infinite;
  will-change: opacity;
}

.dot-1 {
  animation-delay: 0s;
}

.dot-2 {
  animation-delay: 0.15s;
}

.dot-3 {
  animation-delay: 0.3s;
}

.dots-label {
  font-size: 12px;
  color: var(--text-muted, #615d73);
  font-weight: normal;
}

/* Bar 变体 (LD-01 细轨道往返) */
.bar-wrapper {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

.bar-track {
  position: relative;
  width: 100%;
  height: 4px;
  background: var(--surface-soft, rgba(108, 88, 217, 0.12));
  border-radius: 2px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: var(--primary, #6c58d9);
  border-radius: 2px;
  transition: width 0.15s ease;
}

.bar-shimmer {
  position: absolute;
  top: 0;
  height: 100%;
  width: 96px;
  background: var(--primary, #6c58d9);
  border-radius: 2px;
  animation: barShimmer 1.6s ease-in-out infinite alternate;
}

.bar-label {
  font-size: 11px;
  color: var(--text-muted, #615d73);
}

/* Variant classes */
.variant-ring,
.variant-dots,
.variant-bar {
  display: inline-flex;
}

.variant-overlay {
  display: inline-flex;
  padding: 12px 18px;
  background: var(--surface, #ffffff);
  border: 1px solid var(--border, rgba(119, 123, 163, 0.15));
  border-radius: 14px;
  box-shadow: 0 4px 16px var(--shadow-l2, rgba(38, 36, 56, 0.08));
}

/* Keyframes */
@keyframes prismSpin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@keyframes diamondPulse {
  0%, 100% {
    opacity: 0.35;
  }
  50% {
    opacity: 1;
  }
}

@keyframes barShimmer {
  from {
    left: 0%;
  }
  to {
    left: calc(100% - 96px);
  }
}

/* Reduced Motion */
@media (prefers-reduced-motion: reduce) {
  .spinner-svg,
  .diamond-dot,
  .bar-shimmer {
    animation: none !important;
  }
  .diamond-dot {
    opacity: 0.7 !important;
  }
}

.motion-reduced .spinner-svg,
.motion-reduced .diamond-dot,
.motion-reduced .bar-shimmer {
  animation: none !important;
}

.motion-reduced .diamond-dot {
  opacity: 0.7 !important;
}

:global(body.motion-disabled) .spinner-svg,
:global(body.motion-disabled) .diamond-dot,
:global(body.motion-disabled) .bar-shimmer,
:global([data-motion="disabled"]) .spinner-svg,
:global([data-motion="disabled"]) .diamond-dot,
:global([data-motion="disabled"]) .bar-shimmer {
  animation: none !important;
}

:global(body.page-hidden) .spinner-svg,
:global(body.page-hidden) .diamond-dot,
:global(body.page-hidden) .bar-shimmer {
  animation-play-state: paused !important;
}
</style>
