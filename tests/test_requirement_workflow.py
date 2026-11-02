"""Custom status configuration and real native requirement controls."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from tools.requirement_workflow import (DEFAULT_ROWS, configured_stages, configured_statuses,
                                      save_workflow, validate_rows)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        config = patch('tools.requirement_workflow.workflow_path', return_value=self.root / 'workflow.json')
        config.start()
        self.addCleanup(config.stop)

    def test_roundtrip_order_and_semantic_groups(self):
        from tools.requirement_stages import status_group, stage_index
        from tools.requirements import is_online_status
        rows = [{'name': '评审中', 'stage': '评审', 'group': 'analysis'},
                {'name': '联调中', 'stage': '联调', 'group': 'testing'},
                {'name': '生产验证完成', 'stage': '发布完成', 'group': 'online'}]
        save_workflow(rows)
        self.assertEqual(configured_statuses(), ('评审中', '联调中', '生产验证完成'))
        self.assertEqual([s[1] for s in configured_stages()], ['评审', '联调', '发布完成'])
        self.assertEqual(status_group('联调中'), 'testing')
        from tools.requirement_workflow import workflow_stage_index
        self.assertEqual(workflow_stage_index('生产验证完成'), 2)
        self.assertEqual(stage_index('生产验证完成'), 4)  # Home keeps canonical business stages.
        self.assertTrue(is_online_status({'status': '生产验证完成'}))
        self.assertTrue(is_online_status({'status': '已上线'}))
        from tools.requirements import requirement_from_text
        self.assertEqual(requirement_from_text('合成测试', systems=[])['status'], '评审中')

    def test_invalid_edit_leaves_saved_configuration_intact(self):
        save_workflow(DEFAULT_ROWS)
        previous = (self.root / 'workflow.json').read_bytes()
        with self.assertRaises(ValueError):
            save_workflow([DEFAULT_ROWS[0], DEFAULT_ROWS[0]])
        self.assertEqual((self.root / 'workflow.json').read_bytes(), previous)
        with self.assertRaises(ValueError):
            validate_rows([{'name': 'foo', 'stage': 'same', 'group': 'analysis'},
                           {'name': 'bar', 'stage': 'same', 'group': 'online'}])

    def test_renamed_status_retains_historical_business_classification(self):
        from tools.requirement_stages import status_group
        save_workflow([{'name': '联调中', 'stage': '联调', 'group': 'testing'}])
        save_workflow([{'name': '联调验证', 'stage': '联调', 'group': 'testing'}])
        self.assertEqual(status_group('联调中'), 'testing')
        self.assertNotIn('联调中', configured_statuses())

    def test_legacy_configuration_migrates_without_writing_until_save(self):
        import json
        from tools.requirement_workflow import load_nodes, save_nodes, node_default_status
        path = self.root / 'workflow.json'
        path.write_text(json.dumps({'version': 1, 'statuses': DEFAULT_ROWS}), encoding='utf-8')
        before = path.read_bytes()
        self.assertEqual(node_default_status('开发'), '开发中')
        self.assertEqual(node_default_status('测试'), '集成测试')
        self.assertEqual(path.read_bytes(), before)
        statuses = configured_statuses()
        nodes = load_nodes()
        nodes[2]['default_status'] = '用户测试'
        save_nodes(nodes)
        self.assertEqual(configured_statuses(), statuses)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['version'], 2)
        self.assertEqual(node_default_status('测试'), '用户测试')

    def test_statuses_and_nodes_are_independent_and_validate_references(self):
        from tools.requirement_workflow import load_workflow, load_nodes, save_nodes, save_statuses
        statuses, nodes = load_workflow(), load_nodes()
        save_nodes(nodes[:-1])
        self.assertEqual(load_workflow(), statuses)
        remaining = load_nodes()
        statuses.append({'name': '评审通过', 'group': 'analysis'})
        save_statuses(statuses)
        self.assertEqual(load_nodes(), remaining)
        self.assertIn('评审通过', configured_statuses())
        with self.assertRaises(ValueError):
            save_statuses([s for s in statuses if s['name'] != '开发中'])
        self.assertEqual(load_nodes(), remaining)
        for status in statuses:
            if status['name'] == '开发中':
                status['name'] = '编码中'
        save_statuses(statuses, {'开发中': '编码中'})
        self.assertEqual(load_nodes()[1]['default_status'], '编码中')
        save_nodes([])
        self.assertIn('编码中', configured_statuses())
        self.assertEqual(configured_stages(), ())

    def test_legacy_condensed_nodes_restore_original_detailed_statuses_once(self):
        import json
        from tools.requirement_workflow import load_nodes, load_workflow, save_nodes, save_statuses
        short = [row for row in DEFAULT_ROWS if row['name'] in ('待分析', '开发中', '集成测试', '待上线', '暂停')]
        path = self.root / 'workflow.json'
        path.write_text(json.dumps({'version': 1, 'statuses': short}), encoding='utf-8')
        before = path.read_bytes()
        self.assertEqual(configured_statuses(), ('待分析', '待开发', '开发中', '待测试', '集成测试',
                                               '用户测试', '模拟测试', '待上线', '已上线', '暂停'))
        testing = next(node for node in load_nodes() if node['name'] == '测试')
        self.assertEqual(testing['statuses'], ['待测试', '集成测试', '用户测试', '模拟测试'])
        self.assertEqual(testing['default_status'], '集成测试')
        self.assertEqual(path.read_bytes(), before)
        # 新版独立配置中主动删除的状态，不会在下次读取时重新补回。
        nodes = load_nodes()
        for node in nodes:
            node['statuses'] = [s for s in node['statuses'] if s != '模拟测试']
        save_nodes(nodes)
        save_statuses([s for s in load_workflow() if s['name'] != '模拟测试'])
        self.assertNotIn('模拟测试', configured_statuses())


class WorkflowUiTests(WorkflowTests):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        # Theme checks must not leak global QApplication styling into other suites.
        self.addCleanup(self.app.setFont, self.app.font())
        self.addCleanup(self.app.setStyleSheet, self.app.styleSheet())

    def panel(self, status='待分析'):
        from panels.requirement_panel import RequirementPanel
        row = {'id': 'fixture', 'code': 'REQ-FIXTURE', 'title': '合成测试需求', 'status': status,
               'record_kind': '需求', 'actual_release_date': '2026-10-29', 'local_path': '', 'system': ''}
        self.saved = []
        for target, value in (
            ('panels.requirement_panel.load_requirements', lambda: [dict(row)]),
            ('panels.requirement_panel.load_systems', lambda: []),
            ('panels.requirement_panel.load_last_choices', lambda: {}),
            ('panels.requirement_panel.update_last_choices', lambda **kw: None),
            ('panels.requirement_panel.load_requirement_ui', lambda: {}),
            ('panels.requirement_panel.save_requirement_ui', lambda *a: None),
            ('panels.requirement_panel.save_requirements', lambda items: self.saved.append(items)),
            ('panels.requirement_panel.RequirementPanel._refresh_file_tree', lambda *_: None),
            ('config.load_layout_splitter', lambda *a, **k: None),
            ('config.save_layout_splitter', lambda *a, **k: None),
        ):
            mock = patch(target, side_effect=value)
            mock.start()
            self.addCleanup(mock.stop)
        panel = RequirementPanel()
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.close)
        self.panel_widget = panel
        return panel

    def test_node_click_saves_through_existing_status_path(self):
        from PyQt6.QtCore import Qt, QPoint
        from PyQt6.QtTest import QTest
        panel = self.panel()
        panel.resize(1400, 850)
        panel.show()
        self.app.processEvents()
        panel.stage_bar.set_status('待分析')
        QTest.mouseClick(panel.stage_bar, Qt.MouseButton.LeftButton,
                         pos=QPoint(panel.stage_bar.width() - 15, 13))
        self.assertEqual(self.saved[-1][0]['status'], '已上线')
        self.assertEqual(panel.status_combo.currentText(), '已上线')

    def test_custom_status_and_legacy_status_preserved(self):
        save_workflow([{'name': '联调中', 'stage': '联调', 'group': 'testing'}])
        panel = self.panel('历史自定义状态')
        self.assertEqual(panel.status_combo.currentText(), '历史自定义状态')
        panel.status_combo.setCurrentText('联调中')
        self.assertEqual(self.saved[-1][0]['status'], '联调中')
        self.assertEqual(panel.stage_bar._index, 0)

    def test_editors_share_status_choices_and_refresh_panel_on_save(self):
        from ui.requirement_workflow_editor import WorkflowEditor, StatusEditor
        from tools.requirement_workflow import load_nodes, load_workflow
        panel = self.panel()
        status_editor = StatusEditor()
        self.addCleanup(status_editor.deleteLater)
        before = load_nodes()
        status_editor.add_row({'name': '回归验证', 'group': 'testing'})
        status_editor.save()
        self.assertEqual(load_nodes(), before)
        choices = [panel.status_combo.itemText(i) for i in range(panel.status_combo.count())]
        self.assertIn('回归验证', choices)
        editor = WorkflowEditor()
        self.addCleanup(editor.deleteLater)
        combo = editor.table.cellWidget(2, 1)
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], choices)
        combo.setCurrentText('回归验证')
        statuses = load_workflow()
        editor.table.item(2, 0).setText('回归')
        editor.save()
        self.assertEqual(load_workflow(), statuses)
        panel.stage_bar.activate_node(2)
        self.assertEqual(panel._current['status'], '回归验证')
        self.assertEqual(panel.status_combo.currentText(), '回归验证')
        self.assertEqual(panel.stage_bar._stages[2][1], '回归')

    def test_toolbar_heights_and_file_width_with_theme(self):
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm', font_size=13)
        panel = self.panel()
        panel.resize(1600, 900)
        panel.show()
        self.app.processEvents()
        widgets = panel._req_toolbar_fields + (panel.update_all_btn, panel.toolbar_more_btn, panel.ticket_btn, panel.add_btn)
        self.assertEqual(len({widget.height() for widget in widgets}), 1,
                         [(widget.objectName(), widget.height()) for widget in widgets])
        self.assertEqual(panel.status_combo.height(), panel.edit_btn.height())
        self.assertAlmostEqual(panel.file_tree.header().length(), panel.file_tree.viewport().width(), delta=2)
        panel.resize(1200, 800)
        self.app.processEvents()
        self.assertAlmostEqual(panel.file_tree.header().length(), panel.file_tree.viewport().width(), delta=2)

    def test_more_menu_has_distinct_import_routes(self):
        panel = self.panel()
        labels = [a.text() for a in panel.toolbar_more_menu.actions()]
        for label in ('通过 SVN 路径导入…', '通过需求目录导入…', '手动登记导入…', '系统配置'):
            self.assertIn(label, labels)
        self.assertNotIn('扫描需求目录…', labels)
        self.assertNotIn('导入资料…', labels)
        action = next(a for a in panel.toolbar_more_menu.actions() if a.text() == '任务进度节点配置…')
        with patch('ui.requirement_workflow_editor.open_workflow_editor') as opener:
            action.trigger()
            opener.assert_called_once_with(panel)
        action = next(a for a in panel.toolbar_more_menu.actions() if a.text() == '任务状态配置…')
        with patch('ui.requirement_workflow_editor.open_status_editor') as opener:
            action.trigger()
            opener.assert_called_once_with(panel)

    def test_workflow_editor_combos_stay_within_rows(self):
        from ui.requirement_workflow_editor import WorkflowEditor
        from ui.theme_manager import ThemeManager
        self.addCleanup(ThemeManager.instance().apply, self.app, 'calm', font_size=13)
        for font_size in (13, 16):
            ThemeManager.instance().apply(self.app, 'calm', font_size=font_size)
            dialog = WorkflowEditor()
            self.addCleanup(dialog.close)
            self.addCleanup(dialog.deleteLater)
            dialog.show()
            self.app.processEvents()
            for row in range(dialog.table.rowCount()):
                combo = dialog.table.cellWidget(row, 1)
                cell = dialog.table.visualRect(dialog.table.model().index(row, 1))
                self.assertLessEqual(combo.height(), cell.height())
                self.assertGreaterEqual(combo.y(), cell.y())
                self.assertLessEqual(combo.geometry().bottom(), cell.bottom())
                self.assertGreaterEqual(combo.height(), combo.minimumSizeHint().height())
            combo = dialog.table.cellWidget(0, 1)
            combo.showPopup()
            self.app.processEvents()
            self.assertGreater(combo.view().height(), combo.view().sizeHintForRow(0) * 3)
            combo.hidePopup()
            combo.setCurrentIndex(combo.findData('集成测试'))
            self.assertEqual(dialog.rows()[0]['default_status'], '集成测试')
            dialog.close()

    def test_status_popup_and_default_node_selection(self):
        from PyQt6.QtWidgets import QMenu
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm', font_size=13)
        panel = self.panel('待开发')
        panel.resize(1400, 850)
        panel.show()
        self.app.processEvents()
        panel.status_combo.showPopup()
        self.app.processEvents()
        view = panel.status_combo.view()
        self.assertGreater(view.height(), view.sizeHintForRow(0) * 3)
        panel.status_combo.hidePopup()
        bar = panel.stage_bar
        with patch.object(QMenu, 'popup') as popup:
            bar.activate_node(1)
            popup.assert_not_called()
        self.assertEqual(panel._current['status'], '开发中')
        self.assertEqual(self.saved[-1][0]['status'], '开发中')
        with patch.object(QMenu, 'popup') as popup:
            bar.activate_node(2)
            popup.assert_not_called()
        self.assertEqual(panel._current['status'], '集成测试')
        self.assertEqual(self.saved[-1][0]['status'], '集成测试')
        self.assertEqual(panel.status_combo.currentText(), '集成测试')
        self.assertEqual(panel.stage_bar._index, 2)

    def test_save_status_with_search_keeps_current_row_and_scroll(self):
        from PyQt6.QtCore import Qt
        panel = self.panel()
        panel._requirements = [dict(panel._requirements[0], id=f'row-{i}', title=f'合成测试需求 {i:03}',
                                    source_modified_at='2026-10-01', status='待开发') for i in range(70)]
        panel.search_edit.setText('合成')
        panel._req_search_timer.stop()
        panel._refresh()
        panel.resize(1400, 720)
        panel.show()
        self.app.processEvents()
        group = panel.requirement_list.topLevelItem(0)
        item = group.child(40)
        panel.requirement_list.setCurrentItem(item)
        panel.requirement_list.scrollToItem(item)
        self.app.processEvents()
        current_id = panel._current['id']
        before = panel.requirement_list.verticalScrollBar().value()
        panel.status_combo.setCurrentText('开发中')
        self.app.processEvents()
        self.assertEqual(panel._current['id'], current_id)
        self.assertEqual(panel.requirement_list.currentItem().data(0, Qt.ItemDataRole.UserRole)['id'], current_id)
        self.assertEqual(panel.requirement_list.verticalScrollBar().value(), before)
        panel._refresh()  # A later background refresh must not select the first match.
        self.assertEqual(panel._current['id'], current_id)

    def test_edit_dialog_with_search_keeps_current_requirement(self):
        from PyQt6.QtWidgets import QDialog
        panel = self.panel()
        panel._requirements += [dict(panel._requirements[0], id='second', title='合成第二条')]
        panel.search_edit.setText('合成')
        panel._req_search_timer.stop()
        panel._refresh()
        group = panel.requirement_list.topLevelItem(0)
        panel.requirement_list.setCurrentItem(group.child(1))
        current_id = panel._current['id']
        values = dict(panel._current, title='合成编辑后的标题')
        with patch('panels.requirement_panel.RequirementDialog') as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.values.return_value = values
            panel._save_dialog(panel._current, offer_next=False)
        self.assertEqual(panel._current['id'], current_id)
        self.assertEqual(panel._current['title'], '合成编辑后的标题')


if __name__ == '__main__':
    unittest.main()
