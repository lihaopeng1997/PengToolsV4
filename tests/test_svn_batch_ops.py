# -*- coding: utf-8 -*-
"""SVN 批量路径工具：不依赖本机 svn 的纯逻辑 + 可选集成。"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.svn_workspace import (
    _normalize_file_paths, changed_paths, commit_paths, lock_files,
    revert_paths, unlock_files, working_copy_locks,
)


class SvnBatchPathTests(unittest.TestCase):
    def test_normalize_file_paths_filters_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            f1 = os.path.join(tmp, 'a.txt')
            d1 = os.path.join(tmp, 'sub')
            os.makedirs(d1)
            with open(f1, 'w', encoding='utf-8') as stream:
                stream.write('x')
            files = _normalize_file_paths([f1, d1, f1, os.path.join(tmp, 'missing.txt')])
            self.assertEqual(files, [os.path.abspath(f1)])

    def test_changed_paths_parses_status_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            f1 = os.path.join(tmp, 'x.sql')
            with open(f1, 'w', encoding='utf-8') as stream:
                stream.write('1')
            fake = {
                'clean': False,
                'changes': [
                    f'M       {f1}',
                    f'?       {os.path.join(tmp, "new.txt")}',
                ],
                'text': 'demo',
                'returncode': 0,
            }
            with mock.patch('tools.svn_workspace.svn_status', return_value=fake):
                info = changed_paths(tmp)
            self.assertFalse(info['clean'])
            self.assertIn(os.path.abspath(f1), info['paths'])

    def test_lock_files_requires_files(self):
        with self.assertRaises(ValueError):
            lock_files([])

    def test_working_copy_locks_parses_xml(self):
        xml = '''<?xml version="1.0" encoding="UTF-8"?>
<status>
  <target path="C:/wc">
    <entry path="C:/wc/a.txt">
      <wc-status item="normal" revision="3" props="none">
        <lock>
          <token>opaquelocktoken:abc</token>
          <owner>lihp</owner>
          <created>2026-07-22T01:00:00.000000Z</created>
          <comment>dev</comment>
        </lock>
      </wc-status>
    </entry>
    <entry path="C:/wc/b.txt">
      <wc-status item="normal" revision="3" props="none"/>
    </entry>
  </target>
</status>'''
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch('tools.svn_workspace.run_svn', return_value={'returncode': 0, 'output': xml}):
                locks = working_copy_locks(tmp)
        # 相对路径会按 abs/relpath 计算；path 是绝对路径时 relpath 到 tmp 可能跳出
        # 用可控 root + 相对 entry path 再测一次
        xml2 = '''<?xml version="1.0" encoding="UTF-8"?>
<status>
  <target path=".">
    <entry path="a.txt">
      <wc-status item="normal" revision="3" props="none">
        <lock>
          <token>opaquelocktoken:abc</token>
          <owner>lihp</owner>
          <created>2026-07-22T01:00:00.000000Z</created>
        </lock>
      </wc-status>
    </entry>
    <entry path="b.txt">
      <wc-status item="normal" revision="3" props="none"/>
    </entry>
  </target>
</status>'''
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch('tools.svn_workspace.run_svn', return_value={'returncode': 0, 'output': xml2}):
                locks = working_copy_locks(tmp)
            self.assertIn('a.txt', locks)
            self.assertEqual(locks['a.txt']['owner'], 'lihp')
            self.assertNotIn('b.txt', locks)

    def test_lock_unlock_commit_revert_call_run_svn(self):
        with tempfile.TemporaryDirectory() as tmp:
            f1 = os.path.join(tmp, 'a.txt')
            f2 = os.path.join(tmp, 'b.txt')
            for path in (f1, f2):
                with open(path, 'w', encoding='utf-8') as stream:
                    stream.write('1')
            with mock.patch('tools.svn_workspace.run_svn', return_value={'returncode': 0, 'output': 'ok'}) as run:
                locked = lock_files([f1, f2], message='test lock')
                self.assertEqual(locked['count'], 2)
                self.assertTrue(run.called)
                unlocked = unlock_files([f1, f2])
                self.assertEqual(unlocked['count'], 2)
                reverted = revert_paths([f1, f2])
                self.assertEqual(reverted['count'], 2)
            with mock.patch('tools.svn_workspace.run_svn', return_value={'returncode': 0, 'output': 'Committed revision 1.'}):
                with mock.patch('tools.svn_workspace.working_copy_info', return_value={'local_path': tmp}):
                    with mock.patch('tools.svn_workspace.svn_status', return_value={'text': 'clean', 'clean': True, 'changes': [], 'returncode': 0}):
                        committed = commit_paths([f1, f2], 'msg', working_copy=tmp)
            self.assertEqual(committed['count'], 2)


@unittest.skipUnless(shutil.which('svn'), 'SVN command line is unavailable')
class SvnBatchIntegrationTests(unittest.TestCase):
    def test_multi_lock_unlock_roundtrip(self):
        svnadmin = os.path.join(os.path.dirname(shutil.which('svn')), 'svnadmin.exe')
        if not os.path.isfile(svnadmin):
            self.skipTest('svnadmin is unavailable')
        from tools.svn_workspace import add_text_file, checkout, commit_working_copy, run_svn

        with tempfile.TemporaryDirectory() as temp:
            repository = os.path.join(temp, 'repository')
            subprocess_run = __import__('subprocess').run
            subprocess_run([svnadmin, 'create', repository], check=True)
            url = Path(repository).as_uri()
            run_svn(['mkdir', f'{url}/trunk', '-m', 'init'])
            wc = os.path.join(temp, 'wc')
            checkout(f'{url}/trunk', wc)
            a = add_text_file(wc, 'a.txt', 'a')
            b = add_text_file(wc, 'b.txt', 'b')
            commit_working_copy(wc, 'add two files')
            result = lock_files([a, b], message='batch lock')
            self.assertIn('lock', result['output'].casefold())
            result = unlock_files([a, b])
            self.assertIn('unlock', result['output'].casefold())


if __name__ == '__main__':
    unittest.main()
