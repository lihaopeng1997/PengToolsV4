from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import sqlite3
import threading
import unittest

from tools.data_center.changeset import QmarkSqlDialect
from tools.data_center.changeset_dialects import create_changeset_dialect
from tools.data_center.manual_session import ManualSessionError, ManualSessionState
from tools.data_center.table_source import (
    TableFilter, TableOrder, TableQuerySpec, TableSourceError,
    build_table_query,
)
from tests import test_data_center_table_source as source_fixture


class TableQueryTests(unittest.TestCase):
    def _open(self, provider="mysql", query=TableQuerySpec()):
        helper = source_fixture.TableSourceTests()
        service, tree_target, node, _view, _host = helper._tree(provider)
        self.addCleanup(lambda: service.close().result(timeout=3))
        choice, target = helper._choice_target(tree_target)
        driver = source_fixture._Driver(provider, node.name, "order view", rows=[(i, "a", Decimal("4.20")) for i in range(130)])
        if provider.endswith("oracle"):
            driver.oracle_columns = [(target.schema, *row[1:]) for row in driver.oracle_columns]
            driver.rows = [(i, "note") for i in range(130)]
        session = helper._session(target, driver)
        self.addCleanup(lambda: session.close(disposition="rollback").result(timeout=3))
        result = session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice, query=query).result(timeout=3)
        return result, session, driver, choice, (service, tree_target, node)

    def test_both_vendor_bind_styles_and_literal_like_escaping(self):
        for provider, text_column in (("mysql", "part"), ("oracle", "note"), ("oceanbase_mysql", "part"), ("oceanbase_oracle", "note")):
            with self.subTest(provider=provider):
                result, _session, _driver, choice, _scope = self._open(provider)
                query = TableQuerySpec((TableFilter(text_column, "contains", "a%_!' OR 1=1 --"), TableFilter("id", ">=", 60), TableFilter(text_column, "IS NOT NULL")), (TableOrder(text_column, True),))
                sql, parameters = build_table_query(result.metadata, create_changeset_dialect(choice, result.metadata.target), query)
                self.assertIn(" WHERE ", sql)
                self.assertIn("LIKE ", sql)
                self.assertIn("ESCAPE '!'", sql)
                self.assertNotIn("OR 1=1", sql)
                values = tuple(parameters.values()) if isinstance(parameters, dict) else parameters
                self.assertEqual(values, ("%a!%!_!!' OR 1=1 --%", 60))
                if provider.endswith("mysql"):
                    self.assertIn("%s", sql)
                    self.assertIn(f"ORDER BY `{text_column}` DESC, `id`", sql)
                else:
                    self.assertIn(":1" if provider == "oceanbase_oracle" else ":p0", sql)
                    self.assertIn(f'ORDER BY "{text_column}" DESC, "id"', sql)

    def test_compiled_query_filters_entire_data_and_orders_before_paging(self):
        result, _session, _driver, _choice, _scope = self._open()
        metadata = replace(result.metadata, target=replace(result.metadata.target, database="", schema=""), signed_ref=replace(result.metadata.signed_ref, raw_name="items"))
        query = TableQuerySpec((TableFilter("id", ">=", 60), TableFilter("part", "contains", "%_!")), (TableOrder("amount", True),))
        sql, parameters = build_table_query(metadata, QmarkSqlDialect(), query)
        with sqlite3.connect(":memory:") as connection:
            connection.execute('CREATE TABLE items (id INTEGER, part TEXT, amount INTEGER)')
            connection.executemany('INSERT INTO items VALUES (?, ?, ?)', [(i, "%_!" if i % 2 == 0 else "other", i % 3) for i in range(120)])
            actual = connection.execute(sql, parameters).fetchall()
        expected = [(i, "%_!", i % 3) for i in range(60, 120, 2)]
        expected.sort(key=lambda row: (-row[2], row[0], row[1]))
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 30)

    def test_invalid_columns_operators_duplicate_orders_and_limits_are_rejected(self):
        result, _session, _driver, choice, _scope = self._open()
        dialect = create_changeset_dialect(choice, result.metadata.target)
        invalid = (
            TableQuerySpec((TableFilter("id; DROP TABLE items", "=", 1),)),
            TableQuerySpec((TableFilter("id", "OR 1=1", 1),)),
            TableQuerySpec((TableFilter("id", "IS NULL", "not null"),)),
            TableQuerySpec((TableFilter("id", "=", object()),)),
            TableQuerySpec((TableFilter("id", "=", float("nan")),)),
            TableQuerySpec((TableFilter("id", "=", Decimal("Infinity")),)),
            TableQuerySpec((TableFilter("part", "=", "x" * 16385),)),
            TableQuerySpec(tuple(TableFilter("id", "=", i) for i in range(33))),
            TableQuerySpec(orders=(TableOrder("id"), TableOrder("id", True))),
            TableQuerySpec(orders=(TableOrder("unknown"),)),
        )
        for query in invalid:
            with self.subTest(query_type=type(query)):
                with self.assertRaises(TableSourceError):
                    build_table_query(result.metadata, dialect, query)
        column = replace(result.metadata.columns[1], comparable=False)
        metadata = replace(result.metadata, columns=(result.metadata.columns[0], column, result.metadata.columns[2]))
        with self.assertRaises(TableSourceError):
            build_table_query(metadata, dialect, TableQuerySpec((TableFilter("part", "=", "x"),)))
        sql, parameters = build_table_query(metadata, dialect, TableQuerySpec((TableFilter("part", "IS NULL"),)))
        self.assertIn("`part` IS NULL", sql)
        self.assertEqual(parameters, ())

    def test_owner_reuses_dirty_session_and_same_filtered_cursor_for_next_page(self):
        _initial, session, driver, choice, scope = self._open()
        session.submit("UPDATE existing SET x = 1").result(timeout=3)
        query = TableQuerySpec((TableFilter("id", ">", 5),), (TableOrder("amount", True),))
        service, tree_target, node = scope
        result = session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice, page_size=50, query=query).result(timeout=3)
        count = len([event for event in driver.events if event[0] == "execute"])
        next_page = session.fetch_next_page(50).result(timeout=3)
        self.assertEqual(result.query, query)
        self.assertEqual(result.page.query_id, next_page.query_id)
        self.assertEqual([row[0] for row in result.page.rows + next_page.rows], list(range(100)))
        self.assertEqual(len([event for event in driver.events if event[0] == "execute"]), count)
        sql, params = [(event[2], event[3]) for event in driver.events if event[0] == "execute" and " WHERE `id` > " in event[2]][0]
        self.assertIn("ORDER BY `amount` DESC, `id`, `part`", sql)
        self.assertEqual(params, (5,))
        self.assertEqual(session.state, ManualSessionState.DIRTY)
        self.assertEqual((driver.commit_count, driver.rollback_count), (0, 0))
        owners = {event[1] for event in driver.events}
        self.assertEqual(len(owners), 1)
        self.assertNotIn(threading.get_ident(), owners)
        before = len(driver.events)
        with self.assertRaises(ManualSessionError):
            session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice, query=TableQuerySpec((TableFilter("id", "DROP", 1),))).result(timeout=3)
        self.assertEqual(len(driver.events), before)
        with self.assertRaises(ManualSessionError) as error:
            session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice, query=TableQuerySpec((TableFilter("removed_column", "=", 1),))).result(timeout=3)
        self.assertEqual(error.exception.code, "TABLE_QUERY_COLUMN_UNAVAILABLE")
        self.assertEqual(session.state, ManualSessionState.DIRTY)


if __name__ == "__main__":
    unittest.main()
