from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QTextCursor

from tools.data_center.manual_driver import classify_mysql_statement, classify_oracle_statement
from tools.data_center.manual_session import StatementKind, _contains_transaction_control
from tools.data_center.sql_script import SqlScriptError, scan_sql, statement_at
from tests import test_data_center_query_page_size as host_fixture
from ui.sql_editor import SqlEditor


class SqlBoundaryTests(unittest.TestCase):
    def test_mysql_quotes_comments_and_positions_preserve_original_statement(self):
        text = "SELECT `a;b`, 'x;''y' FROM t;\n\n SELECT 2;\nUPDATE t SET v=1"
        script = scan_sql(text, "mysql")
        self.assertEqual(len(script.segments), 3)
        self.assertEqual(script.segments[0].sql, "SELECT `a;b`, 'x;''y' FROM t")
        self.assertEqual(statement_at(text, text.index("\n") + 1, "mysql"), "SELECT 2")
        self.assertEqual(classify_mysql_statement(script.segments[0].sql).kind, StatementKind.QUERY)
        self.assertEqual(classify_mysql_statement("SELECT `a;b`; UPDATE t SET v=1").kind, StatementKind.MULTI_STATEMENT)
        self.assertEqual(len(scan_sql("SELECT 1 # ; COMMIT\n; SELECT 2", "mysql").segments), 2)
        self.assertEqual(len(scan_sql("SELECT a#b; UPDATE t SET v=1", "oracle").segments), 2)
        self.assertEqual(statement_at("SELECT 1; -- only a comment", 18, "mysql"), "")

    def test_oracle_alternative_quotes_do_not_expose_literal_transaction_words(self):
        for literal in ("q'[can't; COMMIT; ]'", "nq'{a'; ROLLBACK;}'", "q'!a'; DELETE FROM t;!'", "q'<a; COMMIT;>'"):
            with self.subTest(literal=literal):
                sql = f"SELECT {literal} FROM dual"
                self.assertEqual(len(scan_sql(sql, "oracle").segments), 1)
                self.assertEqual(classify_oracle_statement(sql).kind, StatementKind.QUERY)
                self.assertFalse(_contains_transaction_control(sql))
                self.assertTrue(_contains_transaction_control(sql + "; COMMIT"))
                self.assertEqual(classify_oracle_statement(sql + "; COMMIT").kind, StatementKind.MULTI_STATEMENT)
        oracle_path = "SELECT 'path\\' FROM dual"
        self.assertEqual(classify_oracle_statement(oracle_path).kind, StatementKind.QUERY)
        self.assertFalse(_contains_transaction_control(oracle_path))
        self.assertEqual(classify_mysql_statement("SELECT q'[x'; COMMIT; SELECT 'y]'").kind, StatementKind.MULTI_STATEMENT)

    def test_incomplete_or_ambiguous_input_is_rejected_instead_of_guessing(self):
        cases = (("SELECT 'open", "mysql"), ('SELECT "open', "oracle"), ("SELECT `open", "mysql"), ("SELECT 1 /* open", "oracle"), ("SELECT q'[open", "oracle"), ("SELECT 'it\\'s'", "mysql"), ("SELECT 1 /*!; COMMIT */", "mysql"), ("SELECT 1 /*M!; COMMIT */", "mysql"))
        for sql, dialect in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(SqlScriptError):
                    scan_sql(sql, dialect)
        self.assertEqual(classify_mysql_statement("SELECT 1 /*!; COMMIT */").kind, StatementKind.MULTI_STATEMENT)

    def test_procedure_body_is_not_mistaken_for_a_standalone_statement(self):
        text = "/* draft */ CREATE PROCEDURE p() BEGIN UPDATE t SET v=1; DELETE FROM t; END; SELECT 1;"
        with self.assertRaises(SqlScriptError) as caught:
            statement_at(text, text.index("DELETE"), "mysql")
        self.assertEqual(caught.exception.code, "SQL_PROCEDURAL_SCRIPT_UNSUPPORTED")
        text = "WITH FUNCTION f RETURN NUMBER IS BEGIN NULL; DELETE FROM t; RETURN 1; END; SELECT f FROM dual"
        with self.assertRaises(SqlScriptError):
            statement_at(text, text.index("DELETE"), "oracle")
        sql = "SELECT 'CREATE PROCEDURE p; BEGIN; COMMIT;' FROM t"
        self.assertEqual(statement_at(sql, 0, "mysql"), sql)


class SqlTargetQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        host_fixture.QueryPageSizeTests.setUpClass()

    def setUp(self):
        self.fixture = host_fixture.QueryPageSizeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.state = self.fixture._new_tab("sql-target")
        self.host = self.fixture.host
        self.editor = SqlEditor(self.fixture.panel)
        self.fixture.panel._current_editor = lambda: self.editor
        self.confirm = patch("ui.confirm_dialog.confirm_action", return_value=False)
        self.confirm.start()
        self.addCleanup(self.confirm.stop)

    def _position(self, text, start, end=None):
        self.editor.setPlainText(text)
        cursor = self.editor.textCursor()
        cursor.setPosition(len(text[:start].encode("utf-16-le")) // 2)
        if end is not None:
            cursor.setPosition(len(text[:end].encode("utf-16-le")) // 2, QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)

    def test_emoji_before_boundary_executes_the_actual_current_statement(self):
        first = "SELECT '" + "🚗" * 8 + "'"
        text = first + ";\nUPDATE t SET value=1 WHERE id=1;"
        self._position(text, len(first))
        self.assertNotEqual(self.editor.textCursor().position(), len(first))
        self.assertEqual(self.editor.selected_or_all()[1], len(first))
        self.host.run_current()
        self.fixture._finish_page()
        executed = [event[2] for event in self.fixture.drivers["sql-target"].events if event[0] == "execute"]
        self.assertEqual(executed, [first])

    def test_explicit_select_outside_procedure_draft_uses_selection_not_whole_editor(self):
        sql = "SELECT value FROM t"
        text = "CREATE PROCEDURE p() BEGIN UPDATE t SET v=1; END;\n" + sql
        self._position(text, text.index(sql), len(text))
        self.host.run_current()
        self.fixture._finish_page()
        executed = [event[2] for event in self.fixture.drivers["sql-target"].events if event[0] == "execute"]
        self.assertEqual(executed, [sql])

    def test_unclosed_input_and_unselected_procedure_never_start_owner_io(self):
        for text in ("SELECT 'open; DELETE FROM t", "CREATE PROCEDURE p() BEGIN UPDATE t SET v=1; END;"):
            self._position(text, len(text) // 2)
            self.host.run_current()
        self.assertEqual(self.fixture.factories, [])
        self.assertEqual(self.fixture.watched, [])


if __name__ == "__main__":
    unittest.main()
