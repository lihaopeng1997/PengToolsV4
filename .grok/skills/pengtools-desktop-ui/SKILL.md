---
name: pengtools-desktop-ui
description: >
  Translate design skills into PengToolsHub PyQt6/QSS. Use when redesigning,
  theming, or polishing PengTools UI, or when the user mentions 主题, QSS,
  ThemeManager, 墨黑, calm/clear/warm/black. Slash: /pengtools-desktop-ui
---

# PengTools Desktop UI Adapter

PengToolsHub is an offline Windows desktop workbench (Python 3.12 + PyQt6).
Web skills (ui-ux-pro-max, impeccable, taste, react-bits) supply **judgment only**.

## Must

1. Read `ui/theme_manager.py`, `resources/style.qss`, `ui/design_system.py` first.
2. Output QSS tokens / PyQt properties. Never JSX, Tailwind, Google Fonts, or CDN.
3. Theme IDs: `calm` | `clear` | `warm` | `black`. Alias `night` → `black`.
4. Runtime fonts: `Microsoft YaHei UI`, `Segoe UI`; code/terminal stay monospace.
5. One Primary per work surface. Motion 150–250ms hover/focus only.
6. Do not change nav index ↔ Stack mapping, `data/` root, or sensitive persistence.
7. Dark theme is **ink black**, not mint-on-slate. No white cards on `black`.
8. Combo sizing (must):
   - Closed enums (`GET` / 全随机 / 接口库|历史) → `size_enum_combo` once, longest label + arrow.
   - Dynamic lists (server / log file / user category / env name) → `size_pick_combo` fixed 200px. Never `fit_combo` on reload.
   - Action buttons sit after the field they belong to; leftover space is a trailing stretch only.

## Forbidden

Neon, glassmorphism, particles, Aurora/Spotlight backgrounds, emoji icons,
React / QWebEngine as the main UI, downloading fonts at runtime.

## Frozen tokens

Authoritative values live in `docs/ui/PengToolsV4_新视觉语言_V1.0.md` and
`THEMES` in `ui/theme_manager.py`.
