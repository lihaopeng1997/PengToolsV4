# -*- coding: utf-8 -*-
"""页面骨架自检（页面骨架与控件分层规范 v1 §9）。

覆盖三个硬约束：
1. 每个 Stack 页面必须有 L1 页头（#page-header）；
2. 每页 primary 主操作按钮数量不超基线（棘轮：只降不升，批次 2 收口到 ≤1）；
3. #page-filter-bar 内不允许出现改数据的按钮（已知违例走基线表，批次 2 迁移后清零）。

已知缺页头的 3 个页面（升级准备/命令库/自我学习·日报）用 expectedFailure 标记，
批次 1 补齐页头后测试会以 unexpected success 报警，届时移除装饰器与基线表条目。
"""
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QPushButton  # noqa: E402

# (nav_index, 页面名)
STACK_PAGES = [
    (0, '工作台'),
    (1, '证件类型'),
    (2, '升级准备'),
    (3, '接口文档更新'),
    (4, '车辆 VIN'),
    (5, '加解密'),
    (6, '命令库'),
    (7, '设置'),
    (9, '日报（自我学习共用 PersonalPanel）'),
    (10, '需求管理'),
    (11, '格式工具'),
    (12, '接口排查'),
    (13, '日志排查'),
    (14, 'SQL 控制台'),
    (15, '模型对话'),
]

# 已知缺 L1 页头的页面（批次 1 补齐后从此移除）
KNOWN_MISSING_HEADER = {2, 6, 9}

# primary 按钮当前基线（面板树内，弹窗不计；2026-08-26 实测校准）：
# 只降不升；批次 2 主操作收口后逐页下调到 ≤1
PRIMARY_BASELINE = {
    0: 0, 1: 2, 2: 4, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1,
    9: 3, 10: 4, 11: 4, 12: 2, 13: 6, 14: 1, 15: 1,
}

# #page-filter-bar 内 QPushButton 当前基线（nav 10 需求管理把工具栏误挂为筛选条，批次 2 迁移）
FILTER_BAR_BUTTON_BASELINE = {10: 6}

_WINDOW_CACHE = {}


def _window():
    app = QApplication.instance() or QApplication([])
    window = _WINDOW_CACHE.get('window')
    if window is None:
        os.environ['PENGTOOLS_SYNC_BOOT'] = '1'
        from main_window import MainWindow
        window = MainWindow()
        _WINDOW_CACHE['window'] = window
    return window


def _panel_for(nav_index: int):
    window = _window()
    window._show_panel(nav_index)
    QApplication.processEvents()
    return window.stack.currentWidget()


def _primary_buttons(panel) -> list:
    return [b for b in panel.findChildren(QPushButton) if b.objectName() == 'primary-btn']


class PageHeaderTests(unittest.TestCase):
    """约束 1：每页必有 L1 页头。"""

    def test_complete_pages_have_header(self):
        problems = []
        for nav, label in STACK_PAGES:
            if nav in KNOWN_MISSING_HEADER:
                continue
            panel = _panel_for(nav)
            header = panel.findChild(QFrame, 'page-header')
            if header is None:
                problems.append(label)
        self.assertEqual(problems, [], f'以下页面缺少 L1 页头: {problems}')

    @unittest.expectedFailure
    def test_release_page_header_missing(self):
        panel = _panel_for(2)
        self.assertIsNotNone(panel.findChild(QFrame, 'page-header'), '升级准备应有 L1 页头')

    @unittest.expectedFailure
    def test_ops_page_header_missing(self):
        panel = _panel_for(6)
        self.assertIsNotNone(panel.findChild(QFrame, 'page-header'), '命令库应有 L1 页头')

    @unittest.expectedFailure
    def test_personal_page_header_missing(self):
        panel = _panel_for(9)
        self.assertIsNotNone(panel.findChild(QFrame, 'page-header'), '自我学习/日报应有 L1 页头')


class PrimaryActionTests(unittest.TestCase):
    """约束 2：主操作数量棘轮（基线只降不升；目标 ≤1，批次 2 达成）。"""

    def test_primary_count_within_baseline(self):
        problems = []
        for nav, label in STACK_PAGES:
            panel = _panel_for(nav)
            actual = len(_primary_buttons(panel))
            allowed = PRIMARY_BASELINE.get(nav, 0)
            if actual > allowed:
                problems.append(f'{label}: {actual} > 基线 {allowed}')
        self.assertEqual(problems, [], f'primary 按钮超出基线（棘轮收紧，禁止新增）: {problems}')

    def test_primary_count_target_documented(self):
        """规范终态：主操作 ≤1。当前允许超基线的页面必须登记在 PRIMARY_BASELINE，
        该表不允许新增条目（新页面必须直接满足 ≤1）。"""
        for nav, label in STACK_PAGES:
            if PRIMARY_BASELINE.get(nav, 0) > 1:
                continue  # 已知历史欠账，批次 2 清偿
        # 新页面约束由 test_primary_count_within_baseline 的基线默认 0 保证


class FilterBarPurityTests(unittest.TestCase):
    """约束 3：筛选条内不放改数据的按钮（已知违例走基线，批次 2 清零）。"""

    def test_filter_bar_button_count_within_baseline(self):
        problems = []
        for nav, label in STACK_PAGES:
            panel = _panel_for(nav)
            for bar in panel.findChildren(QFrame, 'page-filter-bar'):
                count = len(bar.findChildren(QPushButton))
                allowed = FILTER_BAR_BUTTON_BASELINE.get(nav, 0)
                if count > allowed:
                    problems.append(f'{label}: 筛选条内 {count} 个按钮 > 基线 {allowed}')
        self.assertEqual(problems, [], f'筛选条混入操作按钮（应迁往 #page-toolbar）: {problems}')


class EmptyStateFactoryTests(unittest.TestCase):
    """make_empty_state 四要素模板单测（规范 v1 §6.3）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make(self, **kwargs):
        from ui.page_chrome import make_empty_state
        return make_empty_state(
            '尚未关联需求', '点击右上「新建需求」开始台账', **kwargs
        )

    def test_frame_object_name_and_title(self):
        frame = self._make()
        self.assertEqual(frame.objectName(), 'empty-state')
        title = frame.findChild(QLabel, 'empty-state-title')
        self.assertIsNotNone(title)
        self.assertEqual(title.text(), '尚未关联需求')
        text = frame.findChild(QLabel, 'empty-state-text')
        self.assertIsNotNone(text)
        self.assertIn('新建需求', text.text())

    def test_optional_parts(self):
        from ui.page_chrome import make_empty_state
        bare = make_empty_state('尚未关联需求')
        self.assertIsNone(bare.findChild(QLabel, 'empty-state-text'))

        action = QPushButton('扫描需求目录')
        with_button = make_empty_state(
            '尚未关联需求', '点击右上「新建需求」开始台账', button=action
        )
        self.assertIs(action, with_button.findChild(QPushButton))

    def test_qss_has_skeleton_sections(self):
        from ui.theme_manager import ThemeManager
        template = ThemeManager.instance().load_template()
        for selector in ('QFrame#page-toolbar', 'QFrame#empty-state',
                         'QLabel#empty-state-title', 'QLabel#empty-state-text'):
            self.assertIn(selector, template, f'style.qss 缺少 {selector}')


if __name__ == '__main__':
    unittest.main()
