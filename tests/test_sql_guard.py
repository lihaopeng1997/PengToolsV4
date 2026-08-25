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

    def test_nav_14_is_workbench(self):
        self.assertEqual(display_name(14, 'zh'), '模型工作台')
        self.assertIn(14, NAV_ITEMS)


if __name__ == '__main__':
    unittest.main()
