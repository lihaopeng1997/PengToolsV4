---
name: pengtools-vue-ui
description: Change PengToolsHub Vue 3, TypeScript, Vite, QWebChannel, Chrome, sidebar, dashboard, theme, or responsive UI while preserving the native/PyQt boundary. Do not use for native-widget-only work.
---

# PengToolsHub hybrid web UI

Use Vue for lightweight global presentation: Chrome, sidebar, dashboard, notifications, and overview. Keep dense workbenches and system interactions in PyQt: databases, SQL, SSH/terminal, capture, large trees/tables, and file operations.

## Boundaries

- A Bridge is a `ui/` adapter, not a business service. It must not import `panels`; callers inject data and callbacks.
- Frontend code does not read local files or databases, call arbitrary Python business logic, load remote assets, or expose a public service.
- Use `ThemeManager` semantic tokens, `layout_metrics.py` modes, and `navigation_model.py` navigation. Do not introduce competing product tokens, breakpoints, or navigation data.

## Delivery

- Change `frontend/` source. When embedded production assets change, generate `resources/webui/vue/` from the source in the same commit; never edit bundled output by hand.
- Verify types and the relevant build when source or generated assets change. Keep visual/layout acceptance separate and report it as pending when it was not manually checked.
