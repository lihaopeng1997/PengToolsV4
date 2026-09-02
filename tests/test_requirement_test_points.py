# -*- coding: utf-8 -*-
"""测试任务点：提取、规范化、即时写盘、首页按钮、编辑弹窗。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestPointModelTests(unittest.TestCase):
    def test_normalize_missing_and_dirty(self):
        from tools.requirements import normalize_requirement, normalize_test_points

        self.assertEqual(normalize_test_points(None), [])
        self.assertEqual(normalize_test_points('x'), [])
        cleaned = normalize_test_points([
            {'id': 'a', 'text': '核对打印', 'done': 1},
            {'text': '  '},
            '纯文本条目',
            {'id': 'a', 'text': '重复 id', 'done': False},
            12,
        ])
        self.assertEqual(len(cleaned), 3)
        self.assertTrue(cleaned[0]['done'])
        self.assertEqual(cleaned[0]['text'], '核对打印')
        self.assertEqual(cleaned[1]['text'], '纯文本条目')
        self.assertFalse(cleaned[1]['done'])
        self.assertNotEqual(cleaned[2]['id'], 'a')
        item = normalize_requirement({'title': '无测试点'})
        self.assertEqual(item['test_points'], [])

    def test_extract_lists_not_paragraphs(self):
        from tools.requirements import extract_test_points_from_text

        text = (
            '本需求需要在出单时核对保单打印功能，并同步周边系统。\n'
            '1. 核对保单打印\n'
            '2、校验保费计算\n'
            '- 通知周边系统\n'
            '* 回归出单页\n'
            '☑ 已测登录\n'
            '[x] 已测权限\n'
            '[ ] 待测批改\n'
            '这是一段普通说明，里面写了 1. 背景 但整行不是列表。\n'
            + ('- ' + ('很长' * 90) + '\n')
        )
        points = extract_test_points_from_text(text)
        labels = [item['text'] for item in points]
        self.assertEqual(
            labels,
            ['核对保单打印', '校验保费计算', '通知周边系统', '回归出单页', '已测登录', '已测权限', '待测批改'],
        )
        done_map = {item['text']: item['done'] for item in points}
        self.assertTrue(done_map['已测登录'])
        self.assertTrue(done_map['已测权限'])
        self.assertFalse(done_map['待测批改'])
        self.assertFalse(done_map['核对保单打印'])

    def test_progress_and_button_text(self):
        from tools.requirements import test_points_button_text, test_points_progress

        points = [
            {'id': '1', 'text': 'A', 'done': True},
            {'id': '2', 'text': 'B', 'done': False},
        ]
        self.assertEqual(test_points_progress(points), (1, 2))
        self.assertEqual(test_points_button_text(points, zh=True), '1/2')
        self.assertEqual(test_points_button_text([], zh=True), '测试点')
        self.assertEqual(test_points_button_text([], zh=False), 'Tests')

    def test_toggle_persists_without_changing_status(self):
        from tools.requirements import load_requirements, save_requirement_test_points, save_requirements

        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'requirements.json')
            save_requirements([
                {
                    'id': 'r1',
                    'title': '打印改造',
                    'status': '待测试',
                    'description': '1. 核对打印\n2. 校验保费',
                    'test_points': [
                        {'id': 'p1', 'text': '核对打印', 'done': False},
                    ],
                }
            ], path)
            updated = save_requirement_test_points(
                'r1',
                [{'id': 'p1', 'text': '核对打印', 'done': True}],
                path=path,
            )
            self.assertEqual(updated['status'], '待测试')
            self.assertTrue(updated['test_points'][0]['done'])
            loaded = load_requirements(path)
            self.assertEqual(loaded[0]['description'], '1. 核对打印\n2. 校验保费')
            self.assertTrue(loaded[0]['test_points'][0]['done'])


class TestPointUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        cls.app.setFont(QFont('Microsoft YaHei UI', 10))

    def test_dialog_values_include_test_points(self):
        from panels.requirement_panel import RequirementDialog

        dialog = RequirementDialog({
            'title': '打印改造',
            'code': 'REQ-TP',
            'description': '说明正文',
            'test_points': [
                {'id': 'p1', 'text': '核对打印', 'done': True},
            ],
            'sql_parts': [],
            'source_files': [],
        })
        values = dialog.values()
        self.assertEqual(len(values['test_points']), 1)
        self.assertEqual(values['test_points'][0]['text'], '核对打印')
        self.assertTrue(values['test_points'][0]['done'])
        self.assertEqual(values['description'], '说明正文')
        dialog.close()

    def test_dialog_does_not_auto_seed_on_save(self):
        from panels.requirement_panel import RequirementDialog

        dialog = RequirementDialog({
            'title': '未提取',
            'description': '1. 核对打印\n2. 校验保费',
            'test_points': [],
            'sql_parts': [],
            'source_files': [],
        })
        values = dialog.values()
        self.assertEqual(values['test_points'], [])
        self.assertIn('核对打印', values['description'])
        dialog.close()

    def test_editor_seeds_from_description_until_commit(self):
        from panels.test_points_editor import TestPointsEditor

        editor = TestPointsEditor(
            [],
            description='1. 核对打印\n- 校验保费',
            persist_callback=None,
            auto_seed=True,
        )
        self.assertTrue(editor.pending_seed())
        texts = [item['text'] for item in editor.points()]
        self.assertEqual(texts, ['核对打印', '校验保费'])
        editor.close()

    def test_dialog_opens_with_seeded_points(self):
        from panels.test_points_editor import TestPointsDialog

        dialog = TestPointsDialog(
            {
                'id': 'r1',
                'title': '打印改造',
                'description': '1. 核对打印\n2. 校验保费',
                'test_points': [],
            },
            persist=False,
        )
        self.assertTrue(dialog.editor.pending_seed())
        self.assertEqual([item['text'] for item in dialog.points()], ['核对打印', '校验保费'])
        dialog.close()

    def test_dashboard_button_text(self):
        from panels.dashboard_panel import DashboardPanel

        current = __import__('datetime').date.today().strftime('%Y-%m') + '-15'
        requirements = [
            {
                'id': 'r1',
                'title': '有测试点',
                'planned_online_date': current,
                'status': '待测试',
                'test_points': [
                    {'id': 'p1', 'text': 'A', 'done': True},
                    {'id': 'p2', 'text': 'B', 'done': False},
                ],
            },
            {
                'id': 'r2',
                'title': '无测试点',
                'planned_online_date': current,
                'status': '开发中',
                'test_points': [],
            },
        ]
        board = {'completed_requirement_keys': [], 'ui_prefs': {'completed_section_collapsed': True}}
        with patch('panels.dashboard_panel.load_requirements', return_value=requirements), \
                patch('panels.dashboard_panel.load_release_board', return_value=board):
            panel = DashboardPanel('zh')
            panel.refresh()
            rows = []
            for i in range(panel.release_list.count()):
                widget = panel.release_list.itemAt(i).widget()
                if widget is not None and hasattr(widget, 'test_points_btn'):
                    rows.append(widget)
            labels = {row._payload['id']: row.test_points_btn.text() for row in rows}
            self.assertEqual(labels.get('r1'), '1/2')
            self.assertEqual(labels.get('r2'), '测试点')
            panel.close()
