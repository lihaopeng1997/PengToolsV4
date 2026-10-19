from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import threading
import unittest

from tools.data_center.manual_session import (
    ManualSession,
    ManualSessionError,
    ManualSessionState,
    ManualSessionTarget,
    StatementClassification,
    StatementKind,
    TransactionEffect,
)
from tools.data_center.host_metadata import MetadataTarget
from tools.data_center.object_tree import ObjectTreeError, ObjectTreeService, TreeNode, TreeTarget, VerifiedTableRef
from tools.data_center.profile_host import ConnectionChoice
from tools.data_center.table_source import TableSourceError, validate_table_target


class _DirectoryLease:
    def __init__(self, host: "_DirectoryHost", target: MetadataTarget) -> None:
        self.host = host
        self.target = target
        self.dialect = "mysql" if target.provider.endswith("mysql") else "oracle"

    def execute_readonly(self, request, **_kwargs):
        self.host.directory_calls.append((threading.get_ident(), request["sql"], tuple(request["parameters"])))
        sql = request["sql"].casefold()
        offset = int(request["parameters"][-1]) if request["parameters"] else 0
        if "information_schema.schemata" in sql:
            rows = [(self.host.database,)]
        elif "information_schema.tables" in sql:
            rows = [(self.host.table_name, "BASE TABLE"), (self.host.view_name, "VIEW")]
        elif "select distinct owner" in sql:
            rows = [(self.host.schema,)]
        elif "select object_name, object_type" in sql:
            rows = [(self.host.table_name, "TABLE"), (self.host.view_name, "VIEW")]
        else:
            rows = []
        return {"columns": (), "rows": rows[offset : offset + int(request["row_limit"])]}

    def close(self):
        self.host.directory_closed_threads.append(threading.get_ident())


class _DirectoryFactory:
    def __init__(self, host: "_DirectoryHost", target: MetadataTarget) -> None:
        self.host = host
        self.target = target

    def __call__(self, connection_id):
        del connection_id
        self.host.directory_factory_threads.append(threading.get_ident())
        return _DirectoryLease(self.host, self.target)


class _DirectoryHost:
    def __init__(self, provider: str, mode: str, database: str, schema: str, table_name: str, view_name: str) -> None:
        self.provider = provider
        self.mode = mode
        self.database = database
        self.schema = schema
        self.table_name = table_name
        self.view_name = view_name
        self.directory_calls = []
        self.directory_factory_threads = []
        self.directory_closed_threads = []

    def profile_snapshot(self, connection_id):
        return type("Snapshot", (), {
            "connection_id": connection_id,
            "profile_revision": "rev-1",
            "provider": self.provider,
            "mode": self.mode,
            "database": self.database,
        })()

    def create_factory(self, connection_id, profile_revision, provider, mode, database_name, **_kwargs):
        target = MetadataTarget(connection_id, profile_revision, provider, mode, database_name, ())
        return _DirectoryFactory(self, target)


class _Cursor:
    def __init__(self, driver: "_Driver", rows=(), description=None, rowcount=0) -> None:
        self.driver = driver
        self.rows = list(rows)
        self.description = description
        self.rowcount = rowcount
        self.offset = 0
        self.closed = False

    def fetchmany(self, size):
        self.driver.events.append(("fetchmany", threading.get_ident(), size))
        fetch_size = min(size, self.driver.max_fetch_size or size)
        rows = self.rows[self.offset : self.offset + fetch_size]
        self.offset += len(rows)
        return rows

    def close(self):
        self.driver.events.append(("cursor_close", threading.get_ident()))
        self.closed = True


class _Driver:
    def __init__(
        self,
        provider: str,
        table_name: str,
        view_name: str,
        *,
        table_type: str = "BASE TABLE",
        mysql_columns=None,
        mysql_keys=None,
        oracle_columns=None,
        oracle_keys=None,
        rows=None,
        block_on: str = "",
        fail_on: str = "",
    ) -> None:
        self.provider = provider
        self.table_name = table_name
        self.view_name = view_name
        self.table_type = table_type
        self.mysql_columns = list(mysql_columns if mysql_columns is not None else [
            ("sales db", table_name, "id", "bigint", "bigint", "NO", 0, "", 1),
            ("sales db", table_name, "part", "varchar(12)", "varchar", "NO", 0, "", 2),
            ("sales db", table_name, "amount", "decimal(10,2)", "decimal", "YES", 0, "", 3),
        ])
        self.mysql_keys = list(mysql_keys if mysql_keys is not None else [("id", 1), ("part", 2)])
        self.oracle_columns = list(oracle_columns if oracle_columns is not None else [
            ("Quoted Owner", table_name, "id", "NUMBER", 22, 10, 0, "N", 0, "NO", "NO", 1),
            ("Quoted Owner", table_name, "note", "VARCHAR2", 80, None, None, "Y", 0, "NO", "NO", 2),
        ])
        self.oracle_keys = list(oracle_keys if oracle_keys is not None else [("id", 1)])
        self.rows = list(rows if rows is not None else [(1, "a", Decimal("1.25")), (2, "b", None)])
        self.block_on = block_on.casefold()
        self.fail_on = fail_on.casefold()
        self.max_fetch_size = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.events = []
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    @staticmethod
    def _values(parameters):
        if isinstance(parameters, dict):
            return tuple(parameters[key] for key in sorted(parameters))
        return tuple(parameters or ())

    def configure_manual_commit(self):
        self.events.append(("configure", threading.get_ident()))

    def execute(self, sql, parameters):
        values = self._values(parameters)
        thread_id = threading.get_ident()
        self.events.append(("execute", thread_id, sql, parameters))
        lower = sql.casefold()
        if self.fail_on and self.fail_on in lower:
            raise RuntimeError("sensitive database diagnostic")
        if self.block_on and self.block_on in lower:
            self.started.set()
            if not self.release.wait(5):
                raise TimeoutError("fake driver unblock timed out")
        if "select table_type from information_schema.tables" in lower:
            kind = self.table_type if len(values) > 1 and values[1] == self.table_name else "VIEW"
            return _Cursor(self, [(kind,)])
        if "from information_schema.columns" in lower:
            return _Cursor(self, self.mysql_columns)
        if "from information_schema.key_column_usage" in lower:
            return _Cursor(self, self.mysql_keys)
        if "from all_objects" in lower:
            kind = "TABLE" if len(values) > 1 and values[1] == self.table_name else "VIEW"
            return _Cursor(self, [(kind,)])
        if "from all_tab_columns c" in lower:
            return _Cursor(self, self.oracle_columns)
        if "from all_constraints c" in lower:
            return _Cursor(self, self.oracle_keys)
        if lower.startswith("update "):
            return _Cursor(self, (), (), 1)
        description = (
            ("id", "BIGINT", None, None, None, 0, False),
            ("part", "VARCHAR", None, None, None, None, True),
            ("amount", "DECIMAL", None, None, 10, 2, True),
        )
        return _Cursor(self, self.rows, description, len(self.rows))

    def commit(self):
        self.commit_count += 1
        self.events.append(("commit", threading.get_ident()))

    def rollback(self):
        self.rollback_count += 1
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("connection_close", threading.get_ident()))

    def create_savepoint(self, _name):
        raise AssertionError("table browse must not create savepoints")

    def rollback_to_savepoint(self, _name):
        raise AssertionError("table browse must not roll back to savepoints")

    def release_savepoint(self, _name):
        raise AssertionError("table browse must not release savepoints")

    def ddl_transaction_behavior(self, _sql):
        return None


class TableSourceTests(unittest.TestCase):
    def _tree(self, provider="mysql", *, table_name="order` detail %s", view_name="order view", now=None):
        mode = "mysql" if provider in {"mysql", "oceanbase_mysql"} else "oracle"
        database = "sales db" if mode == "mysql" else "orcl service"
        schema = "" if mode == "mysql" else 'Quoted "Owner'
        host = _DirectoryHost(provider, mode, database, schema or "APP", table_name, view_name)
        service = ObjectTreeService(relational_host=host, clock=(lambda: now[0]) if now is not None else __import__("time").monotonic, node_ttl_seconds=5 if now is not None else 900)
        target = TreeTarget("conn-1", "rev-1", provider, mode=mode, database=database)
        root = service.root(target)
        first = service.submit_children(target, root, generation=17).future.result(timeout=3)
        container = next(item for item in first.nodes if item.kind in {"database", "schema"})
        second = service.submit_children(target, container, generation=17).future.result(timeout=3)
        table = next(item for item in second.nodes if item.kind == "table")
        view = next(item for item in second.nodes if item.kind == "view")
        return service, target, table, view, host

    @staticmethod
    def _choice_target(tree_target, *, tab_generation=2, schema=None):
        provider, mode = tree_target.provider, tree_target.mode
        choice = ConnectionChoice(
            tree_target.connection_id,
            tree_target.profile_revision,
            provider,
            mode,
            mode,
            tree_target.database,
        )
        selected_schema = schema if schema is not None else (tree_target.database if mode == "mysql" else 'Quoted "Owner')
        target = ManualSessionTarget(
            "tab-1", tree_target.connection_id, tree_target.profile_revision,
            database=tree_target.database, schema=selected_schema, generation=tab_generation,
        )
        return choice, target

    @staticmethod
    def _session(target, driver):
        def classify(sql):
            if sql.casefold().startswith("update "):
                return StatementClassification(StatementKind.DML, TransactionEffect.DIRTY)
            return StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE)

        return ManualSession(target, lambda _target: driver, classify, page_size=50)

    def test_signed_table_opens_in_manual_owner_and_reuses_dirty_typed_page_cursor(self):
        service, tree_target, node, _view, host = self._tree()
        choice, target = self._choice_target(tree_target, tab_generation=2)
        rows = [(index, f"p{index}", Decimal("4.20") if index == 0 else None) for index in range(60)]
        driver = _Driver("mysql", node.name, "order view", rows=rows)
        driver.max_fetch_size = 1
        session = self._session(target, driver)
        try:
            session.submit("UPDATE existing SET x = 1").result(timeout=3)
            result = session.open_table_source(
                service, tree_target, node, tree_generation=17, choice=choice, page_size=50,
            ).result(timeout=3)
            self.assertEqual(result.metadata.primary_key, ("id", "part"))
            self.assertTrue(result.metadata.editable)
            self.assertEqual(result.page.tab_id, target.tab_id)
            self.assertEqual(result.page.generation, target.generation)
            self.assertEqual(len(result.page.rows), 50)
            self.assertTrue(result.page.has_more)
            self.assertIsInstance(result.page.rows[0][2], Decimal)
            self.assertIsNone(result.page.rows[1][2])
            next_page = session.fetch_next_page(50).result(timeout=3)
            self.assertEqual(len(next_page.rows), 10)
            self.assertFalse(next_page.has_more)
            self.assertEqual(session.state, ManualSessionState.DIRTY)
            self.assertEqual(driver.commit_count, 0)
            self.assertEqual(driver.rollback_count, 0)
            owner_ids = {event[1] for event in driver.events if event[0] in {"configure", "execute", "fetchmany", "cursor_close"}}
            self.assertEqual(len(owner_ids), 1)
            self.assertNotEqual(next(iter(owner_ids)), threading.get_ident())
            self.assertTrue(all(call[0] != next(iter(owner_ids)) for call in host.directory_calls))
            sql = next(event[2] for event in driver.events if event[0] == "execute" and "FROM `sales db`" in event[2])
            self.assertIn("ORDER BY `id`, `part`", sql)
            self.assertIn("`order`` detail %%s`", sql)
            session.rollback().result(timeout=3)
        finally:
            session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_table_open_closes_previous_live_result_before_catalog_queries(self):
        service, tree_target, node, _view, _host = self._tree()
        choice, target = self._choice_target(tree_target)
        driver = _Driver("mysql", node.name, "view", rows=[(index, f"p{index}", Decimal("1")) for index in range(60)])
        session = self._session(target, driver)
        try:
            previous = session.query("SELECT previous").result(timeout=3)
            self.assertTrue(previous.has_more)
            self.assertIsNotNone(session._cursor)
            opened = session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice).result(timeout=3)
            self.assertNotEqual(opened.page.query_id, previous.query_id)
            table_catalog_index = next(
                index for index, event in enumerate(driver.events)
                if event[0] == "execute" and "information_schema.tables" in event[2].casefold()
            )
            self.assertTrue(any(event[0] == "cursor_close" for event in driver.events[:table_catalog_index]))
            self.assertEqual(session.state, ManualSessionState.CLEAN)
            self.assertEqual(driver.commit_count, 0)
            self.assertEqual(driver.rollback_count, 0)
        finally:
            session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_views_and_tables_without_keys_are_browseable_but_read_only(self):
        service, tree_target, table, view, _host = self._tree()
        choice, target = self._choice_target(tree_target)
        driver = _Driver("mysql", table.name, view.name, mysql_keys=[])
        session = self._session(target, driver)
        try:
            table_result = session.open_table_source(service, tree_target, table, tree_generation=17, choice=choice).result(timeout=3)
            self.assertFalse(table_result.metadata.editable)
            self.assertIn("主键", table_result.metadata.read_only_reason)
        finally:
            session.close().result(timeout=3)

        view_driver = _Driver("mysql", view.name, view.name, table_type="VIEW")
        view_session = self._session(target, view_driver)
        try:
            view_result = view_session.open_table_source(service, tree_target, view, tree_generation=17, choice=choice).result(timeout=3)
            self.assertFalse(view_result.metadata.editable)
            self.assertFalse(view_result.metadata.is_base_table)
            self.assertIn("视图", view_result.metadata.read_only_reason)
        finally:
            view_session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_tree_signature_checks_target_display_generation_refresh_and_expiry_without_db_io(self):
        service, tree_target, node, _view, host = self._tree()
        calls_before = len(host.directory_calls)
        resolved = service.resolve_table_source(tree_target, node, generation=17)
        self.assertEqual(resolved.raw_name, node.name)
        self.assertEqual(len(host.directory_calls), calls_before)
        with self.assertRaises(ObjectTreeError) as stale_generation:
            service.resolve_table_source(tree_target, node, generation=18)
        self.assertEqual(stale_generation.exception.code, "STALE_NODE")
        with self.assertRaises(ObjectTreeError):
            service.resolve_table_source(tree_target, replace(node, name="forged table"), generation=17)
        wrong_target = TreeTarget("conn-other", "rev-1", "mysql", mode="mysql", database=tree_target.database)
        with self.assertRaises(ObjectTreeError):
            service.resolve_table_source(wrong_target, node, generation=17)
        record = service._nodes[node.parent_ref]
        container = TreeNode(
            node.parent_ref, record.kind, record.name, record.database, record.schema,
            record.parent_ref, record.actionable, record.notice,
        )
        refreshed = service.submit_children(tree_target, container, refresh=True, generation=17).future.result(timeout=3)
        self.assertEqual(refreshed.cache_state, "fresh")
        with self.assertRaises(ObjectTreeError) as stale_refresh:
            service.resolve_table_source(tree_target, node, generation=17)
        self.assertEqual(stale_refresh.exception.code, "STALE_NODE")
        self.assertGreater(len(host.directory_calls), calls_before)
        service.close().result(timeout=3)

        clock = [0.0]
        expiring_service, expiring_target, expiring_node, _view, _expiring_host = self._tree(now=clock)
        clock[0] = 6.0
        with self.assertRaises(ObjectTreeError) as expired:
            expiring_service.resolve_table_source(expiring_target, expiring_node, generation=17)
        self.assertEqual(expired.exception.code, "STALE_NODE")
        expiring_service.close().result(timeout=3)

    def test_target_mismatch_and_forged_capture_fail_before_manual_database_io(self):
        service, tree_target, node, _view, _host = self._tree()
        choice, target = self._choice_target(tree_target, schema="different database")
        driver = _Driver("mysql", node.name, "view")
        session = self._session(target, driver)
        try:
            with self.assertRaises(ManualSessionError) as caught:
                session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice).result(timeout=3)
            self.assertEqual(caught.exception.code, "MANUAL_SCHEMA_MISMATCH")
            self.assertEqual(driver.events, [])
            with self.assertRaises(TableSourceError):
                validate_table_target(service.resolve_table_source(tree_target, node, generation=17), tree_target, target, replace(choice, database="other"))
        finally:
            session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_global_mysql_and_ob_mysql_roots_only_bind_matching_signed_child_database(self):
        for provider in ("mysql", "oceanbase_mysql"):
            with self.subTest(provider=provider):
                tree_target = TreeTarget("conn-root", "rev-1", provider, mode="mysql", database="")
                choice = ConnectionChoice(
                    "conn-root", "rev-1", provider, "mysql", "mysql", "signed-db",
                )
                target = ManualSessionTarget(
                    "tab-root", "conn-root", "rev-1", database="signed-db",
                    schema="signed-db", generation=4,
                )
                ref = VerifiedTableRef(
                    tree_target, "signed-node", 17, 0, "table", "raw_table", "signed-db", "",
                )
                validate_table_target(ref, tree_target, target, choice)

                mismatched_choice = replace(choice, database="other-db")
                mismatched_target = replace(target, database="other-db", schema="other-db")
                with self.assertRaises(TableSourceError) as different_child_database:
                    validate_table_target(ref, tree_target, mismatched_target, mismatched_choice)
                self.assertEqual(different_child_database.exception.code, "TABLE_DATABASE_MISMATCH")

                scoped_root = replace(tree_target, database="root-db")
                scoped_ref = replace(ref, target=scoped_root)
                scoped_choice = replace(choice, database="root-db")
                scoped_target = replace(target, database="root-db", schema="root-db")
                wrong_child = replace(scoped_ref, database="other-db")
                with self.assertRaises(TableSourceError) as nonempty_root:
                    validate_table_target(wrong_child, scoped_root, scoped_target, scoped_choice)
                self.assertEqual(nonempty_root.exception.code, "TABLE_DATABASE_MISMATCH")

        oracle_root = TreeTarget("conn-root", "rev-1", "oracle", mode="oracle", database="")
        oracle_choice = ConnectionChoice("conn-root", "rev-1", "oracle", "oracle", "oracle", "service")
        oracle_target = ManualSessionTarget(
            "tab-root", "conn-root", "rev-1", database="service", schema="OWNER", generation=4,
        )
        oracle_ref = VerifiedTableRef(oracle_root, "oracle-node", 17, 0, "table", "T", "", "OWNER")
        with self.assertRaises(TableSourceError) as oracle_empty_root:
            validate_table_target(oracle_ref, oracle_root, oracle_target, oracle_choice)
        self.assertEqual(oracle_empty_root.exception.code, "MANUAL_TARGET_MISMATCH")

    def test_oracle_service_and_owner_stay_separate_and_ordered_composite_key_is_used(self):
        service, tree_target, node, _view, _host = self._tree("oracle", table_name='A" B', view_name="READ_VIEW")
        choice, target = self._choice_target(tree_target, schema='Quoted "Owner')
        driver = _Driver(
            "oracle", node.name, "READ_VIEW",
            oracle_columns=[
                (target.schema, node.name, "K1", "NUMBER", 22, 12, 0, "N", 0, "NO", "NO", 1),
                (target.schema, node.name, "K2", "VARCHAR2", 80, None, None, "N", 0, "NO", "NO", 2),
                (target.schema, node.name, "NOTE", "CLOB", 1000, None, None, "Y", 0, "NO", "NO", 5),
            ],
            oracle_keys=[("K2", 2), ("K1", 1)],
        )
        session = self._session(target, driver)
        try:
            result = session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice).result(timeout=3)
            self.assertEqual(result.metadata.primary_key, ("K1", "K2"))
            self.assertTrue(result.metadata.editable)
            clob = next(column for column in result.metadata.columns if column.name == "NOTE")
            self.assertFalse(clob.editable)
            self.assertFalse(clob.comparable)
            query = next(event[2] for event in driver.events if event[0] == "execute" and "FROM \"Quoted" in event[2])
            self.assertIn('FROM "Quoted ""Owner"."A"" B" ORDER BY "K1", "K2"', query)
            catalog = [event for event in driver.events if event[0] == "execute" and "ALL_OBJECTS" in event[2]]
            self.assertEqual(catalog[0][3], {"p0": target.schema, "p1": node.name})
            self.assertNotIn(target.schema, catalog[0][2])
        finally:
            session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_catalog_failure_returns_stable_error_without_page_sql_or_transaction_effects(self):
        service, tree_target, node, _view, _host = self._tree()
        choice, target = self._choice_target(tree_target)
        driver = _Driver("mysql", node.name, "view", fail_on="from information_schema.columns")
        session = self._session(target, driver)
        try:
            with self.assertRaises(ManualSessionError) as caught:
                session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice).result(timeout=3)
            self.assertEqual(caught.exception.code, "TABLE_METADATA_QUERY_FAILED")
            self.assertNotIn("sensitive database diagnostic", str(caught.exception))
            self.assertFalse(any(
                event[0] == "execute" and "FROM `sales db`" in event[2]
                for event in driver.events
            ))
            self.assertEqual(driver.commit_count, 0)
            self.assertEqual(driver.rollback_count, 0)
        finally:
            session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_oceanbase_mysql_uses_mysql_dialect_and_oracle_unknown_identity_stays_read_only(self):
        mysql_service, mysql_target, mysql_node, _view, _host = self._tree("oceanbase_mysql")
        choice, target = self._choice_target(mysql_target)
        mysql_session = self._session(target, _Driver("oceanbase_mysql", mysql_node.name, "view"))
        try:
            result = mysql_session.open_table_source(mysql_service, mysql_target, mysql_node, tree_generation=17, choice=choice).result(timeout=3)
            self.assertTrue(result.metadata.editable)
            query = next(event[2] for event in mysql_session._driver.events if event[0] == "execute" and "FROM `sales db`" in event[2])
            self.assertIn("`order`` detail %%s`", query)
        finally:
            mysql_session.close().result(timeout=3)
            mysql_service.close().result(timeout=3)

        oracle_service, oracle_target, oracle_node, _view, _host = self._tree("oceanbase_oracle")
        oracle_choice, oracle_manual_target = self._choice_target(oracle_target)
        oracle_driver = _Driver(
            "oceanbase_oracle", oracle_node.name, "view",
            oracle_columns=[
                (oracle_manual_target.schema, oracle_node.name, "id", "NUMBER", 22, 12, 0, "N", 0, None, "NO", 1),
            ],
        )
        oracle_session = self._session(oracle_manual_target, oracle_driver)
        try:
            result = oracle_session.open_table_source(oracle_service, oracle_target, oracle_node, tree_generation=17, choice=oracle_choice).result(timeout=3)
            self.assertEqual(result.metadata.primary_key, ("id",))
            self.assertFalse(result.metadata.editable)
            self.assertIn("生成列", result.metadata.read_only_reason)
        finally:
            oracle_session.close().result(timeout=3)
            oracle_service.close().result(timeout=3)

    def test_generated_and_default_column_facts_are_preserved(self):
        service, tree_target, node, _view, _host = self._tree()
        choice, target = self._choice_target(tree_target)
        driver = _Driver(
            "mysql", node.name, "view",
            mysql_columns=[
                (tree_target.database, node.name, "id", "bigint", "bigint", "NO", 0, "", 1),
                (tree_target.database, node.name, "generated_id", "bigint", "bigint", "NO", 0, "auto_increment", 2),
                (tree_target.database, node.name, "calc", "int", "int", "YES", 0, "VIRTUAL GENERATED", 3),
                (tree_target.database, node.name, "label", "varchar(20)", "varchar", "YES", 0, "", 4),
                (tree_target.database, node.name, "required", "int", "int", "NO", 0, "", 5),
                (tree_target.database, node.name, "server_default", "varchar(20)", "varchar", "NO", 1, "", 6),
                (tree_target.database, node.name, "payload", "longblob", "longblob", "YES", 0, "", 7),
            ],
            mysql_keys=[("id", 1)],
        )
        session = self._session(target, driver)
        try:
            result = session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice).result(timeout=3)
            facts = {column.name: column for column in result.metadata.columns}
            self.assertTrue(facts["generated_id"].generated)
            self.assertFalse(facts["generated_id"].editable)
            self.assertTrue(facts["calc"].generated)
            self.assertFalse(facts["calc"].editable)
            self.assertTrue(facts["label"].nullable)
            self.assertTrue(facts["label"].has_default)
            self.assertTrue(facts["required"].required_on_insert)
            self.assertIsNone(facts["required"].has_default)
            self.assertTrue(facts["server_default"].has_default)
            self.assertFalse(facts["server_default"].required_on_insert)
            self.assertFalse(facts["payload"].editable)
            self.assertFalse(facts["payload"].comparable)

            from tools.data_center.changeset import ChangeSet, ChangeSetError
            from tools.data_center.changeset_dialects import create_changeset_dialect

            changes = ChangeSet(
                result.metadata.verified_changeset,
                create_changeset_dialect(choice, target),
            )
            changes.stage_insert({"id": 10, "required": 7})
            with self.assertRaises(ChangeSetError) as missing_required:
                changes.stage_insert({"id": 11})
            self.assertEqual(missing_required.exception.code, "INSERT_REQUIRED_COLUMN_MISSING")
        finally:
            session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_running_open_is_not_cancelled_as_terminal_and_close_waits_for_real_owner(self):
        service, tree_target, node, _view, _host = self._tree()
        choice, target = self._choice_target(tree_target)
        driver = _Driver("mysql", node.name, "view", block_on="from information_schema.columns")
        session = self._session(target, driver)
        future = session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice)
        try:
            self.assertTrue(driver.started.wait(2))
            self.assertTrue(future.running())
            self.assertFalse(future.cancel())
            self.assertTrue(session.cancel_current())
            self.assertFalse(future.done())
            status = session.poll_cleanup()
            self.assertTrue(status.busy)
            self.assertTrue(status.connection_retained)
            closed = session.close()
            self.assertFalse(closed.done())
            driver.release.set()
            with self.assertRaises(ManualSessionError) as caught:
                future.result(timeout=3)
            self.assertEqual(caught.exception.code, "CANCELLED")
            self.assertTrue(closed.result(timeout=3).closed)
            self.assertEqual(driver.commit_count, 0)
            self.assertEqual(driver.rollback_count, 0)
            self.assertTrue(driver.closed)
            columns_execute_index = next(
                index for index, event in enumerate(driver.events)
                if event[0] == "execute" and "information_schema.columns" in event[2].casefold()
            )
            self.assertFalse(any(event[0] == "fetchmany" for event in driver.events[columns_execute_index + 1 :]))
        finally:
            driver.release.set()
            if not session.poll_cleanup().cleanup_complete:
                try:
                    session.close(disposition="cancel").result(timeout=3)
                except Exception:
                    pass
            service.close().result(timeout=3)

    def test_refresh_during_owner_catalog_work_rejects_stale_table_before_page_query(self):
        service, tree_target, node, _view, _host = self._tree()
        choice, target = self._choice_target(tree_target)
        driver = _Driver("mysql", node.name, "view", block_on="from information_schema.columns")
        session = self._session(target, driver)
        future = session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice)
        try:
            self.assertTrue(driver.started.wait(2))
            record = service._nodes[node.parent_ref]
            parent = TreeNode(
                node.parent_ref, record.kind, record.name, record.database, record.schema,
                record.parent_ref, record.actionable, record.notice,
            )
            service.submit_children(tree_target, parent, refresh=True, generation=17).future.result(timeout=3)
            driver.release.set()
            with self.assertRaises(ManualSessionError) as caught:
                future.result(timeout=3)
            self.assertEqual(caught.exception.code, "STALE_TABLE_REF")
            self.assertFalse(any(
                event[0] == "execute" and "FROM `sales db`" in event[2]
                for event in driver.events
            ))
        finally:
            driver.release.set()
            session.close(disposition="cancel").result(timeout=3)
            service.close().result(timeout=3)

    def test_queued_open_future_can_be_cancelled_without_catalog_io(self):
        service, tree_target, node, _view, _host = self._tree()
        choice, target = self._choice_target(tree_target)
        driver = _Driver("mysql", node.name, "view", block_on="select block")
        session = self._session(target, driver)
        try:
            blocker = session.query("SELECT block")
            self.assertTrue(driver.started.wait(2))
            queued = session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice)
            self.assertTrue(queued.cancel())
            driver.release.set()
            blocker.result(timeout=3)
            self.assertTrue(queued.cancelled())
            self.assertFalse(any("information_schema" in event[2].casefold() for event in driver.events if event[0] == "execute"))
        finally:
            driver.release.set()
            session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_manual_result_budget_still_caps_table_page_rows(self):
        service, tree_target, node, _view, _host = self._tree()
        choice, target = self._choice_target(tree_target)
        driver = _Driver("mysql", node.name, "view", rows=[("x" * 1024, "p", Decimal("1.0"))])
        session = ManualSession(
            target,
            lambda _target: driver,
            lambda _sql: StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE),
            page_size=50,
            result_budget_bytes=512,
        )
        try:
            result = session.open_table_source(service, tree_target, node, tree_generation=17, choice=choice).result(timeout=3)
            self.assertEqual(result.page.rows, ())
            self.assertTrue(result.page.truncated_by_budget)
            self.assertLessEqual(result.page.result_bytes, 512)
            with self.assertRaises(ManualSessionError) as caught:
                session.fetch_next_page(50).result(timeout=3)
            self.assertEqual(caught.exception.code, "RESULT_BUDGET_EXHAUSTED")
        finally:
            session.close().result(timeout=3)
            service.close().result(timeout=3)


if __name__ == "__main__":
    unittest.main()
