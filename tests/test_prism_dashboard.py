# -*- coding: utf-8 -*-
"""PRISM-UI-P05: Prism Dashboard and Daily Classic targeted contract test suite.

Authoritative specification:
《UI优化需求_晴空棱镜_Agent实施规范 V2.0》Section 7 & Section 11.

Verification scope:
1. Component decomposition:
   - PrismSurface.vue & PrismIcon.vue in shared/components/
   - HeroSection.vue, StatsGrid.vue, MonthlyTasks.vue, ReleaseOverview.vue, QuickTools.vue in dashboard/components/
   - useDailyQuote.ts in dashboard/composables/
   - resources/ui/daily-quotes.json
2. Dashboard Layout (Section 7.1):
   - 2-column home-grid: home-left and home-right
   - Responsive: C>=1200 (R330), 1020<=C<1200 (R300), C<1020 (single column)
   - Outer padding 0 0 8px
3. Hero Structure & 120x120 Graphic (Section 7.1 & 7.2):
   - 176px minimum height, padding 24
   - 120x120 decorative prism-orb graphic
   - 4.8s ease-in-out loop: y: 0/-5/0, rotate: -5/5/-5deg, scale: 1/1.04/1
4. Motion Contract (Section 7.2):
   - 4 stat icons: 5s period, only 80%-100% rises 2px, stagger delays 0, 0.3, 0.6, 0.9s
   - Quick tools: hover/focus translateY(-2px) rotate(-6deg), 200ms
   - prefers-reduced-motion, motion-disabled, page-hidden visibility
5. Daily Classic Quotes (Section 7.3 & 11):
   - 12 items in resources/ui/daily-quotes.json (id, text, source, author)
   - Deterministic UTC day ordinal from local date % 12
   - Zero toISOString().slice(0, 10)
   - 28x28 next-quote button
   - 60s timer and visibilitychange check
6. Native Fallback (Section 7.2 & 13):
   - dashboard_panel.py reads resources/ui/daily-quotes.json
   - Hero card with date line, quote, refresh button
   - 120x120 PrismOrbWidget with single QVariantAnimation
7. Business Logic Protection:
   - actual_release_date authority, zero planned release copy
   - Zero payload color consumption
   - Demo safety (non-interactive)
   - Zero fake features
"""
import datetime
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class PrismDashboardContractTests(unittest.TestCase):
    """PRISM-UI-P05 Dashboard contract tests according to V2.0 Spec."""

    @classmethod
    def setUpClass(cls):
        cls.shared_surface_path = os.path.join(ROOT, 'frontend', 'src', 'shared', 'components', 'PrismSurface.vue')
        cls.shared_icon_path = os.path.join(ROOT, 'frontend', 'src', 'shared', 'components', 'PrismIcon.vue')
        cls.hero_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'HeroSection.vue')
        cls.stats_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'StatsGrid.vue')
        cls.tasks_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'MonthlyTasks.vue')
        cls.release_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'ReleaseOverview.vue')
        cls.tools_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'QuickTools.vue')
        cls.use_quote_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'composables', 'useDailyQuote.ts')
        cls.quotes_json_path = os.path.join(ROOT, 'resources', 'ui', 'daily-quotes.json')

        cls.vue_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'DashboardApp.vue')
        cls.sprite_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'IconSprite.vue')
        cls.panel_path = os.path.join(ROOT, 'panels', 'dashboard_panel.py')
        cls.qss_path = os.path.join(ROOT, 'resources', 'style.qss')

        def read_file(p):
            with open(p, 'r', encoding='utf-8') as f:
                return f.read()

        cls.hero_src = read_file(cls.hero_path)
        cls.stats_src = read_file(cls.stats_path)
        cls.tasks_src = read_file(cls.tasks_path)
        cls.release_src = read_file(cls.release_path)
        cls.tools_src = read_file(cls.tools_path)
        cls.use_quote_src = read_file(cls.use_quote_path)
        cls.vue_src = read_file(cls.vue_path)
        cls.sprite_src = read_file(cls.sprite_path)
        cls.panel_src = read_file(cls.panel_path)
        cls.qss_src = read_file(cls.qss_path)

        with open(cls.quotes_json_path, 'r', encoding='utf-8') as f:
            cls.quotes_data = json.load(f)

    def test_01_component_decomposition(self):
        """1. Component decomposition: all 8 required files exist and are clean."""
        self.assertTrue(os.path.exists(self.shared_surface_path), 'PrismSurface.vue must exist')
        self.assertTrue(os.path.exists(self.shared_icon_path), 'PrismIcon.vue must exist')
        self.assertTrue(os.path.exists(self.hero_path), 'HeroSection.vue must exist')
        self.assertTrue(os.path.exists(self.stats_path), 'StatsGrid.vue must exist')
        self.assertTrue(os.path.exists(self.tasks_path), 'MonthlyTasks.vue must exist')
        self.assertTrue(os.path.exists(self.release_path), 'ReleaseOverview.vue must exist')
        self.assertTrue(os.path.exists(self.tools_path), 'QuickTools.vue must exist')
        self.assertTrue(os.path.exists(self.use_quote_path), 'useDailyQuote.ts must exist')
        self.assertTrue(os.path.exists(self.quotes_json_path), 'daily-quotes.json must exist')

    def test_02_dashboard_layout_structure(self):
        """2. Dashboard Layout: 2-column home-grid (home-left and home-right) and breakpoints."""
        self.assertIn('class="home-grid"', self.vue_src)
        self.assertIn('class="home-left"', self.vue_src)
        self.assertIn('class="home-right', self.vue_src)

        # Padding 0 0 8px
        self.assertIn('padding: 0 0 8px;', self.vue_src)

        # Breakpoints
        self.assertIn('330px', self.vue_src)
        self.assertIn('300px', self.vue_src)
        self.assertIn('@media (min-width: 1200px)', self.vue_src)
        self.assertIn('@media (max-width: 1019px)', self.vue_src)

    def test_03_hero_structure_and_graphic(self):
        """3. Hero: 176px minimum height, 120x120 prism-orb graphic and 4.8s animation."""
        self.assertIn('min-height: 176px;', self.hero_src)
        self.assertIn('height: auto;', self.hero_src)
        self.assertIn('width: 120px;', self.hero_src)
        self.assertIn('height: 120px;', self.hero_src)
        self.assertIn('class="prism-orb"', self.hero_src)

        # 4.8s animation contract: y: 0/-5/0, rotate: -5/5/-5deg, scale: 1/1.04/1
        self.assertIn('4.8s ease-in-out infinite', self.hero_src)
        self.assertIn('translateY(-5px)', self.hero_src)
        self.assertIn('rotate(-5deg)', self.hero_src)
        self.assertIn('scale(1.04)', self.hero_src)

    def test_04_motion_contract(self):
        """4. Motion contract: 5s stat stagger, quick tool translateY(-2px) rotate(-6deg)."""
        # Stat icons: 5s period, only 80%-100% rises 2px, stagger 0, 0.3, 0.6, 0.9s
        self.assertIn('5s ease-in-out infinite 0s', self.stats_src)
        self.assertIn('5s ease-in-out infinite 0.3s', self.stats_src)
        self.assertIn('5s ease-in-out infinite 0.6s', self.stats_src)
        self.assertIn('5s ease-in-out infinite 0.9s', self.stats_src)
        self.assertIn('translateY(-2px)', self.stats_src)

        # Quick tools: translateY -2px rotate -6deg, 200ms
        self.assertIn('translateY(-2px) rotate(-6deg)', self.tools_src)
        self.assertIn('200ms', self.tools_src)

        # Prefers reduced motion & visibility pause
        self.assertIn('@media (prefers-reduced-motion: reduce)', self.vue_src)
        self.assertIn('body.motion-disabled', self.vue_src)
        self.assertIn('body.page-hidden', self.vue_src)
        self.assertIn('animation-play-state: paused', self.vue_src)

    def test_05_daily_classic_quote_system(self):
        """5. Daily quote: 12 JSON items, UTC day ordinal, 28x28 button, 60s timer."""
        # 12 items with stable id
        self.assertEqual(len(self.quotes_data), 12)
        for i, q in enumerate(self.quotes_data):
            self.assertEqual(q['id'], f'quote-{i+1:02d}')
            self.assertTrue(len(q['text']) > 0)
            self.assertTrue(len(q['source']) > 0)
            self.assertTrue(len(q['author']) > 0)

        # Zero toISOString().slice(0, 10)
        self.assertNotIn('toISOString().slice(0, 10)', self.use_quote_src)
        self.assertNotIn('toISOString().slice(0, 10)', self.vue_src)

        # UTC day ordinal function
        self.assertIn('Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000', self.use_quote_src)

        # 28x28 next quote button
        self.assertIn('width: 28px;', self.hero_src)
        self.assertIn('height: 28px;', self.hero_src)
        self.assertIn('class="quote-next"', self.hero_src)

        # 60s timer & visibilitychange
        self.assertIn('setInterval(checkDate, 60000)', self.use_quote_src)
        self.assertIn('visibilitychange', self.use_quote_src)

    def test_06_native_fallback_consistency(self):
        """6. Native fallback reads daily-quotes.json, has hero card and 120x120 PrismOrbWidget."""
        self.assertIn('daily-quotes.json', self.panel_src)
        self.assertIn('class PrismOrbWidget', self.panel_src)
        self.assertIn('self.hero_card = QFrame()', self.panel_src)
        self.assertIn('self.hero_card.setMinimumHeight(176)', self.panel_src)
        self.assertIn('self.quote_refresh_btn', self.panel_src)
        self.assertIn('self.prism_orb = PrismOrbWidget', self.panel_src)
        self.assertIn('QVariantAnimation', self.panel_src)
        self.assertIn('#dashboard-hero-card', self.qss_src)

    def test_07_business_logic_protection(self):
        """7. Actual release date authority, zero planned copy, demo isolation, zero fake features."""
        self.assertIn('actual_release_date', self.tasks_src)
        self.assertNotIn('planned_release_date', self.tasks_src)
        self.assertNotIn('planned_online_date', self.tasks_src)
        self.assertNotIn('计划上线日期', self.tasks_src)

        # Demo non-interactive
        self.assertIn('clickable', self.tasks_src)
        self.assertIn('is-demo', self.tasks_src)
        self.assertIn('demo-', self.vue_src)

        # No fake features
        for fake in ['notification-center', 'cloud-sync', 'global-search', 'ai-recommend', 'chart-dashboard']:
            self.assertNotIn(fake, self.vue_src)

    def test_08_icon_sprite_quote_and_stroke(self):
        """8. IconSprite has i-quote and stroke-width 1.6."""
        self.assertIn('id="i-quote"', self.sprite_src)
        self.assertIn('stroke-width="1.6"', self.sprite_src)
        self.assertNotIn('stroke-width="1.8"', self.sprite_src)


if __name__ == '__main__':
    unittest.main()
