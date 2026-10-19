"""Scoped loaded metadata and local SQL alias completion; synthetic names only."""

from dataclasses import replace
import unittest
from unittest.mock import patch

from tools.data_center.completion_cache import CompletionCatalog
from tools.data_center.manual_session import ManualSessionTarget
from tools.data_center.object_tree import TreePage, TreeTarget, VerifiedTableRef
from tools.data_center.sql_completion import completion_candidates, current_token
from tools.data_center.table_source import TableColumnMetadata, TableSourceMetadata


def metadata(ref, *names, tab="tab", schema=None):
    target = ManualSessionTarget(tab, ref.target.connection_id, ref.target.profile_revision,
                                database=ref.database, schema=ref.schema if schema is None else schema)
    columns = tuple(TableColumnMetadata(name, "varchar", True, False, False, False, True, False) for name in names)
    return TableSourceMetadata(target, ref, columns, (), False, None, "Read-only view")


def ref_for(name="users", *, database="db", schema="", provider="mysql", revision="rev", epoch=0, generation=1):
    target = TreeTarget("temp", revision, provider, database=database)
    return VerifiedTableRef(target, name + str(epoch), generation, epoch, "table", name, database, schema)


def page_for(ref, parent="root"):
    return TreePage(ref.target, parent, (), None, ref.tree_generation, ref.refresh_epoch, 0)


class CompletionTests(unittest.TestCase):
    def test_loaded_columns_are_isolated_by_revision_database_schema_and_provider(self):
        catalog = CompletionCatalog()
        ref = ref_for()
        value = metadata(ref, "id", "name", schema="db")  # MySQL's equivalent empty/schema=db forms.
        self.assertTrue(catalog.learn_table(value))
        snap = catalog.snapshot(value.target, "mysql")
        self.assertEqual(snap['objects'][0]['columns'], [{'name': 'id'}, {'name': 'name'}])
        for change in ({'connection_id': 'other'}, {'profile_revision': 'old'}, {'database': 'other'}):
            self.assertEqual(catalog.snapshot(replace(value.target, **change), "mysql")['objects'], [])
        self.assertEqual(catalog.snapshot(value.target, "oracle")['objects'], [])
        oracle = metadata(ref_for("ITEMS", database="svc", schema="APP", provider="oracle"), "ID")
        catalog.reset(oracle.signed_ref.target, 1)
        catalog.learn_table(oracle)
        self.assertEqual(len(catalog.snapshot(oracle.target, "oracle")['objects']), 1)
        self.assertEqual(catalog.snapshot(replace(oracle.target, schema="OTHER"), "oracle")['objects'], [])
        self.assertEqual(catalog.snapshot(replace(oracle.target, schema=""), "oracle")['objects'], [])
        # Whole-server MySQL trees retain separate database children.
        global_target = replace(ref.target, database="")
        catalog.reset(global_target, 1)
        one = replace(ref, target=global_target)
        two = replace(one, node_ref="two", database="another_db")
        catalog.accept_page(page_for(one), [one, two])
        self.assertEqual(len(catalog.snapshot(value.target, "mysql")['objects']), 1)
        self.assertEqual(len(catalog.snapshot(replace(value.target, database="another_db", schema="another_db"), "mysql")['objects']), 1)

    def test_refresh_invalidates_columns_and_rejects_late_old_generation_or_epoch(self):
        catalog = CompletionCatalog(allow_unseeded=False)
        ref = ref_for()
        catalog.reset(ref.target, 1)
        self.assertTrue(catalog.accept_page(page_for(ref), [ref]))
        self.assertTrue(catalog.learn_table(metadata(ref, "old_column")))
        fresh = ref_for(epoch=1)
        catalog.accept_page(page_for(fresh), [fresh])
        self.assertFalse(catalog.learn_table(metadata(ref, "old_column")))
        self.assertFalse(catalog.accept_page(page_for(ref), [ref]))
        self.assertEqual(catalog.snapshot(metadata(fresh).target, "mysql")['objects'][0]['columns'], [])
        self.assertTrue(catalog.learn_table(metadata(fresh, "new_column")))
        empty_page = replace(page_for(fresh), refresh_epoch=2)
        catalog.accept_page(empty_page, [])
        self.assertFalse(catalog.learn_table(metadata(fresh, "late_removed_table")))
        catalog.reset(fresh.target, 2)
        self.assertFalse(catalog.learn_table(metadata(fresh, "late")))
        catalog.reset()
        self.assertFalse(catalog.learn_table(metadata(fresh, "late")))

    def test_paged_names_preserve_raw_identity_and_respect_retention_limits(self):
        catalog = CompletionCatalog()
        raw = "x\n" + "a" * 270
        ref = ref_for(raw)
        catalog.reset(ref.target, 1)
        catalog.accept_page(page_for(ref), [ref])
        second = ref_for("next_page")
        catalog.accept_page(page_for(second), [second], request_cursor="cursor")
        self.assertEqual([o['name'] for o in catalog.snapshot(metadata(ref).target, "mysql")['objects']], [raw, "next_page"])
        with patch("tools.data_center.completion_cache.MAX_COMPLETION_TABLES", 2):
            for name in ("three", "four", "five"):
                item = ref_for(name)
                catalog.accept_page(page_for(item), [item], request_cursor="cursor")
            self.assertEqual([o['name'] for o in catalog.snapshot(metadata(ref).target, "mysql")['objects']], ["four", "five"])
        with patch("tools.data_center.completion_cache.MAX_COMPLETION_BYTES", 50):
            catalog.learn_table(metadata(ref_for("five"), "very_long_column_name" * 4))
            self.assertLessEqual(catalog._bytes, 50)

    @staticmethod
    def _complete(sql, objects, dialect="mysql"):
        position = sql.index("|")
        sql = sql.replace("|", "")
        prefix, _, _ = current_token(sql, position)
        return completion_candidates({'dialect': dialect, 'objects': objects}, sql, position, prefix, ("SELECT", "FROM"))

    def test_aliases_join_statements_comments_and_nested_query_depth(self):
        objects = [{'owner': 'db', 'name': 'users', 'columns': [{'name': 'name'}, {'name': 'id'}]},
                   {'owner': 'db', 'name': 'orders', 'columns': [{'name': 'amount'}]}]
        for sql in ("SELECT u.na| FROM users u", "SELECT u.na| FROM db.users AS u JOIN orders o ON u.id=o.id",
                    "SELECT u.na| FROM /*comment*/ users /*comment*/ AS u",
                    "SELECT x.id FROM (SELECT u.na| FROM users u) x",
                    "SELECT 'FROM orders u'; SELECT u.na| FROM users u"):
            self.assertEqual(self._complete(sql, objects), ['u.`name`'], sql)
        self.assertEqual(self._complete("SELECT o.a| FROM users u JOIN orders o ON u.id=o.id", objects), ['o.`amount`'])
        for sql in ("SELECT 'u.na|' FROM users u", "SELECT 1 -- u.na|\n FROM users u",
                    "SELECT u.na| FROM orders u", "SELECT u.na| FROM users u JOIN orders u ON 1=1",
                    "SELECT u.na| FROM (SELECT name FROM users) u"):
            self.assertEqual(self._complete(sql, objects), [], sql)

    def test_quoted_identifiers_schema_objects_and_ambiguous_names(self):
        objects = [{'owner': 'Owner', 'name': 'Mixed Table', 'columns': [{'name': 'Name'}, {'name': 'a"b'}]}]
        self.assertEqual(self._complete('SELECT u.na| FROM "Owner"."Mixed Table" u', objects, 'oracle'), ['u."Name"'])
        self.assertEqual(self._complete('SELECT "Owner"."Mixed Table".a| FROM dual', objects, 'oracle'), ['"Owner"."Mixed Table"."a""b"'])
        self.assertEqual(self._complete('SELECT * FROM "Owner".Mi|', objects, 'oracle'), ['"Owner"."Mixed Table"'])
        self.assertEqual(self._complete('SELECT u.na| FROM "Owner"."Mixed Table" "u"', objects, 'oracle'), [])
        self.assertEqual(self._complete('SELECT "u".na| FROM "Owner"."Mixed Table" "u"', objects, 'oracle'), ['"u"."Name"'])
        objects = [{'owner': 'a', 'name': 'items', 'columns': [{'name': 'first'}]},
                   {'owner': 'b', 'name': 'items', 'columns': [{'name': 'second'}]}]
        self.assertEqual(self._complete('SELECT items.|', objects), [])
        self.assertEqual(self._complete('SELECT b.items.s|', objects), ['b.items.`second`'])
        objects = [{'name': 'users', 'columns': [{'name': 'table'}, {'name': 'a`b'}]}]
        self.assertEqual(self._complete('SELECT u.t| FROM users u', objects), ['u.`table`'])
        self.assertEqual(self._complete('SELECT u.a| FROM users u', objects), ['u.`a``b`'])
        self.assertEqual(self._complete('SELECT u.t| FROM users u', objects, 'generic'), ['u.table'])


if __name__ == "__main__":
    unittest.main()
