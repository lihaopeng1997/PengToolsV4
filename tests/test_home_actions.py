"""首页就地操作：详情 / 拖动改阶段 / 测试点 / 字段 / 快速新建 / 桥接 JSON。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from tools import home_actions
from tools.dashboard_summary import requirement_progress
from tools.requirements import load_requirements, save_requirements


class HomeActionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'requirements.json')
        save_requirements([
            {'id': 'r1', 'code': 'REQ-1', 'title': '承保规则', 'status': '用户测试',
             'test_points': [{'id': 'a', 'text': '规则一', 'done': False}]},
            {'id': 'r2', 'code': 'REQ-2', 'title': '查询优化', 'status': '开发中'},
        ], self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def act(self, **payload):
        return home_actions.handle(payload, path=self.path)

    def stored(self, rid):
        return next(item for item in load_requirements(self.path) if item['id'] == rid)

    def test_detail_contains_stage_and_points(self):
        result = self.act(action='detail', id='r1')
        self.assertTrue(result['ok'])
        item = result['item']
        self.assertEqual((item['group'], item['stage'], item['tp_total']), ('testing', 2, 1))
        self.assertIn('已上线', result['statuses'])
        self.assertEqual(self.act(action='detail', id='missing')['ok'], False)

    def test_move_group_keeps_finer_status_within_group(self):
        same = self.act(action='move_group', id='r1', group='testing')
        self.assertEqual(same['item']['status'], '用户测试')
        moved = self.act(action='move_group', id='r1', group='release')
        self.assertEqual(self.stored('r1')['status'], '待上线')
        self.assertTrue(moved['changed'])
        online = self.act(action='move_group', id='r2', group='online')
        self.assertEqual(online['item']['status'], '已上线')
        self.assertTrue(self.stored('r2')['actual_release_date'])  # 上线写入今天
        self.assertFalse(self.act(action='move_group', id='r2', group='nope')['ok'])

    def test_test_points_and_fields(self):
        result = self.act(action='save_test_points', id='r1', points=[
            {'id': 'a', 'text': '规则一', 'done': True},
            {'id': '', 'text': '  新增  测试点 ', 'done': False},
            {'text': ''},
        ])
        self.assertEqual((result['item']['tp_done'], result['item']['tp_total']), (1, 2))
        self.assertEqual(self.stored('r1')['test_points'][1]['text'], '新增 测试点')
        self.act(action='update_fields', id='r1', fields={'title': '承保规则 v2', 'priority': '紧急',
                                                          'actual_release_date': '2026-10-30'})
        row = self.stored('r1')
        self.assertEqual((row['title'], row['priority'], row['actual_release_date']), ('承保规则 v2', '紧急', '2026-10-30'))
        self.assertFalse(self.act(action='update_fields', id='r1', fields={'svn_url': 'x'})['ok'])
        self.assertFalse(self.act(action='update_fields', id='r1', fields={'actual_release_date': '30/10'})['ok'])
        self.assertFalse(self.act(action='update_fields', id='r1', fields={'title': '  '})['ok'])

    def test_create_and_demo_guard(self):
        created = self.act(action='create', title='新需求', code='REQ-9', group='testing')
        self.assertTrue(created['ok'])
        self.assertEqual(created['item']['status'], '待测试')
        self.assertEqual(load_requirements(self.path)[0]['title'], '新需求')
        self.assertFalse(self.act(action='create', title=' ')['ok'])
        self.assertFalse(self.act(action='set_status', id='demo-1', status='开发中')['ok'])
        self.assertFalse(self.act(action='set_status', id='r1', status='乱写')['ok'])
        self.assertFalse(home_actions.handle('not a dict')['ok'])

    def test_board_lists_active_and_this_month_online(self):
        items = load_requirements(self.path) + [
            {'id': 'o1', 'title': '本月上线', 'status': '已上线', 'actual_release_date': '2026-10-05'},
            {'id': 'o2', 'title': '上月上线', 'status': '已上线', 'actual_release_date': '2026-09-05'},
        ]
        progress = requirement_progress(items, month='2026-10')
        ids = {row['id']: row['group'] for row in progress['board']}
        self.assertEqual(ids, {'r1': 'testing', 'r2': 'develop', 'o1': 'online'})
        self.assertEqual(progress['online_month'], 1)


class HomeBridgeActionTests(unittest.TestCase):
    def test_bridge_returns_json_and_never_raises(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from ui.web_shell import HomeBridge

        bridge = HomeBridge()
        self.assertFalse(json.loads(bridge.homeAction('{"action":"detail"}'))['ok'])
        seen = []
        bridge.set_home_action_handler(lambda payload: seen.append(payload) or {'ok': True, 'item': {'id': 'x'}})
        self.assertEqual(json.loads(bridge.homeAction('{"action":"detail","id":"x"}'))['item']['id'], 'x')
        self.assertEqual(seen, [{'action': 'detail', 'id': 'x'}])
        self.assertFalse(json.loads(bridge.homeAction('not json'))['ok'])
        bridge.set_home_action_handler(lambda payload: 1 / 0)
        self.assertEqual(json.loads(bridge.homeAction('{}')), {'ok': False, 'error': '操作失败'})


if __name__ == '__main__':
    unittest.main()
