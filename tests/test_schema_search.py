# -*- coding: utf-8 -*-
# Deterministic tests for database unified schema object search.

import time
import unittest

from tools.schema_search import (
    build_schema_search_index,
    search_schema_index,
    get_matched_table_identities,
)


class SchemaSearchTests(unittest.TestCase):
    def setUp(self):
        self.sample_snapshot = {
            'snapshot_id': 'snap_001',
            'dialect': 'oracle',
            'objects': [
                {
                    'owner': 'PRPCAR',
                    'name': 'T_POLICY',
                    'object_type': 'TABLE',
                    'comment': '保单主表',
                    'columns': [
                        {'name': 'POLICY_NO', 'data_type': 'VARCHAR2(20)', 'comment': '保单号', 'primary_key': True},
                        {'name': 'APPLY_DATE', 'data_type': 'DATE', 'comment': '投保日期'},
                        {'name': 'SUM_PREMIUM', 'data_type': 'NUMBER(14,2)', 'comment': '总保费'},
                    ],
                },
                {
                    'owner': 'PRPCAR',
                    'name': 'T_CLAIM_POLICY',
                    'object_type': 'TABLE',
                    'comment': '理赔保单关联表',
                    'columns': [
                        {'name': 'CLAIM_NO', 'data_type': 'VARCHAR2(20)', 'comment': '理赔号', 'primary_key': True},
                        {'name': 'POLICY_NO', 'data_type': 'VARCHAR2(20)', 'comment': '关联保单号'},
                    ],
                },
                {
                    'owner': 'PRPPH',
                    'name': 'T_CUSTOMER',
                    'object_type': 'TABLE',
                    'comment': '客户信息表',
                    'columns': [
                        {'name': 'CUSTOMER_ID', 'data_type': 'VARCHAR2(32)', 'comment': '主键'},
                        {'name': 'CUSTOMER_NAME', 'data_type': 'VARCHAR2(100)', 'comment': '中文名称'},
                    ],
                },
            ],
        }
        self.index = build_schema_search_index(self.sample_snapshot)

    def test_table_exact_match(self):
        results = search_schema_index(self.index, 'T_POLICY')
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['kind'], 'table')
        self.assertEqual(results[0]['table_comment'], '保单主表')
        self.assertEqual(results[0]['table_name'], 'T_POLICY')

    def test_table_prefix_match(self):
        results = search_schema_index(self.index, 'T_POLIC')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('PRPCAR.T_POLICY', titles)

    def test_table_substring_match(self):
        results = search_schema_index(self.index, 'customer')
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['table_name'], 'T_CUSTOMER')

    def test_case_insensitive(self):
        r1 = search_schema_index(self.index, 'policy')
        r2 = search_schema_index(self.index, 'POLICY')
        r3 = search_schema_index(self.index, 'Policy')
        self.assertEqual([item['title'] for item in r1], [item['title'] for item in r2])
        self.assertEqual([item['title'] for item in r1], [item['title'] for item in r3])

    def test_owner_schema_match(self):
        results = search_schema_index(self.index, 'PRPPH')
        self.assertGreater(len(results), 0)
        schema_hits = [r for r in results if r['kind'] == 'schema']
        self.assertGreater(len(schema_hits), 0)
        self.assertEqual(schema_hits[0]['owner'], 'PRPPH')

    def test_table_chinese_comment_match(self):
        results = search_schema_index(self.index, '客户')
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['table_name'], 'T_CUSTOMER')

    def test_field_name_match(self):
        results = search_schema_index(self.index, 'apply_date')
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['kind'], 'field')
        self.assertEqual(results[0]['table_name'], 'T_POLICY')
        self.assertEqual(results[0]['field_name'], 'APPLY_DATE')

    def test_field_chinese_comment_match(self):
        results = search_schema_index(self.index, '投保日期')
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['kind'], 'field')
        self.assertEqual(results[0]['table_name'], 'T_POLICY')
        self.assertEqual(results[0]['field_name'], 'APPLY_DATE')

    def test_multiple_tables_same_field_cross_search(self):
        results = search_schema_index(self.index, 'policy_no')
        field_hits = [r for r in results if r['kind'] == 'field']
        self.assertGreaterEqual(len(field_hits), 2)
        tables = [r['table_name'] for r in field_hits]
        self.assertIn('T_POLICY', tables)
        self.assertIn('T_CLAIM_POLICY', tables)

    def test_deterministic_ranking(self):
        # Exact identifier should rank above substring and comment
        results = search_schema_index(self.index, 'POLICY_NO')
        self.assertEqual(results[0]['kind'], 'field')
        self.assertEqual(results[0]['field_name'], 'POLICY_NO')

        # Moreover, running search 10 times produces identical list
        lists = [[r['title'] for r in search_schema_index(self.index, 'policy')] for _ in range(10)]
        for l in lists[1:]:
            self.assertEqual(l, lists[0])

    def test_empty_query(self):
        self.assertEqual(search_schema_index(self.index, ''), [])
        self.assertEqual(search_schema_index(self.index, '   '), [])

    def test_result_limit(self):
        results = search_schema_index(self.index, 'policy', limit=2)
        self.assertLessEqual(len(results), 2)

    def test_redis_mongo_object_without_columns(self):
        redis_snap = {
            'snapshot_id': 'redis_01',
            'dialect': 'redis',
            'objects': [
                {'owner': '', 'name': 'user:session:1001', 'object_type': 'STRING', 'comment': '', 'columns': []},
                {'owner': '', 'name': 'order:queue', 'object_type': 'LIST', 'comment': '订单列表', 'columns': []},
            ],
        }
        idx = build_schema_search_index(redis_snap)
        results = search_schema_index(idx, 'order')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['table_name'], 'order:queue')

    def test_empty_snapshot(self):
        idx = build_schema_search_index(None)
        self.assertEqual(search_schema_index(idx, 'test'), [])

    def test_malformed_incomplete_metadata_tolerated(self):
        malformed = {
            'objects': [
                None,
                'not_a_dict',
                {},
                {'name': 'TOLERATED', 'columns': [None, {}, {'name': 'OK'}]},
            ],
        }
        idx = build_schema_search_index(malformed)
        results = search_schema_index(idx, 'TOLERATED')
        self.assertGreater(len(results), 0)

    def test_get_matched_table_identities(self):
        matched = get_matched_table_identities(self.index, 'apply_date')
        self.assertIn(('prpcar', 't_policy'), matched)

    def test_synthetic_scale_performance_smoke(self):
        big_objects = []
        for t_idx in range(2000):
            cols = [
                {'name': f'COL_{c_idx}_{t_idx}', 'data_type': 'VARCHAR2(30)', 'comment': f'字段_{c_idx}'}
                for c_idx in range(20)
            ]
            big_objects.append({
                'owner': 'SCHE' + str(t_idx % 10),
                'name': f'T_TABLE_{t_idx}',
                'object_type': 'TABLE',
                'comment': f'表_{t_idx}',
                'columns': cols,
            })
        big_snap = {'snapshot_id': 'big_snap', 'objects': big_objects}

        t0 = time.perf_counter()
        idx = build_schema_search_index(big_snap)
        index_duration = time.perf_counter() - t0

        t1 = time.perf_counter()
        results = search_schema_index(idx, 'COL_10_500')
        search_duration = time.perf_counter() - t1

        self.assertGreater(len(results), 0)
        self.assertLess(index_duration, 1.0, f'Indexing took {index_duration:.4f}s, expected < 1.0s')
        self.assertLess(search_duration, 0.25, f'Searching took {search_duration:.4f}s, expected < 0.25s')


if __name__ == '__main__':
    unittest.main()
