# -*- coding: utf-8 -*-
import datetime
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.ticket_submit import (
    TicketSubmitError, build_ticket_payload, default_slot, fill_ticket_xls,
    find_latest_ticket, format_numbered_cell, load_last_submit,
    load_ticket_profiles, next_ticket_folder, normalize_ticket_profile,
    parse_ticket_folder, requirement_candidates_for_profile, save_last_submit,
    save_ticket_profiles, submit_ticket,
)

ECIF_XLS = os.path.join(
    ROOT, 'tiqian', '客户信息平台升级签', 'int', '2026',
    'INT_INT_ECIF_2026081910A-李浩鹏', '升级路径', '客户信息平台日常功能发布单.xls',
)
PRPCAR_XLS = os.path.join(
    ROOT, 'tiqian', '车险共享中心提签', 'int', '2026',
    'INT_INT_prpcar_2026081815A-李浩鹏', '升级清单', '车险共享中心系统日常功能发布单.xls',
)


class TicketSubmitTests(unittest.TestCase):
    def test_default_slot_morning_and_afternoon(self):
        self.assertEqual(default_slot(datetime.datetime(2026, 8, 19, 9, 30)), '10')
        self.assertEqual(default_slot(datetime.datetime(2026, 8, 19, 12, 0)), '15')
        self.assertEqual(default_slot(datetime.datetime(2026, 8, 19, 16, 1)), '15')

    def test_parse_and_increment_folder_name(self):
        names = [
            'INT_INT_ECIF_2026081910A-李浩鹏',
            'INT_INT_ECIF_2026081910B-张三',
            'UAT_UAT_ECIF_2026081910A-李浩鹏',
        ]
        latest = find_latest_ticket(names)
        self.assertEqual(latest['name'], 'INT_INT_ECIF_2026081910B-张三')
        nxt = next_ticket_folder(
            names, 'INT', 'ECIF', '李浩鹏',
            now=datetime.datetime(2026, 8, 19, 9, 0), slot='10',
        )
        self.assertEqual(nxt, 'INT_INT_ECIF_2026081910C-李浩鹏')
        self.assertIsNone(parse_ticket_folder('readme.txt'))

    def test_numbered_cell_and_payload(self):
        self.assertEqual(format_numbered_cell(['REQ-1']), 'REQ-1')
        self.assertEqual(format_numbered_cell(['REQ-1', 'REQ-2']), '1.REQ-1  2.REQ-2')
        payload = build_ticket_payload(
            [
                {'code': 'REQ-1', 'title': '功能甲', 'has_sql': True},
                {'code': 'REQ-2', 'title': '功能乙'},
            ],
            owner='李浩鹏',
            host='10.1.1.1',
        )
        self.assertEqual(payload['需求编号'], '1.REQ-1  2.REQ-2')
        self.assertEqual(payload['功能描述'], '1.功能甲  2.功能乙')
        self.assertEqual(payload['是否有SQL'], '否')
        self.assertEqual(payload['是否有jar包'], '否')
        self.assertEqual(
            build_ticket_payload([{'code': 'REQ-1', 'title': '甲', 'has_sql': True}], owner='李', has_sql=True)['是否有SQL'],
            '是',
        )
        self.assertEqual(payload['责任人'], '李浩鹏')
        self.assertEqual(payload['文件个数'], '')
        self.assertEqual(payload['升级环境地址'], '')

    def test_profile_normalize_and_requirement_filter(self):
        profile = normalize_ticket_profile({
            'name': '客户信息平台',
            'source_systems': ['客户信息平台（ECIF）', ''],
            'envs': {'INT': {'svn_url': 'svn://x/int'}},
        })
        self.assertEqual(profile['envs']['UAT']['svn_url'], '')
        self.assertEqual(profile['source_systems'], ['客户信息平台（ECIF）'])
        reqs = [
            {'title': 'A', 'systems': ['客户信息平台（ECIF）']},
            {'title': 'B', 'systems': ['车险承保中心']},
            {'title': 'C', 'system': ''},
        ]
        titles = [item['title'] for item in requirement_candidates_for_profile(reqs, profile)]
        self.assertEqual(titles, ['A', 'C'])

    def test_fill_ecif_and_prpcar_templates_by_header(self):
        if not os.path.isfile(ECIF_XLS):
            self.skipTest('本机没有 ECIF 样例签')
        import xlrd
        with tempfile.TemporaryDirectory() as temp:
            out = os.path.join(temp, 'ecif.xls')
            fill_ticket_xls(ECIF_XLS, out, {
                '需求编号': 'REQ-1',
                '功能描述': '测试功能',
                '责任人': '李浩鹏',
                '是否有SQL': '否',
            })
            sheet = xlrd.open_workbook(out).sheet_by_index(0)
            self.assertEqual(sheet.cell_value(2, 3), 'REQ-1')
            self.assertEqual(sheet.cell_value(2, 4), '测试功能')
            self.assertEqual(sheet.cell_value(2, 1), '客户信息平台')
        if not os.path.isfile(PRPCAR_XLS):
            return
        with tempfile.TemporaryDirectory() as temp:
            out = os.path.join(temp, 'prpcar.xls')
            fill_ticket_xls(PRPCAR_XLS, out, {
                '需求编号': '1.REQ-A  2.REQ-B',
                '功能描述': '1.甲  2.乙',
                '责任人': '李浩鹏',
            })
            sheet = xlrd.open_workbook(out).sheet_by_index(0)
            self.assertEqual(sheet.cell_value(2, 5), '1.REQ-A  2.REQ-B')
            self.assertEqual(sheet.cell_value(2, 6), '1.甲  2.乙')

    def test_submit_without_svn_url_is_rejected(self):
        with self.assertRaises(TicketSubmitError):
            submit_ticket(
                {'name': '空', 'folder_code': 'X'},
                'INT',
                [{'code': 'REQ-1', 'title': 't'}],
            )

    def test_submit_uses_latest_template_and_imports(self):
        if not os.path.isfile(ECIF_XLS):
            self.skipTest('本机没有 ECIF 样例签')
        calls = []

        def fake_list(url):
            calls.append(('list', url))
            return ['INT_INT_ECIF_2026081910A-李浩鹏']

        def fake_export(url, dest):
            calls.append(('export', url))
            os.makedirs(os.path.join(dest, '升级路径'), exist_ok=True)
            shutil.copy2(ECIF_XLS, os.path.join(dest, '升级路径', '客户信息平台日常功能发布单.xls'))
            return {'path': dest}

        def fake_import(local, url, message):
            calls.append(('import', url, os.path.basename(local)))
            self.assertTrue(os.path.isdir(local))
            self.assertTrue(any(name.endswith('.xls') for _, _, files in os.walk(local) for name in files))
            return {'url': url, 'output': 'Committed revision 9.'}

        profile = {
            'name': '客户信息平台',
            'folder_code': 'ECIF',
            'envs': {'INT': {'svn_url': 'svn://example/ecif/int'}},
        }
        result = submit_ticket(
            profile, 'INT',
            [{'code': 'REQ-9', 'title': '一键提签'}],
            owner='李浩鹏',
            now=datetime.datetime(2026, 8, 19, 9, 0),
            svn_ops={
                'list': fake_list,
                'export': fake_export,
                'mkdir': lambda *a, **k: None,
                'import': fake_import,
                'validate': lambda url: url.rstrip('/'),
            },
        )
        self.assertEqual(result['folder'], 'INT_INT_ECIF_2026081910B-李浩鹏')
        self.assertIn('INT_INT_ECIF_2026081910B-李浩鹏', result['url'])
        self.assertEqual([item[0] for item in calls], ['list', 'export', 'import'])

    def test_config_dialog_switch_profile_does_not_overwrite_target(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from unittest.mock import patch
        from PyQt6.QtWidgets import QApplication
        from panels.ticket_submit_dialog import TicketSubmitConfigDialog
        from tools.ticket_submit import default_ticket_profiles, normalize_ticket_profile
        app = QApplication.instance() or QApplication([])
        seeded = [normalize_ticket_profile(item) for item in default_ticket_profiles()]
        with patch('panels.ticket_submit_dialog.load_ticket_profiles', return_value=seeded):
            dialog = TicketSubmitConfigDialog()
        names = [dialog.list.item(i).text() for i in range(dialog.list.count())]
        self.assertIn('客户信息平台', names)
        self.assertIn('车险共享中心', names)
        dialog.list.setCurrentRow(0)
        dialog._show_profile(0)
        self.assertEqual(dialog.name_edit.text(), '客户信息平台')
        self.assertEqual(dialog.code_edit.text(), 'ECIF')
        share_row = names.index('车险共享中心')
        dialog.list.setCurrentRow(share_row)
        dialog._show_profile(share_row)
        self.assertEqual(dialog.name_edit.text(), '车险共享中心')
        self.assertEqual(dialog.code_edit.text(), 'prpcar')
        self.assertEqual(dialog._profiles[0]['name'], '客户信息平台')
        self.assertEqual(dialog._profiles[share_row]['name'], '车险共享中心')
        dialog.close()
        app

    def test_submit_dialog_lists_selected_requirements(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6.QtWidgets import QApplication
        from panels.ticket_submit_dialog import TicketSubmitDialog
        app = QApplication.instance() or QApplication([])
        dialog = TicketSubmitDialog(
            [
                {'id': 'a', 'code': 'REQ-A', 'title': '甲', 'systems': ['客户信息平台（ECIF）']},
                {'id': 'b', 'code': 'REQ-B', 'title': '乙', 'systems': ['车险承保中心']},
            ],
            selected_ids=['a'],
        )
        self.assertGreaterEqual(dialog.req_list.count(), 1)
        self.assertEqual(dialog.slot_combo.currentData() in ('10', '15'), True)
        dialog.close()
        app  # keep ref

    def test_submit_dialog_keeps_requirement_list_large_and_actions_fixed(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from unittest.mock import patch
        from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QScrollArea
        from panels.ticket_submit_dialog import TicketSubmitDialog
        from tools.ticket_submit import default_ticket_profiles
        app = QApplication.instance() or QApplication([])
        requirements = [
            {'id': str(i), 'code': f'REQ-{i}', 'title': f'需求{i}', 'systems': ['客户信息平台（ECIF）']}
            for i in range(50)
        ]
        with patch('panels.ticket_submit_dialog.load_ticket_profiles', return_value=default_ticket_profiles()):
            dialog = TicketSubmitDialog(requirements)
        dialog.show()
        QApplication.processEvents()
        self.assertGreaterEqual(dialog.req_list.minimumHeight(), 280)
        self.assertGreaterEqual(dialog.req_list.height(), 280)
        self.assertEqual(dialog.req_list.count(), 50)
        self.assertIsInstance(dialog.content_scroll, QScrollArea)
        buttons = dialog.findChild(QDialogButtonBox)
        self.assertIsNotNone(buttons)
        self.assertIs(buttons.parentWidget(), dialog)
        self.assertLessEqual(dialog.height(), QApplication.primaryScreen().availableGeometry().height())
        dialog.close()
        app

    def test_compact_submit_dialog_keeps_requirement_list_large(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from unittest.mock import patch
        from PyQt6.QtWidgets import QApplication
        from panels.ticket_submit_dialog import TicketSubmitDialog
        from tools.ticket_submit import default_ticket_profiles
        app = QApplication.instance() or QApplication([])
        requirements = [
            {'id': str(i), 'code': f'REQ-{i}', 'title': f'需求{i}', 'systems': ['客户信息平台（ECIF）']}
            for i in range(20)
        ]
        with patch('panels.ticket_submit_dialog.load_ticket_profiles', return_value=default_ticket_profiles()):
            dialog = TicketSubmitDialog(requirements, compact=True)
        dialog.show()
        QApplication.processEvents()
        self.assertGreaterEqual(dialog.req_list.minimumHeight(), 220)
        self.assertGreaterEqual(dialog.req_list.height(), 220)
        self.assertLessEqual(dialog.height(), QApplication.primaryScreen().availableGeometry().height())
        dialog.close()
        app

    def test_last_submit_survives_profile_save(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'ticket_submit.json')
            save_ticket_profiles([
                normalize_ticket_profile({'id': 'ecif', 'name': '客户信息平台', 'folder_code': 'ECIF'}),
            ], path)
            save_last_submit({'profile_id': 'ecif', 'env': 'INT', 'slot': '15', 'owner': '李浩鹏'}, path)
            save_ticket_profiles(load_ticket_profiles(path), path)
            last = load_last_submit(path)
            self.assertEqual(last['env'], 'INT')
            self.assertEqual(last['slot'], '15')
            self.assertEqual(last['owner'], '李浩鹏')

    def test_load_profiles_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            profiles = load_ticket_profiles(os.path.join(temp, 'missing.json'))
        self.assertGreaterEqual(len(profiles), 2)
        self.assertEqual(profiles[0]['folder_code'], 'ECIF')


if __name__ == '__main__':
    unittest.main()
