# -*- coding: utf-8 -*-
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.sql_guard import is_read_query, reject_reason
from ui.navigation_model import NAV_ITEMS, display_name


class SqlGuardTests(unittest.TestCase):
    def test_allows_select_and_with(self):
        self.assertTrue(is_read_query('select * from prpCmain'))
        self.assertTrue(is_read_query('WITH x AS (SELECT 1 a FROM dual) SELECT * FROM x'))
        self.assertTrue(is_read_query('-- comment\nSELECT 1 FROM dual'))

    def test_rejects_mutations(self):
        self.assertFalse(is_read_query('delete from prpCmain'))
        self.assertFalse(is_read_query('UPDATE prpCmain SET a=1'))
        self.assertFalse(is_read_query('drop table prpCmain'))
        self.assertIn('DELETE', reject_reason('delete from t'))

    def test_redis_and_mongo_read_only(self):
        self.assertEqual(reject_reason('GET user:1', 'redis'), '')
        self.assertEqual(reject_reason('SCAN 0 MATCH * COUNT 20', 'redis'), '')
        self.assertIn('DEL', reject_reason('DEL user:1', 'redis'))
        self.assertIn('SET', reject_reason('SET k v', 'redis'))
        self.assertEqual(reject_reason('{"collection":"user","filter":{}}', 'mongodb'), '')
        self.assertIn('drop', reject_reason('db.user.drop()', 'mongodb').lower())

    def test_oracledb_thin_crypto_imports(self):
        from cryptography.hazmat.primitives.kdf import pbkdf2
        self.assertTrue(hasattr(pbkdf2, 'PBKDF2HMAC'))

    def test_nav_14_is_workbench(self):
        self.assertEqual(display_name(14, 'zh'), '模型工作台')
        self.assertIn(14, NAV_ITEMS)


if __name__ == '__main__':
    unittest.main()
