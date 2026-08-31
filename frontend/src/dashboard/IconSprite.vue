<script setup lang="ts">
// 本地静态 SVG sprite：从 legacy dashboard.html 原样迁移的 symbol 定义（无网络、无远程依赖）。
</script>

<template>
  <svg width="0" height="0" style="position:absolute" aria-hidden="true">
    <defs>
      <symbol id="i-req" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 6h11M9.5 12h11M9.5 18h11"/><path d="m3.5 5.8 1.2 1.2 2.2-2.4M3.5 11.8l1.2 1.2 2.2-2.4M3.5 17.8l1.2 1.2 2.2-2.4"/></symbol>
      <symbol id="i-daily" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5.5" width="16" height="14.5" rx="2.5"/><path d="M4 10.5h16M8.5 3.5v4M15.5 3.5v4"/><path d="M8 14.5h3.5M8 17.5h6"/></symbol>
      <symbol id="i-rocket" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/></symbol>
      <symbol id="i-db" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5.6" rx="7" ry="2.7"/><path d="M5 5.6v12.8c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7V5.6"/><path d="M5 12c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7"/></symbol>
      <symbol id="i-chat" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6a3 3 0 0 0-3-3H7a3 3 0 0 0-3 3v7a3 3 0 0 0 3 3h1v3.6L13 16h4a3 3 0 0 0 3-3z"/><path d="M8.5 8.5h7M8.5 11.5h4.5"/></symbol>
      <symbol id="i-key" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15.5" r="3.8"/><path d="m10.8 12.8 8.7-8.7M15.5 8.2l2.6 2.6M13 10.7l2 2"/></symbol>
      <symbol id="i-braces" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 3.5C7.7 3.5 8.4 8.1 6 8.2c2.4.1 1.7 4.6 3.5 4.6-1.8 0-1.1 4.5-3.5 4.6 2.4.1 1.7 4.6 3.5 4.6" transform="translate(1 -1.2)"/><path d="M14 3.5c1.8 0 1.1 4.6 3.5 4.7-2.4.1-1.7 4.6-3.5 4.6 1.8 0 1.1 4.5 3.5 4.6-2.4.1-1.7 4.6-3.5 4.6" transform="translate(1 -1.2)"/></symbol>
      <symbol id="i-plug" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3.5V8M16 3.5V8"/><path d="M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0z"/><path d="M12 17v3.5"/></symbol>
      <symbol id="i-plus" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5.5v13M5.5 12h13"/></symbol>
      <symbol id="i-more" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></symbol>
    </defs>
  </svg>
</template>
