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
        """1600x900 下 toolbar/主分隔/Key 树/详情页签/命令输出全部真实可见且符合层次架构。"""
        panel = self._make_panel()
        panel.resize(1600, 900)
        panel.show()
        try:
            self.app.processEvents()
            self.assertGreater(panel.toolbar.height(), 0, '连接 toolbar 必须在顶部且可见')

            # 1. 层次结构断言：main_split 包含左侧全高容器与右侧上下区域
            left_container = panel.main_split.widget(0)
            self.assertEqual(panel.main_split.indexOf(left_container), 0, 'main_split 索引 0 必须是左侧浏览器')
            self.assertEqual(panel.main_split.indexOf(panel._bottom_split), 1, 'main_split 索引 1 必须是右侧区域')

            # 2. 控制台归位断言：Console 仅归属右侧下半部分，绝不横跨左侧
            self.assertEqual(panel._bottom_split.indexOf(panel.side_tabs), 0, '右侧上半部分必须是详情 side_tabs')
            self.assertEqual(panel._bottom_split.indexOf(panel.bottom_frame), 1, '右侧下半部分必须是控制台 bottom_frame')

            # 3. 尺寸断言：左侧容器宽度在 1600x900 下必须达到至少 360px
            self.assertGreaterEqual(left_container.width(), 360, f'左侧浏览器宽度偏窄: {left_container.width()} < 360')

            # 4. 左侧内部高度断言：Key 树与 Key 列表均有充足高度
            self.assertGreaterEqual(panel.key_tree.height(), 100, f'Key 树高度塌陷: {panel.key_tree.height()}')
            self.assertGreaterEqual(panel.key_list.height(), 100, f'Key 列表高度塌陷: {panel.key_list.height()}')

            # 5. 右侧详情与控制台几何断言
            self.assertGreater(panel.side_tabs.width(), 0, 'Key 详情/AI 助手页签宽度为 0')
            self.assertGreater(panel.cmd_output.height(), 0, '命令输出区高度塌陷')
            self.assertLessEqual(panel.cmd_output.width(), panel._bottom_split.width(), '控制台宽度不得越界覆盖左侧')
        finally:
            panel.close()

    def test_no_reparent_replacewidget_pattern(self):
        """源码守护：禁止 reparent 后 replaceWidget 的断链构造与冗余 header。"""
        with open(os.path.join(ROOT, 'panels', 'db_redis_panel.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertNotIn('root.replaceWidget(', src, '禁止 replaceWidget 布局断链构造')
        self.assertNotIn('make_page_header', src, 'Redis 内容区不应再有视觉 header')
        self.assertIn('root.addWidget(self.main_split, 1)', src,
                      'main_split 必须直接作为 root 主 stretch 内容')


if __name__ == '__main__':
    unittest.main()
