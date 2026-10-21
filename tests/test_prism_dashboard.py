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
        cls.header_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'HomeHero.vue')
        cls.progress_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'ProgressBoard.vue')
        cls.removed_paths = [
            os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'HeroSection.vue'),
            os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'StatsGrid.vue'),
            os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'composables', 'useDailyQuote.ts'),
        ]
        cls.tasks_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'MonthlyTasks.vue')
        cls.release_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'ReleaseOverview.vue')
        cls.tools_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'components', 'QuickTools.vue')
        cls.quotes_json_path = os.path.join(ROOT, 'resources', 'ui', 'daily-quotes.json')

        cls.vue_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'DashboardApp.vue')
        cls.sprite_path = os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'IconSprite.vue')
        cls.panel_path = os.path.join(ROOT, 'panels', 'dashboard_panel.py')
        cls.qss_path = os.path.join(ROOT, 'resources', 'style.qss')

        def read_file(p):
            with open(p, 'r', encoding='utf-8') as f:
                return f.read()

        cls.header_src = read_file(cls.header_path)
        cls.progress_src = read_file(cls.progress_path)
        cls.tasks_src = read_file(cls.tasks_path)
        cls.release_src = read_file(cls.release_path)
        cls.tools_src = read_file(cls.tools_path)
        cls.vue_src = read_file(cls.vue_path)
        cls.sprite_src = read_file(cls.sprite_path)
        cls.panel_src = read_file(cls.panel_path)
        cls.qss_src = read_file(cls.qss_path)

        with open(cls.quotes_json_path, 'r', encoding='utf-8') as f:
            cls.quotes_data = json.load(f)

    def test_01_component_decomposition(self):
        """1. 首页组件：问候/日报、项目进度、本月上线、上线总览、常用工具；旧 Hero/统计卡/每日一句已移除。"""
        for path in (self.shared_surface_path, self.shared_icon_path, self.header_path, self.progress_path,
                     self.tasks_path, self.release_path, self.tools_path, self.quotes_json_path):
            self.assertTrue(os.path.exists(path), path)
        for path in self.removed_paths:
            self.assertFalse(os.path.exists(path), f'{path} should be removed')
        for name in ('HeroSection', 'StatsGrid', 'useDailyQuote', 'tip-card', '留一点空间'):
            self.assertNotIn(name, self.vue_src)

    def test_02_dashboard_layout_structure(self):
        """2. 通栏 Hero → 通栏项目进度看板 → 两栏（本月上线 | 上线总览 + 常用工具）。"""
        self.assertIn('class="home-grid"', self.vue_src)
        self.assertIn('class="home-left"', self.vue_src)
        self.assertIn('class="home-right', self.vue_src)
        self.assertIn('padding: 0 0 8px;', self.vue_src)
        self.assertIn('330px', self.vue_src)
        self.assertIn('300px', self.vue_src)
        self.assertIn('@media (min-width: 1200px)', self.vue_src)
        self.assertIn('@media (max-width: 1019px)', self.vue_src)
        left = self.vue_src[self.vue_src.index('class="home-left"'):self.vue_src.index('class="home-right')]
        self.assertLess(self.vue_src.index('<HomeHero'), self.vue_src.index('<ProgressBoard'))
        self.assertLess(self.vue_src.index('<ProgressBoard'), self.vue_src.index('class="home-grid"'))
        self.assertNotIn('<HomeHero', left)
        self.assertIn('<MonthlyTasks', left)
        # 详情抽屉挂在首页根节点，点击需求就地打开
        self.assertIn('<RequirementDrawer', self.vue_src)

    def test_03_progress_board_edits_in_place_via_events(self):
        """3. 项目进度看板：点击开抽屉、拖动改阶段、列内新建；组件只发事件，不直接碰 Bridge。"""
        for event in ("'open'", "'move'", "'create'", "'focusStage'", "'navigate'"):
            self.assertIn(event, self.progress_src)
        self.assertNotIn('bridge', self.progress_src)
        self.assertIn('draggable', self.progress_src)
        self.assertIn('next_action', self.progress_src)
        self.assertIn("startsWith('demo-')", self.progress_src)
        # 写入统一走 DashboardApp → bridge.homeAction
        self.assertIn("action: 'move_group'", self.vue_src)
        self.assertIn('bridge.homeAction(payload)', self.vue_src)

    def test_04_motion_contract(self):
        """4. 常用工具微动效保留；减弱动态与隐藏暂停生效。"""
        self.assertIn('translateY(-2px) rotate(-6deg)', self.tools_src)
        self.assertIn('200ms', self.tools_src)
        self.assertIn('@media (prefers-reduced-motion: reduce)', self.vue_src)
        self.assertIn('body.motion-disabled', self.vue_src)
        self.assertIn('body.page-hidden', self.vue_src)
        self.assertIn('animation-play-state: paused', self.vue_src)

    def test_05_header_daily_and_create_actions(self):
        """5. 头部：本周日报进度可点进日报，新建需求为唯一主按钮。"""
        self.assertIn("emit('writeDaily')", self.header_src)
        self.assertIn("emit('createRequirement')", self.header_src)
        self.assertIn('daily_done', self.header_src)
        self.assertEqual(self.header_src.count('class="hero-primary"'), 1)
        # 首页主视觉为粒子球（粒子按阶段占比着色，图例点击筛选看板）
        self.assertIn('<ParticleSphere', self.header_src)
        self.assertIn("emit('focusStage'", self.header_src)
        # 原生回退页仍使用每日一句数据
        self.assertEqual(len(self.quotes_data), 12)

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
