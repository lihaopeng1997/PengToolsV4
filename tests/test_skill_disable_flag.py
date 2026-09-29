# -*- coding: utf-8 -*-
"""skill 停用标记回归测试：停用后任何入口都不再执行该 skill。

执行路径清单（已审计，全部汇入统一 choke point）：
  入口 A：panels.sql_panel.SqlDraftWorker（SQL 面板 AI 草稿/优化快捷入口）
  入口 B：panels.ops_log_panel._LinuxQueryWorker（运维面板 Linux 查询快捷入口）
  入口 C：tools.ai_harness.draft_sql（遗留兼容入口）
三者最终都调用 tools.ptools_harness.run_task；修复点即 run_task 开头的
停用拦截（find_task + enabled 检查），拦截发生在触及 skill 文本与内网
模型之前。
"""

import contextlib
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
from tools.ai_harness import draft_sql
from tools.harness_project import find_task, is_task_enabled
from tools.intranet_llm import IntranetLlmError
from tools.ptools_harness import run_task

try:
    from panels.ops_log_panel import _LinuxQueryWorker
    from panels.sql_panel import SqlDraftWorker
    _PANELS_AVAILABLE = True
except Exception:
    _PANELS_AVAILABLE = False

# 满足 is_enabled(settings) 的假配置；project_id 避免回退读用户真实配置
_CFG = {'enabled': True, 'base_url': 'http://127.0.0.1:1/v1', 'project_id': 'prpcar'}


@contextlib.contextmanager
def _patched_skills(tasks):
    """用临时 skills.json 覆盖清单；用户目录也指向临时目录，避免污染真实数据。"""
    with tempfile.TemporaryDirectory() as tmp:
        manifest = os.path.join(tmp, 'skills.json')
        with open(manifest, 'w', encoding='utf-8') as stream:
            json.dump({'tasks': tasks}, stream, ensure_ascii=False)
        skills_dir = os.path.join(tmp, 'skills')
        projects_dir = os.path.join(tmp, 'projects')
        config_dir = os.path.join(tmp, 'config')
        with patch.object(harness_project, 'HARNESS_SKILLS_FILE', manifest), \
                patch.object(harness_project, 'HARNESS_SKILLS_DIR', skills_dir), \
                patch.object(harness_project, 'HARNESS_PROJECTS_DIR', projects_dir), \
                patch.object(harness_project, 'ensure_config_dir',
                             lambda: os.makedirs(config_dir, exist_ok=True)):
            yield


def _task_entry(task, enabled):
    builtin_files = {
        'sql.draft': 'sql.md',
        'sql.optimize': 'sql_optimize.md',
        'linux.query': 'log_query.md',
        'mongo.query': 'mongo_query.md',
        'redis.query': 'redis_query.md',
    }
    return {
        'task': task,
        'file': builtin_files.get(task, task.replace('.', '_') + '.md'),
        'title': task,
        'desc': '',
        'enabled': enabled,
    }


class TaskEnabledFlagTests(unittest.TestCase):
    def test_builtin_task_enabled_by_default(self):
        with _patched_skills([]):
            self.assertTrue(is_task_enabled('sql.draft'))
            self.assertIsNotNone(find_task('sql.draft'))

    def test_disabled_builtin_task_flag(self):
        with _patched_skills([_task_entry('sql.draft', False)]):
            self.assertFalse(is_task_enabled('sql.draft'))

    def test_disabled_user_task_flag(self):
        with _patched_skills([{
            'task': 'biz.extract', 'file': 'biz_extract.md',
            'title': '业务抽取', 'desc': '', 'enabled': False,
        }]):
            self.assertFalse(is_task_enabled('biz.extract'))

    def test_unknown_task_treated_as_disabled(self):
        with _patched_skills([]):
            self.assertFalse(is_task_enabled('no.such.task'))
            self.assertIsNone(find_task('no.such.task'))


class RunTaskDisabledTests(unittest.TestCase):
    """choke point：run_task 对停用 task 直接拒绝，且不调用内网模型。"""

    def test_run_task_disabled_builtin_raises(self):
        with _patched_skills([_task_entry('sql.draft', False)]):
            with patch('tools.ptools_harness.chat_completions') as mocked:
                with self.assertRaises(IntranetLlmError) as ctx:
                    run_task('sql.draft', '查所有表', cfg=_CFG)
                mocked.assert_not_called()
        self.assertIn('停用', str(ctx.exception))

    def test_run_task_disabled_linux_query_raises(self):
        with _patched_skills([_task_entry('linux.query', False)]):
            with patch('tools.ptools_harness.chat_completions') as mocked:
                with self.assertRaises(IntranetLlmError) as ctx:
                    run_task('linux.query', '查最近错误日志', cfg=_CFG)
                mocked.assert_not_called()
        self.assertIn('停用', str(ctx.exception))

    def test_run_task_disabled_user_task_raises(self):
        entry = {
            'task': 'biz.extract', 'file': 'biz_extract.md',
            'title': '业务抽取', 'desc': '', 'enabled': False,
        }
        with _patched_skills([entry]):
            with patch('tools.ptools_harness.chat_completions') as mocked:
                with self.assertRaises(IntranetLlmError) as ctx:
                    run_task('biz.extract', '抽取', cfg=_CFG)
                mocked.assert_not_called()
        self.assertIn('停用', str(ctx.exception))

    def test_run_task_unknown_task_still_raises_unknown(self):
        with _patched_skills([]):
            with self.assertRaises(IntranetLlmError) as ctx:
                run_task('no.such.task', 'xxx', cfg=_CFG)
        self.assertIn('未知任务', str(ctx.exception))

    def test_run_task_enabled_task_still_executes(self):
        """修复不得破坏正常路径：启用的 task 照常走完 LLM 调用。"""
        with _patched_skills([_task_entry('sql.draft', True)]):
            with patch('tools.ptools_harness.chat_completions',
                       return_value='SELECT 1 FROM dual') as mocked:
                result = run_task('sql.draft', '查所有表', cfg=_CFG)
        mocked.assert_called_once()
        self.assertEqual(result, 'SELECT 1 FROM dual')

    def test_run_task_enabled_linux_query_still_executes(self):
        payload = '{"summary":"oom","commands":["tail -n 20 app.log"],"risk":"safe"}'
        with _patched_skills([_task_entry('linux.query', True)]):
            with patch('tools.ptools_harness.chat_completions',
                       return_value=payload) as mocked:
                result = run_task('linux.query', '查最近错误日志', cfg=_CFG)
        mocked.assert_called_once()
        self.assertIn('tail -n 20 app.log', result['allowed'])


class EntryPointTests(unittest.TestCase):
    """三个执行入口：停用后均不执行 skill、不调用内网模型。

    入口 A/B 需 PyQt6（面板 worker 为 QThread 子类）；环境缺 Qt 时跳过，
    核心拦截逻辑已由 RunTaskDisabledTests 在无 Qt 条件下覆盖。
    """

    @unittest.skipUnless(_PANELS_AVAILABLE, 'PyQt6 不可用，跳过面板 worker 入口测试')
    def test_entry_a_sql_panel_worker_disabled(self):
        """入口 A：SQL 面板 AI 草稿/优化快捷入口。"""
        with _patched_skills([_task_entry('sql.draft', False)]):
            with patch('tools.ptools_harness.chat_completions') as mocked:
                worker = SqlDraftWorker('查所有表', _CFG, task='sql.draft')
                failures, completions = [], []
                worker.failed.connect(failures.append)
                worker.completed.connect(completions.append)
                worker.run()  # 同线程直接执行，不起线程
                mocked.assert_not_called()
        self.assertEqual(completions, [])
        self.assertEqual(len(failures), 1)
        self.assertIn('停用', failures[0])

    @unittest.skipUnless(_PANELS_AVAILABLE, 'PyQt6 不可用，跳过面板 worker 入口测试')
    def test_entry_b_ops_log_worker_disabled(self):
        """入口 B：运维面板 Linux 只读查询快捷入口。"""
        with _patched_skills([_task_entry('linux.query', False)]):
            with patch('tools.ptools_harness.chat_completions') as mocked:
                worker = _LinuxQueryWorker('查最近错误日志', '', _CFG)
                failures, completions = [], []
                worker.failed.connect(failures.append)
                worker.completed.connect(completions.append)
                worker.run()
                mocked.assert_not_called()
        self.assertEqual(completions, [])
        self.assertEqual(len(failures), 1)
        self.assertIn('停用', failures[0])

    def test_entry_c_ai_harness_draft_sql_disabled(self):
        """入口 C：ai_harness.draft_sql 遗留兼容入口。"""
        with _patched_skills([_task_entry('sql.draft', False)]):
            with patch('tools.ptools_harness.chat_completions') as mocked:
                with self.assertRaises(IntranetLlmError) as ctx:
                    draft_sql('查所有表', cfg=_CFG)
                mocked.assert_not_called()
        self.assertIn('停用', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
