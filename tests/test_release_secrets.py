# -*- coding: utf-8 -*-
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.personal_knowledge import load_seed_entries


class ReleaseSeedSafetyTests(unittest.TestCase):
    def test_seed_files_are_safe_templates(self):
        txt = os.path.join(ROOT, 'resources', 'private_knowledge_seed.txt')
        js = os.path.join(ROOT, 'resources', 'private_knowledge_seed_workbooks.json')
        self.assertTrue(os.path.isfile(txt))
        self.assertTrue(os.path.isfile(js))
        with open(txt, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('安全空模板', text)
        self.assertNotIn('eyJ', text)
        self.assertNotIn('jdbc:oracle', text.lower())
        # 不应再出现历史真实口令片段特征
        self.assertNotIn('D56ak', text)
        self.assertNotIn('Jccs@#', text)
        with open(js, encoding='utf-8') as f:
            content = f.read().strip()
        self.assertIn(content[:1], ('[', '{'))
        if content.startswith('['):
            self.assertEqual(content.replace(' ', '').replace('\n', ''), '[]')

    def test_load_seed_entries_no_crash_and_no_jwt(self):
        entries = load_seed_entries()
        blob = '\n'.join(str(e.get('content', '')) + str(e.get('title', '')) for e in entries)
        self.assertNotIn('eyJhbGci', blob)
        self.assertNotIn('D56akVtSw', blob)

    def test_scan_script_passes_on_repo_resources(self):
        script = os.path.join(ROOT, 'scripts', 'scan_release_secrets.py')
        self.assertTrue(os.path.isfile(script))
        # 直接调用 scan 函数
        sys.path.insert(0, os.path.join(ROOT, 'scripts'))
        import scan_release_secrets as scan_mod
        code = scan_mod.scan(ROOT, ['resources'], strict=False)
        self.assertEqual(code, 0, 'resources/ must pass secret scan')


if __name__ == '__main__':
    unittest.main()
