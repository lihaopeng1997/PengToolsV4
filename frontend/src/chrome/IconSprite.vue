<script setup lang="ts">
// 本地静态 SVG sprite：从 legacy chrome.html 原样迁移的 symbol 定义（无网络、无 npm 图标库）。
</script>

<template>
  <svg width="0" height="0" style="position:absolute" aria-hidden="true">
    <defs>
      <symbol id="i-logo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.8 20 7.4v9.2L12 21.2 4 16.6V7.4z"/><circle cx="12" cy="12" r="2.5"/><path d="M12 9.5V5.5M9.7 13.3l-3.5 2.1M14.3 13.3l3.5 2.1"/></symbol>
      <symbol id="i-home" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 11.5 12 4l8.5 7.5"/><path d="M5.8 10.2V20h12.4v-9.8"/><path d="M10 20v-5h4v5"/></symbol>
      <symbol id="i-db" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5.6" rx="7" ry="2.7"/><path d="M5 5.6v12.8c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7V5.6"/><path d="M5 12c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7"/></symbol>
      <symbol id="i-chat" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6a3 3 0 0 0-3-3H7a3 3 0 0 0-3 3v7a3 3 0 0 0 3 3h1v3.6L13 16h4a3 3 0 0 0 3-3z"/><path d="M8.5 8.5h7M8.5 11.5h4.5"/></symbol>
      <symbol id="i-req" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 6h11M9.5 12h11M9.5 18h11"/><path d="m3.5 5.8 1.2 1.2 2.2-2.4M3.5 11.8l1.2 1.2 2.2-2.4M3.5 17.8l1.2 1.2 2.2-2.4"/></symbol>
      <symbol id="i-rocket" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/></symbol>
      <symbol id="i-docsync" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 3.5H13L18 8.5V19a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 19V5a1.5 1.5 0 0 1 1.5-1.5z"/><path d="M13 3.5V8.5H18"/><path d="M9 14.5h6m0 0-1.8-1.8M15 14.5l-1.8 1.8"/></symbol>
      <symbol id="i-daily" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5.5" width="16" height="14.5" rx="2.5"/><path d="M4 10.5h16M8.5 3.5v4M15.5 3.5v4"/><path d="M8 14.5h3.5M8 17.5h6"/></symbol>
      <symbol id="i-search" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="6.5"/><path d="m15.8 15.8 4.2 4.2"/></symbol>
      <symbol id="i-term" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="m7.5 9.5 3 2.5-3 2.5M13 15h4"/></symbol>
      <symbol id="i-key" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15.5" r="3.8"/><path d="m10.8 12.8 8.7-8.7M15.5 8.2l2.6 2.6M13 10.7l2 2"/></symbol>
      <symbol id="i-plug" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3.5V8M16 3.5V8"/><path d="M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0z"/><path d="M12 17v3.5"/></symbol>
      <symbol id="i-braces" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 3.5C7.7 3.5 8.4 8.1 6 8.2c2.4.1 1.7 4.6 3.5 4.6-1.8 0-1.1 4.5-3.5 4.6 2.4.1 1.7 4.6 3.5 4.6" transform="translate(1 -1.2)"/><path d="M14 3.5c1.8 0 1.1 4.6 3.5 4.7-2.4.1-1.7 4.6-3.5 4.6 1.8 0 1.1 4.5 3.5 4.6-2.4.1-1.7 4.6-3.5 4.6" transform="translate(1 -1.2)"/></symbol>
      <symbol id="i-id" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5.5" width="18" height="13" rx="2.5"/><circle cx="8.6" cy="10.8" r="1.9"/><path d="M5.8 15.8c.7-1.4 1.9-2.2 2.8-2.2s2.1.8 2.8 2.2M14 9.5h4.5M14 13h3"/></symbol>
      <symbol id="i-car" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13.5 6.6 9a2 2 0 0 1 1.9-1.4h7a2 2 0 0 1 1.9 1.4l1.6 4.5"/><path d="M4 13.5h16v3.2a1 1 0 0 1-1 1h-1.4a1 1 0 0 1-1-1v-.4H7.4v.4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/></symbol>
      <symbol id="i-book" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2.5H20v19H6.5A2.5 2.5 0 0 1 4 19v-14A2.5 2.5 0 0 1 6.5 2.5z"/></symbol>
      <symbol id="i-gear" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7.5h9.5M17.5 7.5H20"/><circle cx="15" cy="7.5" r="2.3"/><path d="M4 16.5h2.5M10.5 16.5H20"/><circle cx="8" cy="16.5" r="2.3"/></symbol>
      <symbol id="i-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 5.5 6.5 6.5L9 18.5"/></symbol>
      <symbol id="i-spark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.5l-1.9-5.7L4.5 10.9 10.1 9z"/></symbol>
    </defs>
  </svg>
</template>
