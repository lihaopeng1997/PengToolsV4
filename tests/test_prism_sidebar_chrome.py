# -*- coding: utf-8 -*-
"""PRISM-UI-P04: Prism Sidebar Chrome Web + Native 契约与专项测试。

验证范围：
1. 导航模型与索引完整性：24 个导航载荷索引，22 个叶子节点，2 个父级折叠节点；
2. Web Chrome 源码契约：40px 项高、10px 圆角、20px 图标、64px 品牌行、Prism 变量消费、
   零旧 V2 颜色遗留、禁用品牌旋转、具备 prefers-reduced-motion、无假功能 UI；
3. Native Fallback 侧栏契约：几何模式 248 / 220 / 84 / 72、品牌区 64px、44x44 icon-only
   命中区、父级展开/折叠回调不触发导航、用户手动折叠偏好优先。
"""
import json
import os
import re
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication
from ui.navigation_model import (
    NAV_MODEL, is_parent_nav, FIXED_DB_PAGES, AI_CHAT_NAV, AI_WORKBENCH_NAV,
)
from ui.layout_metrics import NAV_WIDE, NAV_STANDARD, NAV_ICON, NAV_NARROW

_app = QApplication.instance() or QApplication([])


class NavigationModelPrismContractTests(unittest.TestCase):
    """验证导航模型：24 个导航索引（22 叶子 + 2 父级）。"""

    def test_navigation_model_counts_and_types(self):
        all_indices = []
        parents = []
        leaves = []
        for group_key, items in NAV_MODEL:
            for item in items:
                idx = item[0]
                all_indices.append(idx)
                if is_parent_nav(idx):
                    parents.append(idx)
                else:
                    leaves.append(idx)

        # 加上可展开组包含的子项 + 独立设置叶子项（nav 7）
        db_children = [p[0] for p in FIXED_DB_PAGES]
        ai_children = [AI_CHAT_NAV, AI_WORKBENCH_NAV]
        settings_leaf = [7]
        total_leaves = leaves + db_children + ai_children + settings_leaf

        self.assertEqual(len(parents), 2, f'必须恰好有 2 个父级折叠项: {parents}')
        self.assertIn(14, parents, '14 必须是数据中心父节点')
        self.assertIn(15, parents, '15 必须是模型父节点')
        self.assertEqual(len(total_leaves), 22, f'必须恰好有 22 个叶子节点: {len(total_leaves)}')
        self.assertEqual(len(parents) + len(total_leaves), 24, '全量导航项总数必须为 24')

    def test_web_nav_model_structure(self):
        from main_window import MainWindow
        win = MainWindow()
        model_dict = win._build_web_nav_model()

        self.assertIn('groups', model_dict)
        self.assertIn('settings', model_dict)
        self.assertEqual(model_dict['settings']['i'], 7)

        parent_14 = None
        parent_15 = None
        for g in model_dict['groups']:
            for it in g['items']:
                if it['i'] == 14:
                    parent_14 = it
                elif it['i'] == 15:
                    parent_15 = it

        self.assertIsNotNone(parent_14, 'Web nav model 必须包含 14 数据库父节点')
        self.assertIsNotNone(parent_15, 'Web nav model 必须包含 15 模型父节点')
        self.assertTrue(len(parent_14.get('children', [])) >= 6, '14 必须包含至少 6 个数据库子项')
        self.assertTrue(len(parent_15.get('children', [])) == 2, '15 必须包含 2 个 AI 子项')


class WebChromeSourceContractTests(unittest.TestCase):
    """验证 Vue 源码中的 Prism 契约。"""

    @classmethod
    def setUpClass(cls):
        cls.vue_path = os.path.join(ROOT, 'frontend', 'src', 'chrome', 'ChromeApp.vue')
        cls.sprite_path = os.path.join(ROOT, 'frontend', 'src', 'chrome', 'IconSprite.vue')
        with open(cls.vue_path, encoding='utf-8') as f:
            cls.vue_src = f.read()
        with open(cls.sprite_path, encoding='utf-8') as f:
            cls.sprite_src = f.read()

    def test_no_old_v2_color_residue(self):
        old_colors = ['#F6F8FE', '#1B1E2A', '#5B73FF', '#4A61F0', '#111114']
        for oc in old_colors:
            self.assertNotIn(oc.lower(), self.vue_src.lower(),
                             f'ChromeApp.vue 不得残留旧 V2 颜色 {oc}')
            self.assertNotIn(oc.lower(), self.sprite_src.lower(),
                             f'IconSprite.vue 不得残留旧 V2 颜色 {oc}')

    def test_prism_tokens_and_geometry(self):
        # 40px item height, 10px radius, 64px brand row
        self.assertIn('height: 40px', self.vue_src, '必须具备 40px nav-item 高度')
        self.assertIn('--r-sm: 10px', self.vue_src, '必须具备 10px 圆角 token')
        self.assertIn('height: 64px', self.vue_src, '品牌行高度必须为 64px')
        self.assertIn('width: 20px', self.vue_src, '普通导航图标必须定义 20px 尺寸')
        self.assertIn('height: 20px', self.vue_src, '普通导航图标必须定义 20px 尺寸')

        # 验证单一权威 ThemeManager 语义 token 消费（无本地重复调色板）
        tokens = [
            '--sidebar-bg', '--sidebar-text', '--nav-hover', '--nav-active-bg',
            '--primary', '--primary-grad-start', '--primary-grad-end',
            '--aurora-start', '--aurora-mid', '--scroll-handle',
        ]
        for tk in tokens:
            self.assertIn(f'var({tk})', self.vue_src, f'ChromeApp.vue 必须纯消费语义变量 var({tk})')

        # 验证未重复定义调色板
        self.assertNotIn('html[data-theme="black"] {', self.vue_src, '不得在 Vue 内重复定义 Black 主题调色板')

    def test_icon_only_hitbox_and_rules(self):
        self.assertIn('@media (max-width: 120px)', self.vue_src, '必须定义 <= 120px 的 icon-only 响应式分支')
        self.assertIn('width: 44px', self.vue_src, 'Icon-only 命中区必须为 44x44')
        self.assertIn('height: 44px', self.vue_src, 'Icon-only 命中区必须为 44x44')

    def test_brand_behavior_and_motion(self):
        # 禁止 brand:hover rotate / scale
        self.assertNotIn('rotate(-4deg)', self.vue_src, '不得对品牌区应用旋转玩具动画')
        self.assertNotIn('scale(1.03)', self.vue_src, '不得对品牌区应用放大玩具动画')
        self.assertIn('prefers-reduced-motion', self.vue_src, '必须包含 prefers-reduced-motion 降噪规则')

    def test_icon_sprite_prism_brand_and_stroke(self):
        self.assertIn('id="i-logo"', self.sprite_src, '必须定义 i-logo')
        self.assertIn('viewBox="0 0 64 64"', self.sprite_src, 'Prism 品牌矢量必须使用 64x64 viewBox')
        self.assertIn('var(--primary-grad-start)', self.sprite_src, '品牌渐变起色必须消费 --primary-grad-start')
        self.assertIn('var(--primary-grad-end)', self.sprite_src, '品牌渐变终色必须消费 --primary-grad-end')
        self.assertIn('var(--accent-cyan)', self.sprite_src, '品牌星芒必须消费 --accent-cyan')
        self.assertIn('var(--nav-active-text)', self.sprite_src, '品牌字标必须消费 --nav-active-text')
        self.assertIn('stroke-width="1.6"', self.sprite_src, '线性图标必须统一使用 1.6 描边')

    def test_no_fake_ui_elements(self):
        for forbidden in ['fake', 'notification-badge', 'cloud-sync', 'search-box', 'tab-bar']:
            self.assertNotIn(forbidden, self.vue_src.lower())


class NativeFallbackPrismTests(unittest.TestCase):
    """验证 Native Fallback 侧栏与响应式契约。"""

    def setUp(self):
        from main_window import MainWindow
        self.win = MainWindow()

    def test_sidebar_widths_and_brand_geometry(self):
        # 0. 确保非折叠状态以测试纯响应式宽度切换
        self.win._set_nav_collapsed(False, persist=False)

        # 1. 初始 wide / standard
        self.win._on_layout_mode('wide', False)
        self.assertEqual(self.win._sidebar.width(), NAV_WIDE)
        if self.win._sidebar_stack:
            self.assertEqual(self.win._sidebar_stack.width(), NAV_WIDE)

        self.win._on_layout_mode('standard', False)
        self.assertEqual(self.win._sidebar.width(), NAV_STANDARD)
        if self.win._sidebar_stack:
            self.assertEqual(self.win._sidebar_stack.width(), NAV_STANDARD)

        # 2. Compact / Narrow
        self.win._on_layout_mode('compact', False)
        self.assertEqual(self.win._sidebar.width(), NAV_ICON)
        self.assertTrue(self.win._nav_icon_only)
        if self.win._sidebar_stack:
            self.assertEqual(self.win._sidebar_stack.width(), NAV_ICON)

        self.win._on_layout_mode('narrow', False)
        self.assertEqual(self.win._sidebar.width(), NAV_NARROW)
        self.assertTrue(self.win._nav_icon_only)
        if self.win._sidebar_stack:
            self.assertEqual(self.win._sidebar_stack.width(), NAV_NARROW)

        # 3. 品牌行 64px
        self.assertEqual(self.win._brand_block.height(), 64)

    def test_parent_expand_collapse_callbacks(self):
        cur_idx = self.win._current_nav_index

        # SQL 控制台父折叠
        initial_sql = self.win._sql_console_expanded
        self.win._toggle_sql_console()
        self.assertEqual(self.win._sql_console_expanded, not initial_sql)
        self.assertEqual(self.win._current_nav_index, cur_idx, '父项展开折叠绝不得改变当前导航页')

        # 模型父折叠
        initial_ai = self.win._ai_expanded
        self.win._toggle_ai_group()
        self.assertEqual(self.win._ai_expanded, not initial_ai)
        self.assertEqual(self.win._current_nav_index, cur_idx, '父项展开折叠绝不得改变当前导航页')

    def test_user_collapsed_preference_preserved(self):
        self.win._set_nav_collapsed(True, persist=False)
        self.assertTrue(self.win._nav_collapsed)
        self.assertEqual(self.win._sidebar.width(), NAV_ICON)
        self.assertTrue(self.win._nav_icon_only)
        # 验证 Settings 保持可见且处于 icon-only 状态（FIX-A 契约）
        self.assertFalse(self.win.settings_button.isHidden(), '手动折叠必须保留设置按钮可见')
        self.assertTrue(self.win.settings_button.property('iconOnly'), '手动折叠必须置 iconOnly 为 True')
        self.assertEqual(self.win.settings_button.text(), '', '手动折叠设置按钮必须隐藏文字')
        self.assertTrue(bool(self.win.settings_button.toolTip()), '手动折叠设置按钮必须保留 tooltip')

        # 恢复
        self.win._set_nav_collapsed(False, persist=False)
        self.assertFalse(self.win._nav_collapsed)
        self.assertFalse(self.win.settings_button.isHidden())
        self.assertFalse(self.win.settings_button.property('iconOnly'))


if __name__ == '__main__':
    unittest.main()
