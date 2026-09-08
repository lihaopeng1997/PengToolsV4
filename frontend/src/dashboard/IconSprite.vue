<script setup lang="ts">
// 晴空棱镜静态 SVG sprite：统一 stroke-width="1.6"，纯本地展示无网络依赖
</script>

<template>
  <svg width="0" height="0" style="position:absolute" aria-hidden="true">
    <defs>
      <!-- 基础与业务图标 -->
      <symbol id="i-home" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m3 10 9-7 9 7v10H3Z M9 20v-7h6v7"/></symbol>
      <symbol id="i-file" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6Z M14 3v5h4 M9 12h6 M9 16h4"/></symbol>
      <symbol id="i-req" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 6h11M9.5 12h11M9.5 18h11"/><path d="m3.5 5.8 1.2 1.2 2.2-2.4M3.5 11.8l1.2 1.2 2.2-2.4M3.5 17.8l1.2 1.2 2.2-2.4"/></symbol>
      <symbol id="i-daily" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5.5" width="16" height="14.5" rx="2.5"/><path d="M4 10.5h16M8.5 3.5v4M15.5 3.5v4"/><path d="M8 14.5h3.5M8 17.5h6"/></symbol>
      <symbol id="i-calendar" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v16H4Z M8 2v6 M16 2v6 M4 10h16 M8 14h2 M14 14h2 M8 18h2"/></symbol>
      <symbol id="i-rocket" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M13 5c4-3 8-2 8-2s1 4-2 8l-6 6-6-6Z M7 11H3l4-5 5-1 M13 17v4l5-4 1-5 M8 16l-4 4"/></symbol>
      <symbol id="i-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4 4L19 6"/></symbol>
      <symbol id="i-plus" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14 M5 12h14"/></symbol>
      <symbol id="i-spark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3Z"/></symbol>
      <symbol id="i-refresh" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 8a8 8 0 1 0 0 8 M20 3v5h-5"/></symbol>
      <symbol id="i-clock" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2"/></symbol>
      <symbol id="i-activity" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12h5l3-8 4 16 3-8h5"/></symbol>
      <symbol id="i-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14 m-5-5 5 5-5 5"/></symbol>

      <!-- 常用工具与服务图标 -->
      <symbol id="i-db" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6c0-4 16-4 16 0s-16 4-16 0Zm0 0v12c0 4 16 4 16 0V6 M4 12c0 4 16 4 16 0"/></symbol>
      <symbol id="i-chat" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4h18v13H9l-5 4v-4H3Z M7 8h10 M7 12h6"/></symbol>
      <symbol id="i-api" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12h5l3-8 4 16 3-8h5"/></symbol>
      <symbol id="i-plug" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3.5V8M16 3.5V8"/><path d="M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0z"/><path d="M12 17v3.5"/></symbol>
      <symbol id="i-logs" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4h18v16H3Z m4 4 4 4-4 4 M13 16h4"/></symbol>
      <symbol id="i-terminal" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4h18v16H3Z m4 4 4 4-4 4 M13 16h4"/></symbol>
      <symbol id="i-crypto" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z m-3 9 2 2 4-4"/></symbol>
      <symbol id="i-key" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15.5" r="3.8"/><path d="m10.8 12.8 8.7-8.7M15.5 8.2l2.6 2.6M13 10.7l2 2"/></symbol>
      <symbol id="i-format" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m8 7-5 5 5 5 m8-10 5 5-5 5 M14 4l-4 16"/></symbol>
      <symbol id="i-braces" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 3.5C7.7 3.5 8.4 8.1 6 8.2c2.4.1 1.7 4.6 3.5 4.6-1.8 0-1.1 4.5-3.5 4.6 2.4.1 1.7 4.6 3.5 4.6" transform="translate(1 -1.2)"/><path d="M14 3.5c1.8 0 1.1 4.6 3.5 4.7-2.4.1-1.7 4.6-3.5 4.6 1.8 0 1.1 4.5 3.5 4.6-2.4.1-1.7 4.6-3.5 4.6" transform="translate(1 -1.2)"/></symbol>
      <symbol id="i-layers" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3 10 5-10 5L2 8Z M2 12l10 5 10-5 M2 16l10 5 10-5"/></symbol>
      <symbol id="i-quote" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 13h4.5a2.5 2.5 0 0 0 2.5-2.5V8a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v6.5C4 18 6 20 8.5 20"/><path d="M13 13h4.5a2.5 2.5 0 0 0 2.5-2.5V8a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v6.5c0 3.5 2 5.5 4.5 5.5"/></symbol>
    </defs>
  </svg>
</template>
