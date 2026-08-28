# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import harness_project
from tools.harness_project import list_projects, load_project, project_context, scan_mybatis_tables
from tools.linux_guard import inspect_command, inspect_commands
from tools.ptools_harness import TASKS, _extract_json_object, list_tasks, resolve_task_file


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
        # 内置 5 任务（清单驱动，非硬编码 3 个）
        tasks = list_tasks()
        ids = {item.get('task') for item in tasks}
        self.assertEqual(ids, {
            'sql.draft', 'sql.optimize', 'linux.query', 'mongo.query', 'redis.query',
        })
        self.assertTrue(all(item.get('builtin') is True for item in tasks))
        # TASKS 常量（导入时算）与列表一致
        self.assertIn('mongo.query', TASKS)
        self.assertIn('redis.query', TASKS)

    def test_resolve_task_file_returns_skill_filename(self):
        self.assertEqual(resolve_task_file('sql.draft'), 'sql.md')
        self.assertEqual(resolve_task_file('mongo.query'), 'mongo_query.md')
        self.assertEqual(resolve_task_file('redis.query'), 'redis_query.md')
        self.assertIsNone(resolve_task_file('unknown.task'))

    def test_user_skill_overrides_builtin_same_name(self):
        """用户 skills.json 中同名 task 覆盖内置字段，保留 builtin=True。"""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = os.path.join(tmp, 'skills.json')
            with open(manifest, 'w', encoding='utf-8') as stream:
                json.dump({'tasks': [
                    {'task': 'sql.draft', 'file': 'my_custom_sql.md',
                     'title': '我的SQL草案', 'desc': '覆盖', 'enabled': True},
                ]}, stream, ensure_ascii=False)
            with patch.object(harness_project, 'HARNESS_SKILLS_FILE', manifest):
                tasks = {item['task']: item for item in harness_project.list_tasks()}
            self.assertEqual(tasks['sql.draft']['file'], 'my_custom_sql.md')
            self.assertEqual(tasks['sql.draft']['title'], '我的SQL草案')
            # 覆盖后仍视为内置（不可删，仅可停用）
            self.assertIs(tasks['sql.draft']['builtin'], True)

    def test_user_skill_adds_new_task(self):
        """用户 skills.json 中新增 task 追加到清单末尾（不覆盖内置）。"""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = os.path.join(tmp, 'skills.json')
            with open(manifest, 'w', encoding='utf-8') as stream:
                json.dump({'tasks': [
                    {'task': 'biz.extract', 'file': 'biz_extract.md',
                     'title': '业务抽取', 'desc': '新增', 'enabled': True},
                ]}, stream, ensure_ascii=False)
            with patch.object(harness_project, 'HARNESS_SKILLS_FILE', manifest):
                tasks = harness_project.list_tasks()
            ids = {item['task']: item for item in tasks}
            self.assertIn('biz.extract', ids)
            self.assertIs(ids['biz.extract']['builtin'], False)
            # 内置 5 个仍全部保留
            for builtin in ('sql.draft', 'sql.optimize', 'linux.query', 'mongo.query', 'redis.query'):
                self.assertIn(builtin, ids)

    def test_builtin_prpcar_project_pack(self):
        ids = [item.get('id') for item in list_projects()]
        self.assertIn('prpcar', ids)
        project = load_project('prpcar')
        self.assertEqual(project.get('dialect'), 'oracle')
        text = project_context(project)
        self.assertIn('prpTmain', text)
        self.assertIn('车险', text)

    def test_scan_mybatis_tables_from_xml(self):
        import tempfile
        xml = '''<?xml version="1.0"?>
        <mapper namespace="x">
          <select id="a">select * from prpTmain t join prpCmain c on 1=1</select>
          <insert id="b">insert into prpPhead (ENDORSENo) values (1)</insert>
        </mapper>
        '''
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'PrpTmainDao.xml')
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write(xml)
            tables = scan_mybatis_tables(tmp)
        self.assertIn('prpTmain', tables)
        self.assertIn('prpCmain', tables)
        self.assertIn('prpPhead', tables)

    def test_extract_json_object_from_fence(self):
        data = _extract_json_object('```json\n{"summary":"oom","commands":["tail -n 20 a.log"],"risk":"safe"}\n```')
        self.assertEqual(data['summary'], 'oom')
        self.assertEqual(data['commands'], ['tail -n 20 a.log'])


if __name__ == '__main__':
    unittest.main()
