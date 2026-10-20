# -*- coding: utf-8 -*-
"""需求管理 V3：视角、状态分组、状态统计筛选与就地改状态。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 2


def _req(rid, status, month='2026-10', kind='需求'):
    return {
        'id': rid, 'code': rid.upper(), 'title': f'需求 {rid}', 'status': status,
        'record_kind': kind, 'online_month': month, 'sql_parts': [], 'source_files': [],
    }


class RequirementViewHelpersTests(unittest.TestCase):
    def test_status_groups_and_stage_index(self):
        from panels.requirement_views import stage_index, status_group
        self.assertEqual(status_group('开发中'), 'develop')
        self.assertEqual(status_group('集成测试'), 'testing')
        self.assertEqual(status_group('已上线'), 'online')
        self.assertEqual(status_group('暂停'), 'paused')
        self.assertEqual(status_group(''), 'analysis')
        self.assertEqual(status_group('未知状态'), 'analysis')
        self.assertEqual(stage_index('待分析'), 0)
        self.assertEqual(stage_index('用户测试'), 2)
        self.assertEqual(stage_index('暂停'), -1)

    def test_every_status_belongs_to_a_group(self):
        from panels.requirement_views import STATUS_GROUP_ORDER, known_statuses, status_group
        for status in known_statuses():
            self.assertIn(status_group(status), STATUS_GROUP_ORDER, status)


class RequirementPanelViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make(self, rows, view=None):
        from panels.requirement_panel import RequirementPanel
        patches = [
            patch('panels.requirement_panel.load_requirements', return_value=rows),
            patch('panels.requirement_panel.load_last_choices', return_value={'requirement_view': view or {}}),
            patch('panels.requirement_panel.update_last_choices'),
            patch('panels.requirement_panel.load_requirement_ui', return_value={}),
            patch('panels.requirement_panel.save_requirement_ui'),
            patch('panels.requirement_panel.RequirementPanel._refresh_file_tree', lambda self: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.saved = []
        save = patch('panels.requirement_panel.save_requirements', side_effect=lambda rows, *a, **k: self.saved.append([dict(r) for r in rows]))
        save.start()
        self.addCleanup(save.stop)
        panel = RequirementPanel('zh')
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.close)
        return panel

    def _groups(self, panel):
        tree = panel.requirement_list
        return {tree.topLevelItem(i).data(0, GROUP_ROLE): tree.topLevelItem(i) for i in range(tree.topLevelItemCount())}

    def test_default_groups_by_status_and_collapses_history(self):
        panel = self._make([_req('a', '开发中'), _req('b', '集成测试'), _req('c', '已上线')])
        groups = self._groups(panel)
        self.assertEqual(list(groups), ['status:develop', 'status:testing', 'status:online'])
        self.assertTrue(groups['status:develop'].isExpanded())
        self.assertFalse(groups['status:online'].isExpanded())
        row = groups['status:develop'].child(0)
        self.assertEqual([row.text(i) for i in range(3)], ['A', '需求 a', '开发中'])

    def test_status_chip_filters_and_toggles_off(self):
        panel = self._make([_req('a', '开发中'), _req('b', '集成测试'), _req('c', '用户测试')])
        self.assertIn('testing', panel._status_chips)
        self.assertEqual(panel._status_chips['testing'].text(), '测试中 2')
        panel._status_chips['testing'].click()
        self.assertEqual(list(self._groups(panel)), ['status:testing'])
        panel._status_chips['testing'].click()
        self.assertEqual(len(self._groups(panel)), 2)

    def test_unified_quick_actions_and_tab_order(self):
        panel = self._make([_req('a', '开发中'), _req('b', '集成测试')])
        self.assertFalse(hasattr(panel, 'role_switch'))
        self.assertEqual(
            list(panel.quick_buttons),
            ['open_dev_btn', 'quick_update_btn', 'quick_commit_btn', 'daily_btn'],
        )
        for button in panel.quick_buttons.values():
            self.assertFalse(button.isHidden())
        self.assertFalse(panel.ticket_btn.isHidden())
        self.assertEqual(
            [panel.detail_tabs.tabText(i) for i in range(panel.detail_tabs.count())],
            ['文件库', '测试点', 'SQL / 联动'],
        )
        self.assertEqual(len(self._groups(panel)), 2)

    def test_status_combo_saves_and_regroups(self):
        panel = self._make([_req('a', '开发中'), _req('b', '待测试')])
        panel.focus_requirement('a')
        panel.status_combo.setCurrentText('集成测试')
        self.assertTrue(self.saved)
        saved = {row['id']: row['status'] for row in self.saved[-1]}
        self.assertEqual(saved['a'], '集成测试')
        self.assertEqual(list(self._groups(panel)), ['status:testing'])
        self.assertEqual(panel._current['id'], 'a')

    def test_drop_on_status_group_keeps_finer_status_inside_group(self):
        panel = self._make([_req('a', '开发中'), _req('b', '集成测试')])
        panel._move_requirements(['a', 'b'], 'status:testing')
        saved = {row['id']: row['status'] for row in self.saved[-1]}
        self.assertEqual(saved, {'a': '待测试', 'b': '集成测试'})

    def test_month_filter_and_month_grouping(self):
        panel = self._make([_req('a', '开发中', '2026-10'), _req('b', '开发中', '2026-09'), _req('c', '开发中', '')])
        months = [panel.month_filter.itemData(i) for i in range(panel.month_filter.count())]
        self.assertEqual(months, ['', '2026-10', '2026-09', '__none__'])
        panel.month_filter.setCurrentIndex(months.index('2026-09'))
        group = self._groups(panel)['status:develop']
        self.assertEqual([group.child(i).text(0) for i in range(group.childCount())], ['B'])
        panel.month_filter.setCurrentIndex(0)
        panel._set_group_mode('month')
        self.assertEqual(set(self._groups(panel)), {'2026-10', '2026-09', ''})

    def test_selection_bar_only_for_multi_select(self):
        panel = self._make([_req('a', '开发中'), _req('b', '开发中')])
        panel.show()
        group = self._groups(panel)['status:develop']
        group.child(0).setSelected(True)
        self.assertFalse(panel.selection_bar.isVisible())
        group.child(1).setSelected(True)
        self.assertTrue(panel.selection_bar.isVisible())
        self.assertEqual(panel.batch_delete_btn.text(), '删除(2)')


if __name__ == '__main__':
    unittest.main()
