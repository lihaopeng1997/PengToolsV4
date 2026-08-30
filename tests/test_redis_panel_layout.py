# -*- coding: utf-8 -*-
"""Redis 工作区布局回归（Step 4B）：主业务区不得塌陷。

历史缺陷：body/bottom 先加入 root 布局再 reparent 进 QSplitter，
root.replaceWidget(body, ...) 对已 reparent 的子项不可靠，导致 header/
toolbar 异常瓜分整屏高度、Key 树/详情/命令行不可见（大面积空白）。
本测试在典型窗口尺寸下校验真实几何，防止主区再次塌陷。
"""
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from PyQt6.QtWidgets import QApplication
    QT_AVAILABLE = True
except Exception:  # pragma: no cover
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class RedisPanelLayoutGeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_panel(self):
        from panels.db_redis_panel import RedisWorkbenchPanel
        return RedisWorkbenchPanel('zh')

    def test_workbench_geometry_at_typical_size(self):
        """1600x900 下 toolbar/主分隔/Key 树/详情页签/命令输出全部真实可见。"""
        panel = self._make_panel()
        panel.resize(1600, 900)
        panel.show()
        try:
            self.app.processEvents()
            self.assertGreater(panel.toolbar.height(), 0, '连接 toolbar 必须在顶部且可见')
            self.assertGreater(panel._bottom_split.height(), 300, '主业务分隔高度塌陷')
            body = panel._bottom_split.widget(0)
            self.assertIsNotNone(body, '_bottom_split 主区（body）缺失')
            self.assertGreater(body.height(), 200, 'body 主区高度塌陷')
            self.assertGreater(panel.key_tree.width(), 0, 'Key 树宽度为 0')
            self.assertGreater(panel.side_tabs.width(), 0, 'Key 详情/AI 助手页签宽度为 0')
            self.assertGreater(panel.cmd_output.height(), 0, '命令输出区高度塌陷')
        finally:
            panel.close()

    def test_no_reparent_replacewidget_pattern(self):
        """源码守护：禁止 reparent 后 replaceWidget 的断链构造与冗余 header。"""
        with open(os.path.join(ROOT, 'panels', 'db_redis_panel.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertNotIn('root.replaceWidget(', src, '禁止 replaceWidget 布局断链构造')
        self.assertNotIn('make_page_header', src, 'Redis 内容区不应再有视觉 header')
        self.assertIn('root.addWidget(self._bottom_split, 1)', src,
                      '_bottom_split 必须直接作为 root 主 stretch 内容')


if __name__ == '__main__':
    unittest.main()
