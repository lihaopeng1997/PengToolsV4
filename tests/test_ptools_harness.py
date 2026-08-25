# -*- coding: utf-8 -*-
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.linux_guard import inspect_command, inspect_commands
from tools.ptools_harness import TASKS, _extract_json_object


class LinuxGuardTests(unittest.TestCase):
    def test_allows_read_only_queries(self):
        self.assertTrue(inspect_command("grep -n 'OOM' /opt/app/logs/app.log")[0])
        self.assertTrue(inspect_command('tail -n 100 /var/log/messages')[0])
        self.assertTrue(inspect_command('ps aux | grep java')[0])
        self.assertTrue(inspect_command('df -h')[0])

    def test_rejects_destructive_commands(self):
        self.assertFalse(inspect_command('rm -rf /')[0])
        self.assertFalse(inspect_command('reboot')[0])
        self.assertFalse(inspect_command('kill -9 1')[0])
        self.assertFalse(inspect_command('chmod 777 /tmp')[0])
        self.assertFalse(inspect_command('cat file | sh')[0])
        self.assertFalse(inspect_command('echo hi > /tmp/x')[0])
        self.assertFalse(inspect_command('sudo grep foo /etc/passwd')[0])
        self.assertFalse(inspect_command('find / -delete')[0])

    def test_inspect_commands_splits_allowed_and_rejected(self):
        allowed, rejected = inspect_commands([
            'tail -n 20 /var/log/app.log',
            'rm -rf /tmp/a',
            '',
        ])
        self.assertEqual(allowed, ['tail -n 20 /var/log/app.log'])
        self.assertEqual(len(rejected), 1)
        self.assertIn('rm', rejected[0][1].lower() + rejected[0][0])


class PtoolsHarnessParseTests(unittest.TestCase):
    def test_known_tasks(self):
        self.assertIn('sql.draft', TASKS)
        self.assertIn('sql.optimize', TASKS)
        self.assertIn('linux.query', TASKS)

    def test_extract_json_object_from_fence(self):
        data = _extract_json_object('```json\n{"summary":"oom","commands":["tail -n 20 a.log"],"risk":"safe"}\n```')
        self.assertEqual(data['summary'], 'oom')
        self.assertEqual(data['commands'], ['tail -n 20 a.log'])


if __name__ == '__main__':
    unittest.main()
