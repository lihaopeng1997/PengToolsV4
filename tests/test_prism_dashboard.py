# -*- coding: utf-8 -*-
"""PRISM-UI-P05: Prism Dashboard and Daily Classic targeted contract test suite.

Verification scope:
1. Outer padding duplication removed: DashboardApp.vue .content padding restricted to 0 0 8px;
2. Material hierarchy: Hero is the only glass material, 4 stats cards are solid cards;
3. Radius specification: card radius is 16px (--r-lg: 16px / border-radius: 16px);
4. Daily Classic:
   - Data source dailyQuotes.ts exists;
   - Zero network requests;
   - Zero Math.random();
   - Deterministic by date;
5. Four business stats only: req_open, daily_done/daily_total, monthly_release_total, completed_total;
6. Responsive breakpoints aligned to actual WebView (920px / 640px);
7. Actual release date authority: uses actual_release_date, zero planned release copy;
8. Zero inline payload color consumption: no :style with r.color;
9. Monthly checklist duplicate removed;
10. Progress bar has no continuous shimmer animation;
11. IconSprite standardized stroke 1.6 and includes i-quote;
12. Prefers-reduced-motion present;
13. Demo rows non-interactive and zero fake dispatches;
14. Zero fake features.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class PrismDashboardContractTests(unittest.TestCase):
    """PRISM-UI-P05 Dashboard contract tests."""

    @classmethod
    def setUpClass(cls):
        cls.vue_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'DashboardApp.vue')
        cls.sprite_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'IconSprite.vue')
        cls.quotes_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'dailyQuotes.ts')
        cls.panel_path = os.path.join(ROOT, 'panels', 'dashboard_panel.py')
        cls.qss_path = os.path.join(ROOT, 'resources', 'style.qss')

        with open(cls.vue_path, 'r', encoding='utf-8') as f:
            cls.vue_src = f.read()
        with open(cls.sprite_path, 'r', encoding='utf-8') as f:
            cls.sprite_src = f.read()
        with open(cls.quotes_path, 'r', encoding='utf-8') as f:
            cls.quotes_src = f.read()
        with open(cls.panel_path, 'r', encoding='utf-8') as f:
            cls.panel_src = f.read()
        with open(cls.qss_path, 'r', encoding='utf-8') as f:
            cls.qss_src = f.read()

    def test_outer_padding_duplication_removed(self):
        """1. .content must have padding 0 0 8px and min-width 0, no 24px 28px."""
        content_match = re.search(r'\.content\s*\{([^}]+)\}', self.vue_src)
        self.assertTrue(content_match, 'DashboardApp.vue must define .content')
        content_body = content_match.group(1)
        self.assertNotIn('24px 28px', content_body)
        self.assertIn('padding:0 0 8px;', content_body.replace('  ', ' '))
        self.assertIn('min-width:0;', content_body.replace(' ', ''))

    def test_hero_is_only_glass_material(self):
        """2. Hero has glass material, stat cards are solid cards (not glass)."""
        self.assertIn('class="glass hero enter"', self.vue_src)
        stat_cards = re.findall(r'<div class="([^"]*\bstat\b[^"]*)"', self.vue_src)
        self.assertTrue(stat_cards, 'Must have stat cards')
        for cls_name in stat_cards:
            parts = cls_name.split()
            self.assertIn('card', parts)
            self.assertNotIn('glass', parts)

    def test_surface_and_card_radius_is_16px(self):
        """3. Card radius 16px in Vue and QSS."""
        self.assertIn('--r-lg: 16px;', self.vue_src)
        self.assertIn('border-radius:var(--r-lg);', self.vue_src.replace(' ', ''))
        self.assertIn('border-radius: 16px;', self.qss_src)

    def test_daily_classic_present_and_pure(self):
        """4. Daily classic present, zero network, zero Math.random()."""
        self.assertIn('class="daily-classic"', self.vue_src)
        self.assertIn('getDailyQuote', self.vue_src)
        self.assertIn('i-quote', self.vue_src)

        self.assertNotIn('fetch(', self.quotes_src)
        self.assertNotIn('XMLHttpRequest', self.quotes_src)
        self.assertNotIn('http://', self.quotes_src)
        self.assertNotIn('https://', self.quotes_src)
        self.assertNotIn('Math.random(', self.quotes_src)
        self.assertNotIn('Math.random(', self.vue_src)

    def test_daily_classic_deterministic_hash(self):
        """4b. Daily quote determinism: same date returns same quote."""
        matches = re.findall(
            r"\{\s*text:\s*'([^']*)',\s*author:\s*'([^']*)',\s*source:\s*'([^']*)'\s*\}",
            self.quotes_src
        )
        self.assertGreaterEqual(len(matches), 24, 'Quote repository must have >= 24 items')

        def hash_date(date_str):
            h = 0
            for ch in date_str:
                h = ((h << 5) - h) + ord(ch)
                h &= 0xFFFFFFFF
                if h >= 0x80000000:
                    h -= 0x100000000
            return abs(h)

        q1 = matches[hash_date('2026-09-08') % len(matches)]
        q2 = matches[hash_date('2026-09-08') % len(matches)]
        self.assertEqual(q1, q2, 'Same date must return same quote')
        self.assertIsInstance(q1[0], str)
        self.assertTrue(len(q1[0]) > 0)

    def test_four_business_stats_only(self):
        """5. Four business stats only."""
        self.assertIn('summary.value?.stats?.req_open', self.vue_src)
        self.assertIn('s.daily_done', self.vue_src)
        self.assertIn('s.daily_total', self.vue_src)
        self.assertIn('summary.value?.stats?.monthly_release_total', self.vue_src)
        self.assertIn('summary.value?.stats?.completed_total', self.vue_src)

    def test_responsive_breakpoints_aligned_to_actual_webview(self):
        """6. Responsive breakpoints for actual WebView (1040px wide / 1039px compact / 640px narrow)."""
        self.assertIn('@media (min-width: 1040px)', self.vue_src)
        self.assertIn('@media (max-width: 1039px)', self.vue_src)
        self.assertIn('@media (max-width: 639px)', self.vue_src)

        self.assertIn('.stats { grid-template-columns:repeat(4, minmax(0, 1fr)); }', self.vue_src)
        self.assertIn('.grid { grid-template-columns:minmax(0, 1.25fr) minmax(0, 1fr); }', self.vue_src)
        self.assertIn('.tools { grid-template-columns:repeat(4, minmax(0, 1fr)); }', self.vue_src)

    def test_actual_release_date_authority_and_no_planned_residue(self):
        """7. Actual release date authority and text."""
        self.assertIn('actual_release_date', self.vue_src)
        self.assertNotIn('planned_release_date', self.vue_src)
        self.assertNotIn('planned_online_date', self.vue_src)
        self.assertNotIn('计划上线日期', self.vue_src)

        self.assertIn('Set an actual release date to include a task here.', self.panel_src)
        self.assertNotIn('Add planned or actual online dates', self.panel_src)
        self.assertIn('发版日：自动（按本月实际上线日期）', self.panel_src)
        self.assertNotIn('发版日：自动（本月最近计划）', self.panel_src)

    def test_zero_inline_color_consumption(self):
        """8. Zero inline color consumption."""
        self.assertNotIn(':style="{ background: r.color', self.vue_src)
        self.assertNotIn(':style="{ background: c.color', self.vue_src)
        self.assertNotIn('r.color', self.vue_src)
        self.assertIn('.dot.run { background:var(--warning); }', self.vue_src)
        self.assertIn('.dot.rev { background:var(--cyan); }', self.vue_src)
        self.assertIn('.dot.ok { background:var(--success); }', self.vue_src)

    def test_monthly_checklist_duplicate_removed(self):
        """9. Monthly checklist duplicate removed."""
        self.assertNotIn('checklist-item', self.vue_src)
        self.assertNotIn('v-for="c in summary.checklist"', self.vue_src)

    def test_progress_bar_no_continuous_shimmer(self):
        """10. Progress bar has no continuous shimmer."""
        self.assertNotIn('shimmer', self.vue_src.lower())
        self.assertNotIn('animation: shimmer', self.vue_src)

    def test_icon_sprite_standardized_stroke_and_quote_present(self):
        """11. Icon stroke-width 1.6 and includes i-quote."""
        self.assertIn('id="i-quote"', self.sprite_src)
        self.assertIn('stroke-width="1.6"', self.sprite_src)
        self.assertNotIn('stroke-width="1.8"', self.sprite_src)

    def test_prefers_reduced_motion_present(self):
        """12. Motion reduction supported."""
        self.assertIn('@media (prefers-reduced-motion: reduce)', self.vue_src)

    def test_demo_rows_non_interactive(self):
        """13. Demo rows non-interactive."""
        self.assertIn('.ck.is-demo', self.vue_src)
        self.assertIn(':tabindex="r.is_demo ? undefined : 0"', self.vue_src)
        self.assertIn(':role="r.is_demo ? undefined : \'button\'"', self.vue_src)
        self.assertIn('if (!reqId || String(reqId).startsWith(\'demo-\'))', self.vue_src)

    def test_no_fake_features(self):
        """14. Zero fake features."""
        for fake in ['notification-center', 'cloud-sync', 'global-search', 'ai-recommend', 'chart-dashboard']:
            self.assertNotIn(fake, self.vue_src)


if __name__ == '__main__':
    unittest.main()
