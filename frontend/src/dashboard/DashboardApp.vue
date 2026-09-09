<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import type { BridgeApi } from '../shared/bridge'
import type { DashboardSummary } from './types'
import IconSprite from './IconSprite.vue'
import PrismIcon from '../shared/components/PrismIcon.vue'
import HeroSection from './components/HeroSection.vue'
import StatsGrid from './components/StatsGrid.vue'
import MonthlyTasks from './components/MonthlyTasks.vue'
import ReleaseOverview from './components/ReleaseOverview.vue'
import QuickTools from './components/QuickTools.vue'
import { useDailyQuote } from './composables/useDailyQuote'

const props = defineProps<{
  state: {
    summary: DashboardSummary | null
    bridge: BridgeApi | null
    error?: string | null
    retry?: () => Promise<void>
  }
}>()

const summary = computed(() => props.state.summary)
const error = computed(() => props.state.error)

const isDemoMode = computed(() => Boolean(summary.value?.is_demo || summary.value?.stats?.is_demo))

const { dateText, quote, nextQuote } = useDailyQuote()

const isHidden = ref(false)

function onVisibilityChange() {
  if (typeof document !== 'undefined') {
    isHidden.value = document.hidden
    if (document.hidden) {
      document.body.classList.add('page-hidden')
    } else {
      document.body.classList.remove('page-hidden')
    }
  }
}

onMounted(() => {
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
    onVisibilityChange()
  }
})

onUnmounted(() => {
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', onVisibilityChange)
    document.body.classList.remove('page-hidden')
  }
})

function onNavClick(navIndex: number): void {
  props.state.bridge?.navigate(navIndex)
}

function onCreateRequirement(): void {
  if (props.state.bridge?.createRequirement) {
    props.state.bridge.createRequirement()
  } else {
    onNavClick(10)
  }
}

function onOpenRequirement(reqId?: string | null, fallbackNav = 10): void {
  if (!reqId || String(reqId).startsWith('demo-')) {
    return
  }
  if (props.state.bridge?.openRequirement) {
    props.state.bridge.openRequirement(reqId)
  } else {
    onNavClick(fallbackNav)
  }
}

function onWriteDaily(): void {
  onNavClick(9)
}

function onRetry(): void {
  if (props.state.retry) {
    props.state.retry()
  } else {
    onNavClick(0)
  }
}
</script>

<template>
  <IconSprite />

  <div v-if="summary" class="dashboard-root" :class="{ 'page-hidden': isHidden }">
    <!-- 晴空棱镜 2 栏式网格架构 (V2.0 规范第 7.1 节) -->
    <div class="home-grid">
      <!-- 左栏 home-left: Hero (176px) + 4 Stat Cards (108px) + Recent Requirements 任务台账 -->
      <div class="home-left">
        <HeroSection
          :greeting="summary.greeting"
          :username="summary.username"
          :date-text="dateText"
          :quote="quote"
          :is-demo="isDemoMode"
          @change-quote="nextQuote"
          @write-daily="onWriteDaily"
          @create-requirement="onCreateRequirement"
        />

        <StatsGrid
          :stats="summary.stats"
          :is-demo="isDemoMode"
          @navigate="onNavClick"
        />

        <MonthlyTasks
          :tasks="summary.monthly_release_tasks"
          :recent="summary.recent"
          :is-demo="isDemoMode"
          @open-requirement="onOpenRequirement"
          @navigate="onNavClick"
        />
      </div>

      <!-- 右栏 home-right: 上线总览 (300px) + 常用工具 (64px, 2列) + 装饰提示卡片 -->
      <aside class="home-right stack">
        <ReleaseOverview
          :release="summary.release"
          :stats="summary.stats"
          :tasks="summary.monthly_release_tasks"
          :is-demo="isDemoMode"
          @navigate="onNavClick"
        />

        <QuickTools
          :tools="summary.tools"
          @navigate="onNavClick"
        />

        <!-- 装饰信息卡片 -->
        <section class="card pad tip-card">
          <div class="tip-row">
            <PrismIcon name="spark" :size="16" class="tip-spark" />
            <h3 class="tip-title">留一点空间，给新思路。</h3>
          </div>
          <p class="tip-desc">
            工具随手可得，工作有迹可循。<br>
            按 <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd>，快速去往任意模块。
          </p>
        </section>
      </aside>
    </div>
  </div>

  <div v-else-if="error" class="error-view">
    <PrismIcon name="file" :size="32" />
    <p>{{ error }}</p>
    <button class="btn btn-primary" type="button" @click="onRetry">重试加载</button>
  </div>
</template>

<style>
html, body, #app {
  margin: 0;
  min-width: 0;
  width: 100%;
}
body {
  font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text);
  background: var(--app-bg);
}
button, input, select, textarea { font: inherit; }
*, *::before, *::after { box-sizing: border-box; }
</style>

<style scoped>
:root {
  --r-lg: 16px;
  --r-sm: 10px;
}

.dashboard-root {
  padding: 0 0 8px;
  min-width: 0;
  box-sizing: border-box;
  animation: enter 0.22s ease both;
}

@keyframes enter {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 权威 2 栏网格：C>=1200: R330; 1020<=C<1200: R300; C<1020: 单栏 (规范 7.1) */
.home-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 16px;
  min-width: 0;
}

@media (min-width: 1200px) {
  .home-grid {
    grid-template-columns: minmax(0, 1fr) 330px;
  }
}

@media (min-width: 1020px) and (max-width: 1199px) {
  .home-grid {
    grid-template-columns: minmax(0, 1fr) 300px;
  }
}

@media (max-width: 1019px) {
  .home-grid {
    grid-template-columns: 1fr;
  }

  .home-right {
    display: grid !important;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }

  .tip-card {
    grid-column: 1 / -1;
  }
}

@media (max-width: 700px) {
  .home-right {
    grid-template-columns: 1fr;
  }
}

.home-left {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.home-right {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.stack {
  display: flex;
  flex-direction: column;
}

/* 装饰提示卡片 */
.tip-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 18px 20px;
  box-shadow: 0 2px 8px var(--shadow-l1);
  box-sizing: border-box;
}

.tip-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.tip-spark {
  color: var(--primary);
}

.tip-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}

.tip-desc {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.8;
  margin: 0;
}

kbd {
  font: 10px Consolas, monospace;
  padding: 1px 4px;
  border-radius: 3px;
  border: 1px solid var(--border);
  background: var(--surface-soft);
  color: var(--text);
}

.error-view {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 60px 20px;
  color: var(--text-muted);
}

/* 主题与语义 Token 契约锚点（纯 CSS 变量消费，零硬编码 Hex） */
.theme-contract-probe {
  background: linear-gradient(
    135deg,
    var(--aurora-start),
    var(--aurora-mid),
    var(--aurora-end)
  );
  color: var(--primary-grad-start);
  border-color: var(--primary-grad-end);
  box-shadow: 0 2px 8px var(--shadow-l1), 0 4px 14px var(--shadow-l2), 0 6px 16px var(--shadow-l2);
}

.theme-palette-anchor {
  background-color: var(--app-bg);
  color: var(--surface);
  border-color: var(--surface-soft);
  outline-color: var(--elevated-surface);
}

.theme-status-anchor {
  color: var(--warning);
  background-color: var(--success);
}

.theme-accent-anchor {
  color: var(--accent-indigo);
  background-color: var(--accent-cyan);
  border-color: var(--accent-rose);
  outline-color: var(--accent-emerald);
}

/* 全局动效契约 (规范 7.2) */
@media (prefers-reduced-motion: reduce) {
  .dashboard-root,
  .dashboard-root * {
    animation: none !important;
    transition: none !important;
  }
  .enter {
    opacity:1 !important;
    transform:none !important;
  }
}

:global(body.motion-disabled .dashboard-root),
:global(body.motion-disabled .dashboard-root *),
:global([data-motion="disabled"] .dashboard-root),
:global([data-motion="disabled"] .dashboard-root *){
  animation: none !important;
  transition: none !important;
}

:global(body.page-hidden .dashboard-root),
:global(body.page-hidden .dashboard-root *){
  animation-play-state: paused !important;
}
</style>
