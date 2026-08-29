# -*- coding: utf-8 -*-
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from panels.ai_workbench_panel import compose_nl_query
from tools.db_redis_ops import build_key_tree, filter_keys_by_pattern
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
        # v3.0：15 为"模型"父级，16=聊天，17=工作，18–23 六数据库面板
        self.assertEqual(display_name(15, 'zh'), '模型')
        self.assertIn(15, NAV_ITEMS)
        self.assertEqual(display_name(16, 'zh'), '聊天')
        self.assertEqual(display_name(17, 'zh'), '工作')
        self.assertEqual(display_name(18, 'zh'), 'Oracle')
        self.assertEqual(display_name(22, 'zh'), 'Redis')
        self.assertEqual(display_name(23, 'zh'), 'MongoDB')

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

    def test_build_key_tree_key_folder_name_collision(self):
        """key 与文件夹同名（a:b 与 a:b:c 共存）时，key 节点不得被复用为文件夹。"""
        tree = build_key_tree(['a:b', 'a:b:c'])
        keys = []

        def walk(nodes):
            for n in nodes:
                if not n.get('is_folder'):
                    keys.append((n['name'], n['full']))
                walk(n.get('children', []))

        walk(tree)
        self.assertIn(('b', 'a:b'), keys)      # 独立 key 节点 b（完整名 a:b）
        self.assertIn(('c', 'a:b:c'), keys)    # 文件夹 a:b 下的子 key
        self.assertEqual(len(keys), 2)          # 不产生多余/缺失节点

    def test_build_key_tree_same_name_folder_and_key_distinct(self):
        """device:001 与 device:001:status 共存：001 既是 key 也是文件夹，二者独立显示。"""
        tree = build_key_tree(['device:001', 'device:001:status', 'device:002:status'])
        self.assertEqual(tree[0]['name'], 'device')
        self.assertTrue(tree[0]['is_folder'])
        children = tree[0]['children']
        names = [c['name'] for c in children]
        self.assertIn('001', names)             # key 001
        self.assertIn('002', names)             # 文件夹 002:
        # 001 作为 key 节点必须无子节点（不被复用为文件夹）
        for c in children:
            if c['name'] == '001' and not c['is_folder']:
                self.assertEqual(c['children'], [])
                self.assertEqual(c['full'], 'device:001')
        # 001 作为文件夹必须含 status
        for c in children:
            if c['name'] == '001' and c['is_folder']:
                child_names = [cc['name'] for cc in c['children']]
                self.assertIn('status', child_names)

    def test_build_key_tree_flat_and_filter(self):
        tree = build_key_tree(['foo', 'bar'])
        self.assertEqual(len(tree), 2)
        self.assertTrue(all(not c['is_folder'] for c in tree))
        self.assertEqual(filter_keys_by_pattern(['user:1', 'user:2', 'order:1'], 'user:*'), ['user:1', 'user:2'])
        self.assertEqual(filter_keys_by_pattern(['user:1'], 'order:*'), [])


if __name__ == '__main__':
    unittest.main()
