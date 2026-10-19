from __future__ import annotations

import re
import threading
import unittest
from dataclasses import replace

from tools.data_center.manual_session import ManualSessionError, ManualSessionState, ManualSessionTarget
from tools.data_center.object_tree import ObjectTreeService, TreeNode, TreeTarget
from tools.data_center.profile_host import DataCenterProfileHost
from tools.data_center.host_metadata import MetadataTarget
from tools.data_center.host_relational import RelationalReadOnlyHost
from tools.data_center.manual_driver import ManualDriverError, ManualRelationalDriver, RelationalManualDriverFactory
from tools.data_center.manual_relational_providers import BoundManualTableScope, create_manual_session
from tools.data_center.object_tree_composition import ObjectTreeComposition


class _DirectoryLease:
    def __init__(self, host, target):
        self.host = host
        self.target = target
        self.dialect = "mysql" if target.provider.endswith("mysql") else "oracle"

    def execute_readonly(self, request, **_kwargs):
        self.host.directory_calls.append((threading.get_ident(), request["sql"]))
        sql = request["sql"].casefold()
        params = tuple(request["parameters"])
        if "information_schema.schemata" in sql:
            rows = [(self.host.database,)]
        elif "information_schema.tables" in sql:
            rows = [(self.host.table, "BASE TABLE"), ("a view", "VIEW")]
        elif "select distinct owner" in sql:
            rows = [(self.host.schema,)]
        elif "select object_name, object_type" in sql:
            rows = [(self.host.table, "TABLE"), ("a view", "VIEW")]
        else:
            rows = []
        offset = int(params[-1]) if params else 0
        return {"columns": (), "rows": rows[offset : offset + int(request["row_limit"])]}

    def close(self):
        pass


class _DirectoryFactory:
    def __init__(self, host, target):
        self.host = host
        self.target = target

    def __call__(self, connection_id, **_kwargs):
        del connection_id
        return _DirectoryLease(self.host, self.target)


class _DirectoryHost:
    def __init__(self, provider, database, schema=""):
        self.provider = provider
        self.mode = "mysql" if provider.endswith("mysql") else "oracle"
        self.database = database
        self.schema = schema
        self.table = "signed table"
        self.directory_calls = []

    def profile_snapshot(self, connection_id):
        return type("Snapshot", (), {
            "connection_id": connection_id,
            "profile_revision": self.revision,
            "provider": self.provider,
            "mode": self.mode,
            "database": self.database,
        })()

    def create_factory(self, connection_id, **_kwargs):
        target = MetadataTarget(
            connection_id,
            self.revision,
            self.provider,
            self.mode,
            self.database,
            (),
        )
        return _DirectoryFactory(self, target)


class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.description = None
        self.rowcount = -1
        self.closed = False

    def execute(self, sql, parameters=None):
        del parameters
        self.connection.events.append(("execute", str(sql), threading.get_ident()))
        upper = str(sql).upper()
        if upper.startswith("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')"):
            self.rows = [(self.connection.current_schema,)]
            self.description = (("CURRENT_SCHEMA",),)
            self.rowcount = 1
        elif upper.startswith("ALTER SESSION SET CURRENT_SCHEMA"):
            match = re.fullmatch(r'(?is)\s*ALTER\s+SESSION\s+SET\s+CURRENT_SCHEMA\s*=\s*"((?:[^"]|"")*)"\s*', str(sql))
            if not match:
                raise AssertionError("schema identifier must be quoted")
            self.connection.current_schema = match.group(1).replace('""', '"')
            self.rows = []
            self.description = ()
            self.rowcount = 0
        elif upper.startswith("SELECT"):
            self.rows = [(1,)]
            self.description = (("value", "INTEGER", None, None, None, None, False),)
            self.rowcount = 1
        else:
            self.rows = []
            self.description = ()
            self.rowcount = 0
        return self

    def executemany(self, sql, rows):
        self.connection.events.append(("executemany", sql, tuple(rows), threading.get_ident()))
        self.rowcount = len(rows)
        return None

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchmany(self, size):
        result, self.rows = self.rows[:size], self.rows[size:]
        return result

    def close(self):
        self.closed = True
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _Connection:
    def __init__(self, schema="", *, autocommit=True, on_autocommit=None):
        self.current_schema = schema
        self._autocommit = autocommit
        self.on_autocommit = on_autocommit
        self.events = []
        self.closed = False

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self.events.append(("autocommit", bool(value), threading.get_ident()))
        self._autocommit = bool(value)
        if self.on_autocommit:
            self.on_autocommit()

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _Cursor(self)

    def commit(self):
        self.events.append(("commit", threading.get_ident()))

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("connection_close", threading.get_ident()))


class _CatalogCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.offset = 0
        self.description = ()

    def execute(self, sql, parameters=()):
        values = tuple(parameters or ())
        self.connection.events.append(("execute", str(sql), values, threading.get_ident()))
        folded = str(sql).casefold()
        if "information_schema.schemata" in folded:
            self.rows = [("sales",)]
        elif "information_schema.tables" in folded:
            self.rows = [("items", "BASE TABLE"), ("items_view", "VIEW")]
        elif "select distinct owner" in folded:
            self.rows = [("APP",)]
        elif "select object_name, object_type" in folded:
            self.rows = [("items", "TABLE"), ("items_view", "VIEW")]
        elif "information_schema.columns" in folded:
            schema, table = values[:2]
            self.rows = [
                (schema, table, "id", "int", "int", "NO", 0, "", 1),
                (schema, table, "label", "varchar(32)", "varchar", "YES", 0, "", 2),
            ]
        elif "information_schema.key_column_usage" in folded:
            self.rows = [("id", 1)]
        elif folded.lstrip().startswith("select") and " from `sales`.`items" in folded:
            self.rows = [(7, "catalog row")]
            self.description = (
                ("id", "INTEGER", None, None, None, 0, False),
                ("label", "VARCHAR", None, None, None, None, True),
            )
        else:
            self.rows = []
        self.offset = 0

    def fetchmany(self, size):
        rows = self.rows[self.offset:self.offset + size]
        self.offset += len(rows)
        return rows

    def fetchone(self):
        rows = self.fetchmany(1)
        return rows[0] if rows else None

    def close(self):
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _CatalogConnection:
    def __init__(self, events):
        self.events = events
        self.autocommit = True
        self.closed = False

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _CatalogCursor(self)

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("connection_close", threading.get_ident()))


class _ManualTableCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.offset = 0
        self.description = ()
        self.rowcount = -1

    def execute(self, sql, parameters=()):
        values = tuple(parameters.values()) if isinstance(parameters, dict) else tuple(parameters or ())
        folded = str(sql).casefold()
        self.connection.events.append(("execute", str(sql), values, threading.get_ident()))
        if "select sys_context('userenv', 'current_schema')" in folded:
            self.rows = [(self.connection.current_schema,)]
            self.description = (("CURRENT_SCHEMA",),)
        elif folded.lstrip().startswith("alter session set current_schema"):
            match = re.fullmatch(r'(?is)\s*ALTER\s+SESSION\s+SET\s+CURRENT_SCHEMA\s*=\s*"((?:""|[^"])*)"\s*', str(sql))
            if not match:
                raise AssertionError("Oracle owner must use one quoted identifier")
            self.connection.current_schema = match.group(1).replace('""', '"')
            self.rows = []
        elif "from all_objects" in folded:
            self.rows = [("TABLE" if values[1] == "items" else "VIEW",)]
        elif "from all_tab_columns c" in folded:
            owner, table = values[:2]
            self.rows = [(owner, table, "id", "NUMBER", 22, 12, 0, "N", 0, "NO", "NO", 1)]
        elif "from all_constraints c" in folded:
            self.rows = [("id", 1)]
        elif folded.lstrip().startswith("select") and ' from "app"."items"' in folded:
            self.rows = [(7,)]
            self.description = (("id", "NUMBER", None, None, 22, 0, False),)
        elif "select table_type from information_schema.tables" in folded:
            self.rows = [("VIEW" if values[1] == "items_view" else "BASE TABLE",)]
        elif "from information_schema.columns" in folded:
            schema, table = values[:2]
            self.rows = [
                (schema, table, "id", "int", "int", "NO", 0, "", 1),
                (schema, table, "label", "varchar(32)", "varchar", "YES", 0, "", 2),
            ]
        elif "from information_schema.key_column_usage" in folded:
            self.rows = [("id", 1)]
        elif folded.lstrip().startswith("select") and " from `sales`.`items" in folded:
            self.rows = [(7, "manual row")]
            self.description = (
                ("id", "INTEGER", None, None, None, 0, False),
                ("label", "VARCHAR", None, None, None, None, True),
            )
        else:
            self.rows = []
        self.offset = 0
        self.rowcount = len(self.rows)

    def executemany(self, sql, rows):
        self.connection.events.append(("executemany", str(sql), tuple(rows), threading.get_ident()))
        self.rowcount = len(rows)

    def fetchmany(self, size):
        rows = self.rows[self.offset:self.offset + size]
        self.offset += len(rows)
        return rows

    def fetchone(self):
        rows = self.fetchmany(1)
        return rows[0] if rows else None

    def close(self):
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _ManualTableConnection:
    def __init__(self):
        self.events = []
        self.autocommit = True
        self.current_schema = "LOGIN"
        self.closed = False

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _ManualTableCursor(self)

    def commit(self):
        self.events.append(("commit", threading.get_ident()))

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("connection_close", threading.get_ident()))


def _profile(provider, database, schema="LOGIN"):
    mode = "mysql" if provider.endswith("mysql") else "oracle"
    raw_provider = "oceanbase" if provider.startswith("oceanbase_") else provider
    return {
        "id": "scope-connection",
        "provider": raw_provider,
        "engine": raw_provider,
        "mode": mode,
        "dialect": mode,
        "host": "127.0.0.1",
        "port": 3306 if mode == "mysql" else 1521,
        "username": "temp-user",
        "password": "TEMP-CIPHER",
        "database": database,
        "schema": schema,
    }


def _tree(provider, profile, selected_database, schema=""):
    tree_host = _DirectoryHost(provider, selected_database, schema)
    profile_host = DataCenterProfileHost(connections_loader=lambda: [profile])
    choice = profile_host.capture_connection(
        profile,
        database=selected_database,
        schema_allowlist=(schema,) if schema else (),
    )
    tree_host.revision = choice.revision
    target = TreeTarget(
        choice.connection_id,
        choice.revision,
        provider,
        mode=choice.mode,
        database=selected_database,
    )
    service = ObjectTreeService(relational_host=tree_host)
    root = service.root(target)
    first = service.submit_children(target, root, generation=17).future.result(timeout=3)
    parent_kind = "database" if provider.endswith("mysql") else "schema"
    parent = next(node for node in first.nodes if node.kind == parent_kind)
    second = service.submit_children(target, parent, generation=17).future.result(timeout=3)
    table = next(node for node in second.nodes if node.kind == "table")
    return service, tree_host, target, table, profile_host, choice


class ManualTableScopeTests(unittest.TestCase):
    def _oracle_setup(self, *, schema='Sales "East'):
        profile = _profile("oracle", "service.prod", "LOGIN")
        service, tree_host, tree_target, node, profile_host, choice = _tree(
            "oracle", profile, "service.prod", schema,
        )
        target = ManualSessionTarget(
            "manual-tab", choice.connection_id, choice.revision,
            database="service.prod", schema=schema, generation=4,
        )
        calls = []

        def resolve_secret(token):
            calls.append(("secret", token, threading.get_ident()))
            return "TEMP-SECRET"

        connection = _Connection("LOGIN")

        def connect(loaded, secret):
            calls.append(("connector", dict(loaded), secret, threading.get_ident()))
            return connection

        scope = BoundManualTableScope.capture(
            service, tree_target, node, 17, target, choice,
        )
        return service, tree_host, tree_target, node, profile_host, choice, target, scope, calls, connection, resolve_secret, connect

    def test_capture_resolves_live_signature_without_metadata_io_and_keeps_generations_separate(self):
        parts = self._oracle_setup()
        service, tree_host, tree_target, node, _host, choice, target, scope = parts[:8]
        count = len(tree_host.directory_calls)
        self.assertEqual(scope.tree_generation, 17)
        self.assertEqual(scope.manual_target.generation, 4)
        self.assertNotEqual(scope.tree_generation, scope.manual_target.generation)
        self.assertEqual(scope.verified_ref.raw_name, node.name)
        scope.validate_current(target, choice)
        with self.assertRaises(ManualDriverError) as changed_target:
            scope.validate_current(replace(target, schema="OTHER"), choice)
        self.assertEqual(changed_target.exception.code, "TABLE_SCOPE_MISMATCH")
        with self.assertRaises(ManualDriverError) as changed_choice:
            scope.validate_current(target, replace(choice, schema_allowlist=("OTHER",)))
        self.assertEqual(changed_choice.exception.code, "TABLE_SCOPE_MISMATCH")
        self.assertEqual(len(tree_host.directory_calls), count)
        service.close().result(timeout=3)

    def test_manual_owner_revalidates_and_quotes_exact_oracle_owner_on_same_connection(self):
        (service, _tree_host, _tree_target, _node, profile_host, choice, target,
         scope, calls, connection, resolve_secret, connect) = self._oracle_setup()
        profile_threads = []
        original_loader = profile_host._connections_loader
        profile_host._connections_loader = lambda: (profile_threads.append(threading.get_ident()) or original_loader())
        session = create_manual_session(
            target, choice, table_scope=scope, profile_host=profile_host,
            connector=connect, secret_resolver=resolve_secret,
        )
        try:
            result = session.query("SELECT 1").result(timeout=3)
            self.assertEqual(result.rows, ((1,),))
            statements = [event[1] for event in connection.events if event[0] == "execute"]
            self.assertIn('ALTER SESSION SET CURRENT_SCHEMA = "Sales ""East"', statements)
            self.assertEqual(connection.current_schema, 'Sales "East')
            self.assertTrue(any(sql.startswith("SELECT SYS_CONTEXT") for sql in statements))
            self.assertEqual(connection.events.count(("commit", threading.get_ident())), 0)
            self.assertFalse(any(event[0] == "rollback" for event in connection.events))
            owner_ids = {call[-1] for call in calls}
            owner_ids.update(profile_threads)
            owner_ids.update(event[-1] for event in connection.events if isinstance(event[-1], int))
            self.assertEqual(len(owner_ids), 1)
            self.assertNotEqual(next(iter(owner_ids)), threading.get_ident())
            self.assertEqual([item[0] for item in calls], ["secret", "connector"])
            self.assertEqual(calls[1][1]["database"], "service.prod")
            self.assertEqual(calls[1][1]["schema"], "LOGIN")
            self.assertEqual(session.state, ManualSessionState.CLEAN)
        finally:
            session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_manual_target_and_tree_scope_must_still_match_before_secret(self):
        (service, _tree_host, tree_target, node, profile_host, choice, target,
         scope, calls, _connection, resolve_secret, connect) = self._oracle_setup()
        invalidated = False

        def stale_secret(token):
            nonlocal invalidated
            result = resolve_secret(token)
            service.invalidate(tree_target, node)
            invalidated = True
            return result

        session = create_manual_session(
            target, choice, table_scope=scope, profile_host=profile_host,
            connector=connect, secret_resolver=stale_secret,
        )
        try:
            with self.assertRaises(ManualSessionError):
                session.query("SELECT 1").result(timeout=3)
            self.assertTrue(invalidated)
            self.assertEqual([call[0] for call in calls], ["secret"])
        finally:
            session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_signature_invalidated_while_connector_runs_retains_owner_cleanup_handle(self):
        (service, _tree_host, tree_target, node, profile_host, choice, target,
         scope, calls, connection, resolve_secret, _connect) = self._oracle_setup()

        def invalidating_connect(loaded, secret):
            calls.append(("connector", dict(loaded), secret, threading.get_ident()))
            service.invalidate(tree_target, node)
            return connection

        session = create_manual_session(
            target, choice, table_scope=scope, profile_host=profile_host,
            connector=invalidating_connect, secret_resolver=resolve_secret,
        )
        try:
            with self.assertRaises(ManualSessionError):
                session.query("SELECT 1").result(timeout=3)
            self.assertFalse(connection.closed)
            self.assertTrue(session.poll_cleanup().connection_retained)
            self.assertEqual(session.state, ManualSessionState.DISCONNECTED)
            self.assertFalse(any(event[0] in {"autocommit", "execute"} for event in connection.events))
        finally:
            session.close().result(timeout=3)
            self.assertTrue(connection.closed)
            service.close().result(timeout=3)

    def test_public_factory_returns_stale_cleanup_only_driver_that_rejects_direct_db_calls(self):
        (service, _tree_host, tree_target, node, profile_host, choice, target,
         scope, _calls, connection, _secret, _connect) = self._oracle_setup()

        def invalidating_connect(loaded, secret):
            del loaded, secret
            service.invalidate(tree_target, node)
            return connection

        factory = RelationalManualDriverFactory(
            choice, profile_host=profile_host, connector=invalidating_connect,
            secret_resolver=lambda _token: "TEMP-SECRET", table_scope=scope,
        )
        driver = factory(target)
        try:
            initial_cursors = len([event for event in connection.events if event[0] == "cursor"])
            for call in (
                lambda: driver.configure_manual_commit(),
                lambda: driver.execute("SELECT 1", ()),
                lambda: driver.executemany("INSERT INTO t VALUES (:1)", [(1,)]),
                lambda: driver.create_savepoint("test_point"),
                lambda: driver.commit(),
                lambda: driver.rollback(),
            ):
                with self.assertRaises(ManualDriverError) as stale:
                    call()
                self.assertEqual(stale.exception.code, "TABLE_SCOPE_STALE")
            self.assertEqual(
                len([event for event in connection.events if event[0] == "cursor"]),
                initial_cursors,
            )
            self.assertFalse(connection.closed)
        finally:
            driver.close()
            self.assertTrue(connection.closed)
            service.close().result(timeout=3)

    def test_batch_api_revalidates_signed_scope_before_opening_cursor(self):
        (service, _tree_host, tree_target, node, profile_host, choice, target,
         scope, _calls, connection, _secret, connect) = self._oracle_setup()
        factory = RelationalManualDriverFactory(
            choice, profile_host=profile_host, connector=connect,
            secret_resolver=lambda _token: "TEMP-SECRET", table_scope=scope,
        )
        driver = None
        try:
            driver = factory(target)
            service.invalidate(tree_target, node)
            cursor_count = len([event for event in connection.events if event[0] == "cursor"])
            with self.assertRaises(ManualDriverError) as stale:
                driver.executemany("INSERT INTO signed_table VALUES (:1)", [(1,)])
            self.assertEqual(stale.exception.code, "TABLE_SCOPE_STALE")
            self.assertEqual(len([event for event in connection.events if event[0] == "cursor"]), cursor_count)
        finally:
            if driver is not None:
                driver.close()
            service.close().result(timeout=3)

    def test_configured_scoped_transaction_can_commit_or_rollback_after_tree_refresh(self):
        (service, _tree_host, tree_target, node, profile_host, choice, target,
         scope, _calls, connection, _secret, connect) = self._oracle_setup()
        session = create_manual_session(
            target, choice, table_scope=scope, profile_host=profile_host,
            connector=connect, secret_resolver=lambda _token: "TEMP-SECRET",
        )
        try:
            session.submit("UPDATE signed_table SET value = :1", (1,)).result(timeout=3)
            self.assertEqual(session.state, ManualSessionState.DIRTY)
            service.invalidate(tree_target, node)
            committed = session.commit().result(timeout=3)
            self.assertTrue(committed.applied)
            self.assertEqual(session.state, ManualSessionState.CLEAN)
            self.assertEqual([event[0] for event in connection.events].count("commit"), 1)
            self.assertEqual([event[0] for event in connection.events].count("rollback"), 0)
            self.assertTrue(session.close().result(timeout=3).closed)
        finally:
            service.close().result(timeout=3)

        # The close decision path uses the same owner's already-configured
        # driver to roll back its active transaction after a tree refresh.
        (service, _tree_host, tree_target, node, profile_host, choice, target,
         scope, _calls, connection, _secret, connect) = self._oracle_setup()
        session = create_manual_session(
            target, choice, table_scope=scope, profile_host=profile_host,
            connector=connect, secret_resolver=lambda _token: "TEMP-SECRET",
        )
        try:
            session.submit("UPDATE signed_table SET value = :1", (1,)).result(timeout=3)
            self.assertEqual(session.state, ManualSessionState.DIRTY)
            service.invalidate(tree_target, node)
            closed = session.close(disposition="rollback").result(timeout=3)
            self.assertTrue(closed.closed)
            self.assertEqual([event[0] for event in connection.events].count("rollback"), 1)
            self.assertEqual([event[0] for event in connection.events].count("commit"), 0)
        finally:
            service.close().result(timeout=3)

    def test_scope_is_checked_again_after_autocommit_setup_before_oracle_owner_change(self):
        (service, _tree_host, tree_target, node, profile_host, choice, target,
         scope, _calls, _connection, _secret, connect) = self._oracle_setup()
        connection = _Connection("LOGIN", on_autocommit=lambda: service.invalidate(tree_target, node))

        def connector(loaded, secret):
            del loaded, secret
            return connection

        session = create_manual_session(
            target, choice, table_scope=scope, profile_host=profile_host,
            connector=connector, secret_resolver=lambda token: "TEMP-SECRET",
        )
        try:
            with self.assertRaises(ManualSessionError):
                session.query("SELECT 1").result(timeout=3)
            self.assertFalse(connection.closed)
            self.assertTrue(session.poll_cleanup().connection_retained)
            self.assertEqual(session.state, ManualSessionState.DISCONNECTED)
            self.assertFalse(any(event[0] == "execute" and event[1].startswith("ALTER SESSION") for event in connection.events))
        finally:
            session.close().result(timeout=3)
            self.assertTrue(connection.closed)
            service.close().result(timeout=3)

    def test_mysql_uses_signed_database_in_copy_without_mutating_profile_revision(self):
        profile = _profile("mysql", "stored-default")
        original_profile = dict(profile)
        selected_db = "signed-selected"
        service, _tree_host, tree_target, node, profile_host, choice = _tree(
            "mysql", profile, selected_db,
        )
        target = ManualSessionTarget("manual-tab", choice.connection_id, choice.revision, selected_db, selected_db, 3)
        scope = BoundManualTableScope.capture(service, tree_target, node, 17, target, choice)
        seen = []
        connection = _Connection(autocommit=True)
        session = create_manual_session(
            target, choice, table_scope=scope, profile_host=profile_host,
            connector=lambda loaded, _secret: (seen.append(loaded) or connection),
            secret_resolver=lambda _token: "TEMP-SECRET",
        )
        try:
            session.query("SELECT 1").result(timeout=3)
            self.assertEqual(seen[0]["database"], selected_db)
            self.assertIsNot(seen[0], profile)
            self.assertEqual(profile, original_profile)
            self.assertEqual(choice.revision, profile_host.capture_connection(profile, database=selected_db).revision)
        finally:
            session.close().result(timeout=3)
            service.close().result(timeout=3)

    def test_global_mysql_composition_binds_signed_database_for_manual_table_and_view(self):
        profile = _profile("mysql", "stored-default")
        original_profile = dict(profile)
        readonly_events = []
        readonly_connections = []
        readonly_secret_calls = []

        def readonly_connector(provider, profile, secret, **options):
            readonly_events.append((threading.get_ident(), provider, dict(profile), secret, dict(options)))
            connection = _CatalogConnection(readonly_events)
            readonly_connections.append(connection)
            return connection

        relational = RelationalReadOnlyHost(
            profile_loader=lambda connection_id: profile if connection_id == profile["id"] else None,
            secret_loader=lambda loaded: (readonly_secret_calls.append(threading.get_ident()) or "TEMP-READONLY"),
            connector=readonly_connector,
        )
        composition = ObjectTreeComposition(relational_host=relational)
        tree_generation = 17
        table_session = None
        view_session = None
        wrong_session = None
        manual_secret_calls = []
        manual_connector_calls = []
        manual_connections = []

        def manual_connector(loaded, secret):
            manual_connector_calls.append((threading.get_ident(), dict(loaded), secret))
            connection = _ManualTableConnection()
            manual_connections.append(connection)
            return connection

        def resolve_manual_secret(token):
            manual_secret_calls.append((threading.get_ident(), token))
            return "TEMP-MANUAL"

        try:
            tree_target = composition.submit_target_capture(profile["id"]).result(timeout=3)
            self.assertEqual(tree_target.database, "")
            root = composition.service.root(tree_target)
            databases = composition.service.submit_children(
                tree_target, root, generation=tree_generation,
            ).future.result(timeout=3)
            database_node = next(node for node in databases.nodes if node.kind == "database")
            self.assertEqual(database_node.database, "sales")
            objects = composition.service.submit_children(
                tree_target, database_node, generation=tree_generation,
            ).future.result(timeout=3)
            table_node = next(node for node in objects.nodes if node.kind == "table")
            view_node = next(node for node in objects.nodes if node.kind == "view")
            self.assertEqual((table_node.database, table_node.schema), ("sales", ""))
            self.assertEqual((view_node.database, view_node.schema), ("sales", ""))

            profile_host = DataCenterProfileHost(connections_loader=lambda: [profile])
            choice = profile_host.capture_connection(profile, database="sales")
            target = ManualSessionTarget(
                "manual-tab", choice.connection_id, choice.revision,
                database="sales", schema="sales", generation=3,
            )
            table_scope = BoundManualTableScope.capture(
                composition.service, tree_target, table_node, tree_generation, target, choice,
            )
            table_session = create_manual_session(
                target, choice, table_scope=table_scope, profile_host=profile_host,
                connector=manual_connector, secret_resolver=resolve_manual_secret,
            )

            # A whole-server tree only authorizes the exact database signed
            # into the resolved child. A different but internally consistent
            # manual tab is rejected before resolving credentials or connecting.
            wrong_choice = profile_host.capture_connection(profile, database="other-db")
            wrong_target = replace(
                target, profile_revision=wrong_choice.revision, database="other-db", schema="other-db",
            )
            wrong_session = create_manual_session(
                wrong_target, wrong_choice, profile_host=profile_host,
                connector=manual_connector, secret_resolver=resolve_manual_secret,
            )
            with self.assertRaises(ManualSessionError) as wrong_database:
                wrong_session.open_table_source(
                    composition.service, tree_target, table_node,
                    tree_generation=tree_generation, choice=wrong_choice,
                ).result(timeout=3)
            self.assertEqual(wrong_database.exception.code, "TABLE_DATABASE_MISMATCH")

            with self.assertRaises(ManualSessionError):
                table_session.open_table_source(
                    composition.service, tree_target, replace(table_node, name="forged"),
                    tree_generation=tree_generation, choice=choice,
                ).result(timeout=3)
            wrong_revision_tree = replace(tree_target, profile_revision="stale-revision")
            with self.assertRaises(ManualSessionError):
                table_session.open_table_source(
                    composition.service, wrong_revision_tree, table_node,
                    tree_generation=tree_generation, choice=choice,
                ).result(timeout=3)
            self.assertEqual(manual_secret_calls, [])
            self.assertEqual(manual_connector_calls, [])

            opened_table = table_session.open_table_source(
                composition.service, tree_target, table_node,
                tree_generation=tree_generation, choice=choice,
            ).result(timeout=3)
            self.assertEqual(opened_table.metadata.signed_ref.database, "sales")
            self.assertEqual(opened_table.metadata.primary_key, ("id",))
            self.assertEqual(opened_table.page.rows, ((7, "manual row"),))
            self.assertEqual(manual_connector_calls[0][1]["database"], "sales")
            self.assertEqual(profile, original_profile)
            self.assertEqual(choice.revision, profile_host.capture_connection(profile, database="sales").revision)

            view_scope = BoundManualTableScope.capture(
                composition.service, tree_target, view_node, tree_generation, target, choice,
            )
            view_session = create_manual_session(
                target, choice, table_scope=view_scope, profile_host=profile_host,
                connector=manual_connector, secret_resolver=resolve_manual_secret,
            )
            opened_view = view_session.open_table_source(
                composition.service, tree_target, view_node,
                tree_generation=tree_generation, choice=choice,
            ).result(timeout=3)
            self.assertEqual(opened_view.metadata.signed_ref.database, "sales")
            self.assertFalse(opened_view.metadata.is_base_table)
            self.assertIsNone(opened_view.metadata.verified_changeset)
            self.assertEqual(opened_view.page.rows, ((7, "manual row"),))
            self.assertEqual(len(manual_connector_calls), 2)
            self.assertEqual(len(manual_secret_calls), 2)
            self.assertTrue(all(call[1]["database"] == "sales" for call in manual_connector_calls))

            # Refreshing the signed database's object list invalidates its
            # children, while an old ref still cannot trigger another manual
            # profile/secret/connector lookup.
            refreshed = composition.service.submit_children(
                tree_target, database_node, refresh=True, generation=tree_generation,
            ).future.result(timeout=3)
            self.assertEqual(refreshed.cache_state, "fresh")
            with self.assertRaises(ManualSessionError):
                table_session.open_table_source(
                    composition.service, tree_target, table_node,
                    tree_generation=tree_generation, choice=choice,
                ).result(timeout=3)
            self.assertEqual(len(manual_secret_calls), 2)
            self.assertEqual(len(manual_connector_calls), 2)
            self.assertEqual(len(readonly_secret_calls), len(readonly_connections))
        finally:
            if wrong_session is not None:
                wrong_session.close().result(timeout=3)
            if table_session is not None:
                table_session.close(disposition="cancel").result(timeout=3)
            if view_session is not None:
                view_session.close(disposition="cancel").result(timeout=3)
            self.assertTrue(composition.close().result(timeout=3).completed)

    def test_oracle_service_capture_opens_signed_schema_table_on_manual_owner(self):
        profile = _profile("oracle", "service.prod", "LOGIN")
        original_profile = dict(profile)
        readonly_events = []
        readonly_connections = []
        readonly_secret_calls = []

        def readonly_connector(provider, profile, secret, **options):
            readonly_events.append((threading.get_ident(), provider, dict(profile), secret, dict(options)))
            connection = _CatalogConnection(readonly_events)
            readonly_connections.append(connection)
            return connection

        relational = RelationalReadOnlyHost(
            profile_loader=lambda connection_id: profile if connection_id == profile["id"] else None,
            secret_loader=lambda _profile: (readonly_secret_calls.append(threading.get_ident()) or "TEMP-READONLY"),
            connector=readonly_connector,
        )
        composition = ObjectTreeComposition(relational_host=relational)
        profile_host = DataCenterProfileHost(connections_loader=lambda: [profile])
        manual_secret_calls = []
        manual_connector_calls = []
        manual_connections = []

        def manual_connector(loaded, secret):
            manual_connector_calls.append((threading.get_ident(), dict(loaded), secret))
            connection = _ManualTableConnection()
            manual_connections.append(connection)
            return connection

        def resolve_secret(token):
            manual_secret_calls.append((threading.get_ident(), token))
            return "TEMP-MANUAL"

        session = None
        try:
            tree_target = composition.submit_target_capture(profile["id"]).result(timeout=3)
            self.assertEqual(tree_target.database, "service.prod")
            root = composition.service.root(tree_target)
            schemas = composition.service.submit_children(tree_target, root, generation=31).future.result(timeout=3)
            schema_node = next(node for node in schemas.nodes if node.kind == "schema")
            self.assertEqual((schema_node.database, schema_node.schema), ("service.prod", "APP"))
            objects = composition.service.submit_children(tree_target, schema_node, generation=31).future.result(timeout=3)
            table_node = next(node for node in objects.nodes if node.kind == "table")
            self.assertEqual((table_node.database, table_node.schema, table_node.name), ("service.prod", "APP", "items"))

            choice = profile_host.capture_connection(
                profile, database="service.prod", schema_allowlist=("APP",),
            )
            target = ManualSessionTarget(
                "oracle-manual", choice.connection_id, choice.revision,
                database="service.prod", schema="APP", generation=5,
            )
            scope = BoundManualTableScope.capture(
                composition.service, tree_target, table_node, 31, target, choice,
            )
            session = create_manual_session(
                target, choice, table_scope=scope, profile_host=profile_host,
                connector=manual_connector, secret_resolver=resolve_secret,
            )
            opened = session.open_table_source(
                composition.service, tree_target, table_node,
                tree_generation=31, choice=choice,
            ).result(timeout=3)

            self.assertEqual(opened.metadata.signed_ref.database, "service.prod")
            self.assertEqual(opened.metadata.signed_ref.schema, "APP")
            self.assertEqual(opened.metadata.primary_key, ("id",))
            self.assertEqual(opened.page.rows, ((7,),))
            self.assertEqual(manual_connections[0].current_schema, "APP")
            manual_sql = [event[1] for event in manual_connections[0].events if event[0] == "execute"]
            self.assertIn('ALTER SESSION SET CURRENT_SCHEMA = "APP"', manual_sql)
            self.assertTrue(any("SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')" in sql for sql in manual_sql))
            self.assertEqual(manual_connector_calls[0][1]["database"], "service.prod")
            self.assertEqual(manual_connector_calls[0][1]["schema"], "LOGIN")
            self.assertEqual(profile, original_profile)
            self.assertEqual(len(manual_secret_calls), 1)
            self.assertEqual(len(manual_connector_calls), 1)
        finally:
            if session is not None:
                session.close(disposition="cancel").result(timeout=3)
            self.assertTrue(composition.close().result(timeout=3).completed)

    def test_batch_adapter_returns_cursor_closes_failures_and_never_finalizes_transaction(self):
        connection = _Connection(autocommit=False)
        driver = ManualRelationalDriver(connection, "mysql")
        cursor = driver.executemany("INSERT INTO t VALUES (%s)", [(1,), (2,)])
        self.assertEqual(cursor.rowcount, 2)
        self.assertFalse(cursor.closed)
        self.assertEqual([event[0] for event in connection.events].count("commit"), 0)
        self.assertEqual([event[0] for event in connection.events].count("rollback"), 0)
        wrong_thread_codes = []
        wrong_thread = threading.Thread(
            target=lambda: self._capture_driver_error(
                wrong_thread_codes,
                lambda: driver.executemany("INSERT INTO t VALUES (%s)", [(3,)]),
            )
        )
        wrong_thread.start()
        wrong_thread.join(timeout=3)
        self.assertFalse(wrong_thread.is_alive())
        self.assertEqual(wrong_thread_codes, ["OWNER_THREAD_REQUIRED"])
        self.assertEqual([event[0] for event in connection.events].count("cursor"), 1)

        class _FailingCursor(_Cursor):
            def executemany(self, _sql, _rows):
                raise RuntimeError("TEMP vendor detail must not escape")

        class _FailingConnection(_Connection):
            def cursor(self):
                self.cursor_value = _FailingCursor(self)
                return self.cursor_value

        failed_connection = _FailingConnection(autocommit=False)
        with self.assertRaises(ManualDriverError) as raised:
            ManualRelationalDriver(failed_connection, "mysql").executemany("INSERT INTO t VALUES (%s)", [(1,)])
        self.assertEqual(raised.exception.code, "BATCH_EXECUTION_FAILED")
        self.assertTrue(failed_connection.cursor_value.closed)
        self.assertFalse(any(event[0] in {"commit", "rollback"} for event in failed_connection.events))

        class _BrokenCursorConnection(_Connection):
            def cursor(self):
                raise RuntimeError("TEMP connection diagnostic")

        with self.assertRaises(ManualDriverError) as cursor_error:
            ManualRelationalDriver(_BrokenCursorConnection(autocommit=False), "mysql").executemany(
                "INSERT INTO t VALUES (%s)", [(1,)]
            )
        self.assertEqual(cursor_error.exception.code, "BATCH_EXECUTION_FAILED")
        self.assertNotIn("diagnostic", str(cursor_error.exception))

    @staticmethod
    def _capture_driver_error(output, callback):
        try:
            callback()
        except ManualDriverError as error:
            output.append(error.code)

    def test_oceanbase_numeric_batch_binds_respect_literals_comments_and_numeric_order(self):
        connection = _Connection(autocommit=False)
        driver = ManualRelationalDriver(connection, "oceanbase_oracle")
        sql = "INSERT INTO t(a,b) VALUES (:2, ':99') /* :81 */ WHERE c=:1 -- :44\n"
        cursor = driver.executemany(sql, [{"1": "first", 2: "second"}])
        event = next(item for item in connection.events if item[0] == "executemany")
        self.assertEqual(event[1], "INSERT INTO t(a,b) VALUES (?, ':99') /* :81 */ WHERE c=? -- :44\n")
        self.assertEqual(event[2], (("second", "first"),))
        self.assertEqual(cursor.rowcount, 1)
        self.assertFalse(any(item[0] in {"commit", "rollback"} for item in connection.events))

        before_cursors = len([event for event in connection.events if event[0] == "cursor"])
        with self.assertRaises(ManualDriverError) as mismatch:
            driver.executemany("INSERT INTO t VALUES (:2)", [(1,)])
        self.assertEqual(mismatch.exception.code, "BATCH_BIND_MISMATCH")
        self.assertEqual(len([event for event in connection.events if event[0] == "cursor"]), before_cursors)

    def test_oceanbase_numeric_batch_binds_skip_oracle_alternative_quotes(self):
        connection = _Connection(autocommit=False)
        driver = ManualRelationalDriver(connection, "oceanbase_oracle")
        sql = "INSERT INTO t VALUES (q'[it's :2]', :1, Q'{and :4}', nq'< :7 >', q'!!', :1)"
        driver.executemany(sql, [(9,)])
        event = next(item for item in connection.events if item[0] == "executemany")
        self.assertEqual(
            event[1],
            "INSERT INTO t VALUES (q'[it's :2]', ?, Q'{and :4}', nq'< :7 >', q'!!', ?)",
        )
        self.assertEqual(event[2], ((9, 9),))

        count = len([item for item in connection.events if item[0] == "cursor"])
        for malformed_sql in (
            "INSERT INTO t VALUES (q'[unfinished :1)",
            "INSERT INTO t VALUES (q'!', :1)",
            "INSERT INTO t VALUES (nq'!', :1)",
        ):
            with self.assertRaises(ManualDriverError) as malformed:
                driver.executemany(malformed_sql, [(1,)])
            self.assertEqual(malformed.exception.code, "BATCH_BIND_MISMATCH")
        self.assertEqual(len([item for item in connection.events if item[0] == "cursor"]), count)


if __name__ == "__main__":
    unittest.main()
