# -*- coding: utf-8 -*-
"""页面骨架自检（页面骨架与控件分层规范 v1 §9）。

覆盖三个硬约束：
1. 每个 Stack 页面必须有 L1 页头（#page-header）；
2. 每页面板树内 primary 主操作按钮数量 ≤1（Dialog 内不计）；
3. #page-filter-bar 内不允许出现改数据的按钮（已知违例走基线表，批次 2 迁移后清零）。
"""
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QPushButton, QTabWidget  # noqa: E402

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
    (16, '聊天'),
    (17, '工作'),
    (18, 'Oracle'),
    (19, 'MySQL'),
    (20, 'OceanBase'),
    (21, '达梦'),
    (22, 'Redis'),
    (23, 'MongoDB'),
]

# #page-filter-bar 内 QPushButton 基线（批次 2：需求管理工具栏已迁至 #page-toolbar，筛选条应为 0）
FILTER_BAR_BUTTON_BASELINE = {}

_WINDOW_CACHE = {}


def _window():
    app = QApplication.instance() or QApplication([])
    window = _WINDOW_CACHE.get('window')
    if window is None:
        os.environ['PENGTOOLS_SYNC_BOOT'] = '1'
        from main_window import MainWindow
        from config import DEFAULT_SETTINGS
        settings = dict(DEFAULT_SETTINGS)
        settings['ui_web_shell'] = False
        with patch('main_window.load_settings', return_value=settings):
            window = MainWindow()
        _WINDOW_CACHE['window'] = window
    return window


def tearDownModule():
    window = _WINDOW_CACHE.pop('window', None)
    if window is None:
        return
    try:
        window._force_exit = True
        window.close()
        window.deleteLater()
        from PyQt6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
    except RuntimeError:
        pass


def _panel_for(nav_index: int):
    window = _window()
    window._show_panel(nav_index)
    QApplication.processEvents()
    return window.stack.currentWidget()


def _primary_buttons(panel) -> list:
    """面板树内 #primary-btn；嵌套 QDialog 内的按钮不计入。"""
    result = []
    for btn in panel.findChildren(QPushButton):
        if btn.objectName() != 'primary-btn':
            continue
        parent = btn.parentWidget()
        inside_dialog = False
        while parent is not None and parent is not panel:
            if isinstance(parent, QDialog):
                inside_dialog = True
                break
            parent = parent.parentWidget()
        if not inside_dialog:
            result.append(btn)
    return result


class PageHeaderTests(unittest.TestCase):
    """约束 1：每页必有 L1 页头。"""

    def test_complete_pages_have_header(self):
        problems = []
        for nav, label in STACK_PAGES:
            panel = _panel_for(nav)
            header = panel.findChild(QFrame, 'page-header') or panel.findChild(QFrame, 'page-toolbar')
            if header is None:
                problems.append(label)
        self.assertEqual(problems, [], f'以下页面缺少 L1 页头: {problems}')

    def test_h1_ops_log_header_title_and_subtitle_non_empty(self):
        """H1: 日志排查页标题与副标题非空。"""
        panel = _panel_for(13)
        self.assertTrue(hasattr(panel, 'title_label'))
        self.assertEqual(panel.title_label.text(), '日志排查')
        self.assertFalse(panel.title_label.isHidden())
        self.assertIn('SSH 会话', panel.subtitle_label.text())

    def test_h2_page_header_title_audit_all_pages_non_empty(self):
        """H2: 全页面页头审计：所有主页面的 page-title 均不得为空字符串。"""
        empty_titles = []
        for nav, label in STACK_PAGES:
            panel = _panel_for(nav)
            header = panel.findChild(QFrame, 'page-header')
            if header is not None:
                title_lbl = header.findChild(QLabel, 'page-title')
                if title_lbl is not None and not title_lbl.text().strip():
                    empty_titles.append(f'{label}(nav={nav})')
        self.assertEqual(empty_titles, [], f'以下页面页头标题为空: {empty_titles}')

    def test_f1_f2_f3_font_size_control_layout(self):
        """F1-F3: 设置页字体步进器无内部后缀，外部独立 px 标签，且无几何重叠。"""
        window = _window()
        panel = _panel_for(7)
        self.assertTrue(hasattr(panel, 'font_size'))
        self.assertTrue(hasattr(panel, 'font_unit_label'))
        # F1: Stepper 内部无 suffix
        self.assertEqual(panel.font_size.suffix_label.text(), '')
        self.assertFalse(panel.font_size.suffix_label.isVisible())
        # F2: 外部独立 px 标签
        self.assertEqual(panel.font_unit_label.text(), 'px')
        # F3: 几何布局不重叠且有间距
        window.show()
        QApplication.processEvents()
        stepper_geom = panel.font_size.geometry()
        label_geom = panel.font_unit_label.geometry()
        self.assertGreater(label_geom.left(), stepper_geom.right())
        self.assertGreaterEqual(label_geom.left() - stepper_geom.right(), 4)


class PrimaryActionTests(unittest.TestCase):
    """约束 2：每页主操作 ≤1（硬断言，无基线豁免；多 Tab 容器页按 Tab 页分别约束）。"""

    def test_primary_count_at_most_one(self):
        problems = []
        for nav, label in STACK_PAGES:
            panel = _panel_for(nav)
            tab_widget = panel.findChild(QTabWidget)
            if tab_widget is not None and tab_widget.count() > 1:
                for idx in range(tab_widget.count()):
                    tab_page = tab_widget.widget(idx)
                    actual = len(_primary_buttons(tab_page))
                    if actual > 1:
                        names = [b.text() or b.objectName() for b in _primary_buttons(tab_page)]
                        problems.append(f'{label}[Tab{idx}](nav={nav}): {actual} > 1 → {names}')
            else:
                actual = len(_primary_buttons(panel))
                if actual > 1:
                    names = [b.text() or b.objectName() for b in _primary_buttons(panel)]
                    problems.append(f'{label}(nav={nav}): {actual} > 1 → {names}')
        self.assertEqual(problems, [], f'primary 按钮超过 1 个: {problems}')


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


class PageHeaderDividerTests(unittest.TestCase):
    """V1-9 & V1-10: PageHeader API 兼容性与底部分割线测试。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_v1_9_page_header_api_and_divider_component(self):
        """V1-9: make_page_header() 返回 tuple/API 不变，有底部分割线实现，不增加定时器。"""
        from PyQt6.QtCore import QTimer
        from ui.page_chrome import make_page_header

        ret = make_page_header('测试页面', subtitle='副标题', icon_role='settings')
        self.assertIsInstance(ret, tuple)
        self.assertEqual(len(ret), 3)
        frame, title, subtitle = ret
        self.assertIsInstance(frame, QFrame)
        self.assertIsInstance(title, QLabel)
        self.assertIsInstance(subtitle, QLabel)
        self.assertEqual(frame.objectName(), 'page-header')
        # 验证分割线标记与无 timer
        self.assertTrue(getattr(frame, '_has_bottom_divider', False) or hasattr(frame, 'has_bottom_divider'))
        self.assertEqual(len(frame.findChildren(QTimer)), 0)
        frame.deleteLater()

    def test_v1_9_real_render_offscreen_regression(self):
        """V1-9 Real Render: host.show() + frame.show() + grab() 验证真实 paintEvent 执行。"""
        from PyQt6.QtWidgets import QWidget
        from ui.page_chrome import make_page_header

        host = QWidget()
        host.resize(800, 200)
        frame, title, subtitle = make_page_header('测试渲染', subtitle='副标题')
        frame.setParent(host)
        frame.resize(800, 60)
        host.show()
        frame.show()
        self.app.processEvents()

        pix = frame.grab()
        self.assertFalse(pix.isNull(), '渲染 grab 图像不应为空')
        self.assertGreater(pix.width(), 0)
        self.assertGreater(pix.height(), 0)
        self.assertGreater(getattr(frame, '_paint_count', 0), 0, 'paintEvent 应当实际执行')

        host.close()
        host.deleteLater()

    def test_v1_10_page_header_viewport_geometry_not_increased(self):
        """V1-10: 页面头部高度不因 divider 显著增加。"""
        from ui.layout_metrics import PAGE_HEADER_H
        from ui.page_chrome import make_page_header

        frame, _, _ = make_page_header('测试', subtitle='')
        self.assertLessEqual(frame.minimumHeight(), PAGE_HEADER_H)
        self.assertGreaterEqual(frame.minimumHeight(), PAGE_HEADER_H - 16)
        frame.deleteLater()


if __name__ == '__main__':
    unittest.main()
