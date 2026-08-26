# -*- coding: utf-8 -*-
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from panels.ai_workbench_panel import compose_nl_query
from tools.sql_guard import classify_statement, is_read_query, redact_error, reject_reason, split_sql_statements, statement_at_cursor
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

    def test_compose_nl_query_inserts_table_and_columns(self):
        self.assertEqual(compose_nl_query('prpCmain'), '帮我查询表 prpCmain 的数据')
        self.assertEqual(
            compose_nl_query('prpCmain', ['POLICYNO', 'INSUREDNAME']),
            '帮我查询表 prpCmain 的字段 POLICYNO、INSUREDNAME',
        )

    def test_nav_14_is_sql_console(self):
        self.assertEqual(display_name(14, 'zh'), 'SQL 控制台')
        self.assertIn(14, NAV_ITEMS)
        self.assertEqual(display_name(15, 'zh'), '模型对话')
        self.assertIn(15, NAV_ITEMS)

    def test_split_ignores_semicolon_in_strings_and_comments(self):
        parts = split_sql_statements("SELECT 'a;b' FROM dual; -- x;y\nSELECT 2 FROM dual")
        self.assertEqual(len(parts), 2)
        self.assertIn("'a;b'", parts[0])
        self.assertIn('SELECT 2', parts[1])
        sql = "SELECT 1 FROM dual; SELECT 2 FROM dual"
        self.assertEqual(statement_at_cursor(sql, 0), 'SELECT 1 FROM dual')
        self.assertEqual(statement_at_cursor(sql, len(sql)), 'SELECT 2 FROM dual')

    def test_classify_write_needs_confirm(self):
        dml = classify_statement('delete from t', 'oracle')
        self.assertTrue(dml['needs_confirm'])
        self.assertEqual(dml['category'], 'dml')
        ddl = classify_statement('alter table t add (a int)', 'oracle')
        self.assertEqual(ddl['category'], 'ddl')
        self.assertTrue(classify_statement('SET k v', 'redis')['needs_confirm'])
        self.assertFalse(classify_statement('GET k', 'redis')['needs_confirm'])
        with_dml = classify_statement('WITH x AS (SELECT 1 a FROM dual) DELETE FROM t', 'oracle')
        self.assertEqual(with_dml['category'], 'dml')
        self.assertTrue(with_dml['needs_confirm'])
        from tools.sql_guard import ai_draft_safety
        safety = ai_draft_safety('INSERT INTO t VALUES (1); SELECT 1 FROM dual')
        self.assertTrue(safety['fail_closed'])

    def test_redact_error_hides_secrets(self):
        text = redact_error('password=secret token=abc Bearer xyz http://u:p@10.0.0.1/v1')
        self.assertNotIn('secret', text)
        self.assertNotIn('abc', text)
        self.assertIn('***', text)


if __name__ == '__main__':
    unittest.main()
