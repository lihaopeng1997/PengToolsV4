# -*- coding: utf-8 -*-
"""PRISM-UI-P01 晴空棱镜设计系统与基础度量契约测试。

覆盖 17 项基础契约：
1. THEME_IDS: exactly calm + black
2. ALIASES: legacy aliases migrate correctly
3. SETTINGS: old settings still normalize correctly
4. PRISM_CALM_CORE_TOKENS: exact palette values
5. PRISM_BLACK_CORE_TOKENS: exact palette values
6. ALL_EXISTING_REQUIRED_TOKENS: present in both themes
7. QSS_RENDER: no unresolved __TOKEN__
8. GLASS_ALPHA: calm 236, black 238
9. DENSITY: compact 32/32, comfortable 36/40
10. FIELD_METRICS: business dense = 28
11. DATES: 28 height + original width ranges
12. BUTTON_ROLE: actionRole property preserved
13. BUTTON_COMPACT: compactAction=True + 28
14. BUTTON_NORMAL: compactAction=False + 32
15. SURFACE_ROLE: surfaceRole property set correctly
16. OBJECT_NAME_COMPAT: legacy selectors remain usable alongside properties
17. WEB_THEME_TOKEN_PARITY: palette and web theme payload consistency
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from PyQt6.QtWidgets import (
        QApplication,
        QComboBox,
        QDateEdit,
        QFrame,
        QLineEdit,
        QPushButton,
    )
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class TestPrismFoundation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_01_theme_ids_and_meta(self):
        """1. THEME_IDS: 必须严格为 ('calm', 'black')，显示名对齐。"""
        from ui.theme_manager import THEME_IDS, THEMES, THEME_META

        self.assertEqual(THEME_IDS, ('calm', 'black'))
        self.assertEqual(set(THEMES.keys()), {'calm', 'black'})
        self.assertEqual(set(THEME_META.keys()), {'calm', 'black'})

        calm_meta = THEME_META['calm']
        self.assertEqual(calm_meta[0], '晴空棱镜')
        self.assertEqual(calm_meta[1], 'Sky Prism')

        black_meta = THEME_META['black']
        self.assertEqual(black_meta[0], '墨黑')
        self.assertEqual(black_meta[1], 'Ink Black')

    def test_02_aliases(self):
        """2. ALIASES: 历史别名规范解析至 calm 与 black。"""
        from ui.theme_manager import resolve_theme_id

        self.assertEqual(resolve_theme_id('clear'), 'calm')
        self.assertEqual(resolve_theme_id('warm'), 'calm')
        self.assertEqual(resolve_theme_id('light'), 'calm')
        self.assertEqual(resolve_theme_id('night'), 'black')
        self.assertEqual(resolve_theme_id('dark'), 'black')
        self.assertEqual(resolve_theme_id('unknown'), 'calm')

    def test_03_settings_normalization(self):
        """3. SETTINGS: 旧设置在 normalize_settings 下正确规范化。"""
        from config import normalize_settings

        for alias in ('clear', 'warm', 'light'):
            norm = normalize_settings({'ui_theme': alias})
            self.assertEqual(norm['ui_theme'], 'calm')

        for alias in ('night', 'dark'):
            norm = normalize_settings({'ui_theme': alias})
            self.assertEqual(norm['ui_theme'], 'black')

    def test_04_prism_calm_core_tokens(self):
        """4. PRISM_CALM_CORE_TOKENS: 权威母版色值校验。"""
        from ui.theme_manager import THEMES

        expected_calm = {
            'APP_BG': '#F4F3FA',
            'SURFACE': '#FDFDFF',
            'SURFACE_SOFT': '#F0ECFA',
            'PRIMARY': '#6C58D9',
            'PRIMARY_HOVER': '#5D49C5',
            'PRIMARY_SOFT': '#EEE9FF',
            'TEXT_STRONG': '#262438',
            'TEXT_MUTED': '#615D73',
            'BORDER': '#E6E2F0',
            'SUCCESS': '#247F75',
            'WARNING': '#A5772A',
            'DANGER': '#C45371',
            'ON_PRIMARY': '#FFFFFF',
        }
        pal = THEMES['calm']
        for key, exp in expected_calm.items():
            self.assertIn(key, pal)
            self.assertEqual(pal[key].upper(), exp.upper(), f'Calm {key} 期望 {exp}，实际 {pal[key]}')

    def test_05_prism_black_core_tokens(self):
        """5. PRISM_BLACK_CORE_TOKENS: 权威母版色值校验。"""
        from ui.theme_manager import THEMES

        expected_black = {
            'APP_BG': '#15151E',
            'SURFACE': '#1E1E2B',
            'SURFACE_SOFT': '#262437',
            'PRIMARY': '#A99AF5',
            'PRIMARY_HOVER': '#BCB0FF',
            'PRIMARY_SOFT': '#322B4D',
            'TEXT_STRONG': '#ECEAF7',
            'TEXT_MUTED': '#AEA9C2',
            'BORDER': '#393548',
            'SUCCESS': '#74CABB',
            'WARNING': '#E2B96D',
            'DANGER': '#F18CA7',
            'ON_PRIMARY': '#191527',
        }
        pal = THEMES['black']
        for key, exp in expected_black.items():
            self.assertIn(key, pal)
            self.assertEqual(pal[key].upper(), exp.upper(), f'Black {key} 期望 {exp}，实际 {pal[key]}')

    def test_06_all_existing_required_tokens(self):
        """6. ALL_EXISTING_REQUIRED_TOKENS: 扩展 token 完整性，无缺失。"""
        from ui.theme_manager import THEMES, missing_theme_tokens

        required = (
            'TERM_BG', 'TERM_FG', 'TERM_MUTED', 'TERM_BORDER', 'TERM_SEL', 'TERM_SYS',
            'TERM_CHROME', 'TERM_FIND_BG', 'CODE_BG', 'SEARCH_MATCH', 'SEARCH_CURRENT',
            'FOCUS_RING', 'STATUS_INFO_BG', 'STATUS_SUCCESS_BG', 'STATUS_WARNING_BG',
            'STATUS_DANGER_BG', 'GLASS_BG', 'GLASS_BORDER', 'GLASS_HIGHLIGHT',
            'GLASS_SHADOW', 'TABLE_ALT', 'TABLE_SELECT', 'INPUT_BG', 'DISABLED_BG',
            'DISABLED_TEXT', 'DISABLED_ICON', 'SHADOW', 'AURORA_START', 'AURORA_MID',
            'AURORA_END', 'ACCENT_INDIGO', 'ACCENT_CYAN', 'ACCENT_ROSE', 'ACCENT_EMERALD',
            'SIDEBAR_BG', 'SIDEBAR_BORDER', 'SIDEBAR_TEXT', 'SIDEBAR_TEXT_MUTED',
        )
        for tid, palette in THEMES.items():
            missing = missing_theme_tokens(palette, required)
            self.assertEqual(missing, (), f'{tid} 主题缺少必要扩展 token: {missing}')

    def test_07_qss_render_no_unresolved_tokens(self):
        """7. QSS_RENDER: 渲染后绝对无未解析的 __TOKEN__。"""
        from ui.theme_manager import ThemeManager, unresolved_qss_tokens

        tm = ThemeManager.instance()
        tm.load_template()
        for tid in ('calm', 'black'):
            rendered = tm.render(tid)
            unresolved = unresolved_qss_tokens(rendered)
            self.assertEqual(unresolved, (), f'{tid} QSS 存在未解析占位符: {unresolved}')

    def test_08_glass_alpha(self):
        """8. GLASS_ALPHA: Calm 236, Black 238。"""
        from ui.theme_manager import THEMES

        # calm GLASS_BG
        calm_glass = THEMES['calm']['GLASS_BG']
        m_calm = re.search(r'rgba\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*(\d+)\s*\)', calm_glass)
        self.assertIsNotNone(m_calm, f'无法解析 Calm GLASS_BG: {calm_glass}')
        self.assertEqual(int(m_calm.group(1)), 236)

        # black GLASS_BG
        black_glass = THEMES['black']['GLASS_BG']
        m_black = re.search(r'rgba\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*(\d+)\s*\)', black_glass)
        self.assertIsNotNone(m_black, f'无法解析 Black GLASS_BG: {black_glass}')
        self.assertEqual(int(m_black.group(1)), 238)

    def test_09_density_metrics(self):
        """9. DENSITY: compact 32/32, comfortable 36/40。"""
        from ui.design_system import density_metrics

        c = density_metrics('compact')
        self.assertEqual(c.control_height, 32)
        self.assertEqual(c.row_height, 32)

        comf = density_metrics('comfortable')
        self.assertEqual(comf.control_height, 36)
        self.assertEqual(comf.row_height, 40)

        # 未知默认降级到 compact
        fallback = density_metrics('unknown')
        self.assertEqual(fallback.control_height, 32)
        self.assertEqual(fallback.row_height, 32)

    def test_10_field_metrics_business_dense_28(self):
        """10. FIELD_METRICS: 业务紧凑输入与操作固定为 28px。"""
        from ui.field_metrics import BTN_COMPACT_H, FIELD_H, size_compact_button, size_field_height
        from ui import layout_metrics as lm

        self.assertEqual(FIELD_H, 28)
        self.assertEqual(BTN_COMPACT_H, 28)
        self.assertEqual(lm.FIELD_H, 28)
        self.assertEqual(lm.BTN_H_COMPACT, 28)

        # 实测 QWidget 几何属性
        le = QLineEdit()
        size_field_height(le)
        self.assertEqual(le.maximumHeight(), 28)
        self.assertEqual(le.minimumHeight(), 28)
        le.deleteLater()

        btn = QPushButton('Action')
        size_compact_button(btn)
        self.assertEqual(btn.maximumHeight(), 28)
        self.assertEqual(btn.minimumHeight(), 28)
        btn.deleteLater()

    def test_11_date_metrics(self):
        """11. DATES: 高度 28，区间 150-160 与 128-150，下拉固定宽 200。"""
        from ui.field_metrics import (
            COMBO_PICK_W,
            DATE_MONTH_W,
            DATE_W,
            size_date,
            size_pick_combo,
        )

        self.assertEqual(DATE_W, (150, 160))
        self.assertEqual(DATE_MONTH_W, (128, 150))
        self.assertEqual(COMBO_PICK_W, 200)

        de = QDateEdit()
        size_date(de, month=False)
        self.assertEqual(de.maximumHeight(), 28)
        self.assertEqual(de.minimumHeight(), 28)
        self.assertGreaterEqual(de.minimumWidth(), 150)
        self.assertLessEqual(de.maximumWidth(), 160)

        size_date(de, month=True)
        self.assertEqual(de.maximumHeight(), 28)
        self.assertEqual(de.minimumHeight(), 28)
        self.assertGreaterEqual(de.minimumWidth(), 128)
        self.assertLessEqual(de.maximumWidth(), 150)
        de.deleteLater()

        combo = QComboBox()
        size_pick_combo(combo)
        self.assertEqual(combo.maximumHeight(), 28)
        self.assertEqual(combo.minimumHeight(), 28)
        self.assertEqual(combo.minimumWidth(), 200)
        self.assertEqual(combo.maximumWidth(), 200)
        combo.deleteLater()

    def test_12_button_role_properties(self):
        """12. BUTTON_ROLE: actionRole 动态属性规范保留与映射。"""
        from ui.design_system import apply_button

        btn = QPushButton()
        for role, exp_obj_name in (
            ('primary', 'primary-btn'),
            ('secondary', 'btn-secondary'),
            ('danger', 'btn-danger'),
            ('ghost', 'btn-ghost'),
            ('fold', 'fold-action-btn'),
        ):
            apply_button(btn, role=role)
            self.assertEqual(btn.property('actionRole'), role)
            self.assertEqual(btn.objectName(), exp_obj_name)
        btn.deleteLater()

    def test_13_button_compact(self):
        """13. BUTTON_COMPACT: compact=True 设置 compactAction=True 并固定 28px。"""
        from ui.design_system import apply_button

        btn = QPushButton()
        apply_button(btn, role='secondary', compact=True)
        self.assertTrue(btn.property('compactAction'))
        self.assertEqual(btn.maximumHeight(), 28)
        self.assertEqual(btn.minimumHeight(), 28)
        btn.deleteLater()

    def test_14_button_normal(self):
        """14. BUTTON_NORMAL: compact=False 设置 compactAction=False 并固定 32px。"""
        from ui.design_system import apply_button

        btn = QPushButton()
        apply_button(btn, role='secondary', compact=False)
        self.assertFalse(btn.property('compactAction'))
        self.assertEqual(btn.maximumHeight(), 32)
        self.assertEqual(btn.minimumHeight(), 32)
        btn.deleteLater()

    def test_15_surface_role_properties(self):
        """15. SURFACE_ROLE: surfaceRole 动态属性与对象名兼容规范。"""
        from ui.design_system import apply_surface

        frame = QFrame()
        expected = {
            'card': 'ds-card',
            'zone': 'ds-zone',
            'muted': 'ds-muted',
            'glass': 'ds-glass',
            'elevated': 'ds-elevated',
            'tech': 'ds-tech',
        }
        for kind, obj_name in expected.items():
            apply_surface(frame, kind)
            self.assertEqual(frame.property('surfaceRole'), kind)
            self.assertEqual(frame.objectName(), obj_name)
        frame.deleteLater()

    def test_16_object_name_compatibility_in_qss(self):
        """16. OBJECT_NAME_COMPAT: QSS 包含属性选择器且向后兼容老 objectName 选择器。"""
        from ui.theme_manager import ThemeManager

        tm = ThemeManager.instance()
        tm.load_template()
        qss = tm.render('calm')

        # 按钮属性与兼容选择器
        self.assertIn('QPushButton[actionRole="primary"]', qss)
        self.assertIn('QPushButton[actionRole="secondary"]', qss)
        self.assertIn('QPushButton[actionRole="danger"]', qss)
        self.assertIn('QPushButton[actionRole="ghost"]', qss)
        self.assertIn('QPushButton[actionRole="fold"]', qss)
        self.assertIn('QPushButton[compactAction="true"]', qss)
        self.assertIn('QPushButton[compactAction="false"]', qss)

        self.assertIn('QPushButton#primary-btn', qss)
        self.assertIn('QPushButton#btn-secondary', qss)
        self.assertIn('QPushButton#btn-danger', qss)
        self.assertIn('QPushButton#btn-ghost', qss)
        self.assertIn('QPushButton#fold-action-btn', qss)

        # 表面属性与兼容选择器
        self.assertIn('QFrame[surfaceRole="card"]', qss)
        self.assertIn('QFrame[surfaceRole="zone"]', qss)
        self.assertIn('QFrame[surfaceRole="muted"]', qss)
        self.assertIn('QFrame[surfaceRole="glass"]', qss)
        self.assertIn('QFrame[surfaceRole="elevated"]', qss)
        self.assertIn('QFrame[surfaceRole="tech"]', qss)

        self.assertIn('QFrame#ds-card', qss)
        self.assertIn('QFrame#ds-zone', qss)
        self.assertIn('QFrame#ds-muted', qss)
        self.assertIn('QFrame#ds-glass', qss)
        self.assertIn('QFrame#ds-elevated', qss)
        self.assertIn('QFrame#ds-tech', qss)

    @staticmethod
    def _extract_css_block(qss: str, selector_pattern: str) -> str:
        """从 QSS 提取指定选择器对应的规则块内容。"""
        pattern = re.compile(selector_pattern + r'\s*\{([^}]+)\}', re.MULTILINE | re.DOTALL)
        match = pattern.search(qss)
        return match.group(1).strip() if match else ''

    def test_17_focus_contract_and_selector_blocks(self):
        """17. FOCUS_CONTRACT: 验证具体控件选择器块均具备 2px solid FOCUS_RING，绝非仅字串匹配。"""
        from ui.theme_manager import THEMES, ThemeManager

        tm = ThemeManager.instance()
        tm.load_template()

        for tid in ('calm', 'black'):
            qss = tm.render(tid)
            focus_ring = THEMES[tid]['FOCUS_RING']

            # 1. 按钮基础 focus
            btn_block = self._extract_css_block(qss, r'QPushButton:focus')
            self.assertTrue(btn_block, f'{tid} 缺少 QPushButton:focus 规则块')
            self.assertIn('2px solid', btn_block)
            self.assertIn(focus_ring, btn_block)

            # 2. 单行与下拉控件 focus 块 (QLineEdit, QComboBox, QDateEdit, QTimeEdit, etc.)
            input_block = self._extract_css_block(
                qss,
                r'QComboBox:focus,\s*QLineEdit:focus,\s*QDateEdit:focus,\s*QTimeEdit:focus,\s*QTextEdit:focus,\s*QPlainTextEdit:focus',
            )
            self.assertTrue(input_block, f'{tid} 缺少输入控件通用 focus 规则块')
            self.assertIn('2px solid', input_block)
            self.assertIn(focus_ring, input_block)

            # 3. 多行编辑区通用 focus 块
            multiline_block = self._extract_css_block(qss, r'QPlainTextEdit:focus,\s*QTextEdit:focus')
            self.assertTrue(multiline_block, f'{tid} 缺少多行编辑区 focus 规则块')
            self.assertIn('2px solid', multiline_block)
            self.assertIn(focus_ring, multiline_block)

            # 4. 可编辑表单字段专用 focus 块：必须是 2px FOCUS_RING，不得回退为 1px
            editable_block = self._extract_css_block(
                qss,
                r'QLineEdit\[editableField="true"\]:focus,\s*QPlainTextEdit\[editableField="true"\]:focus,\s*QTextEdit\[editableField="true"\]:focus',
            )
            self.assertTrue(editable_block, f'{tid} 缺少 editableField focus 规则块')
            self.assertIn('2px solid', editable_block)
            self.assertIn(focus_ring, editable_block)

            # 5. 只读字段专用 focus 块：保持弱化，不冒充激活的 2px focus ring
            readonly_block = self._extract_css_block(
                qss,
                r'QLineEdit\[readOnlyField="true"\]:focus,\s*QPlainTextEdit\[readOnlyField="true"\]:focus,\s*QTextEdit\[readOnlyField="true"\]:focus',
            )
            self.assertTrue(readonly_block, f'{tid} 缺少 readOnlyField focus 规则块')
            self.assertNotIn('2px solid', readonly_block)

            # 6. 全局兜底焦点规则块
            global_focus_block = self._extract_css_block(
                qss,
                r'QPushButton:focus,\s*QLineEdit:focus,\s*QComboBox:focus,\s*QTextEdit:focus,\s*QPlainTextEdit:focus,\s*QDateEdit:focus,\s*QTimeEdit:focus,\s*QSpinBox:focus',
            )
            self.assertTrue(global_focus_block, f'{tid} 缺少全局 focus 规则块')
            self.assertIn('2px solid', global_focus_block)
            self.assertIn(focus_ring, global_focus_block)

            # 7. 禁用态验证：具备可见文本与背景对比度
            self.assertIn('QPushButton:disabled', qss)
            self.assertIn('QPushButton#primary-btn:disabled', qss)
            self.assertIn(THEMES[tid]['DISABLED_TEXT'], qss)
            self.assertIn(THEMES[tid]['DISABLED_BG'], qss)

        # 实例化控件应用 QSS 运行冒烟测试
        btn = QPushButton('Test Action')
        le = QLineEdit('Input Value')
        cb = QComboBox()
        cb.addItem('Choice 1')
        calm_rendered = tm.render('calm')
        btn.setStyleSheet(calm_rendered)
        le.setStyleSheet(calm_rendered)
        cb.setStyleSheet(calm_rendered)
        self.assertIsNotNone(btn.style())
        self.assertIsNotNone(le.style())
        self.assertIsNotNone(cb.style())
        self.assertIsNotNone(btn.palette())
        self.assertIsNotNone(le.palette())
        btn.deleteLater()
        le.deleteLater()
        cb.deleteLater()

    def test_18_web_theme_parity(self):
        """18. WEB_THEME_TOKEN_PARITY: Web bridge payload 与 Python theme tokens 完全对齐。"""
        from ui.theme_manager import THEMES, ThemeManager
        from ui import web_shell

        tm = ThemeManager.instance()
        tm.load_template()

        for tid in ('calm', 'black'):
            pal = tm.palette(tid)
            self.assertEqual(pal['PRIMARY'], THEMES[tid]['PRIMARY'])
            self.assertEqual(pal['APP_BG'], THEMES[tid]['APP_BG'])
            self.assertEqual(pal['SURFACE'], THEMES[tid]['SURFACE'])

        if getattr(web_shell, 'WEB_SHELL_AVAILABLE', False):
            bridge = web_shell.HomeBridge()
            for tid, is_dark in (('calm', False), ('black', True)):
                payload = {
                    'id': tid,
                    'is_dark': is_dark,
                    'tokens': tm.palette(tid),
                }
                bridge.set_theme_payload(payload)
                raw = bridge.themePayload()
                decoded = json.loads(raw)
                self.assertEqual(decoded['id'], tid)
                self.assertEqual(decoded['is_dark'], is_dark)
                self.assertEqual(decoded['tokens']['PRIMARY'], THEMES[tid]['PRIMARY'])
                self.assertEqual(decoded['tokens']['SURFACE'], THEMES[tid]['SURFACE'])


if __name__ == '__main__':
    unittest.main()
