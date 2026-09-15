from __future__ import annotations

import unittest

from tools.data_center.sql_policy import SQLPolicy


class SQLPolicyTests(unittest.TestCase):
    def test_single_select_is_allowed(self) -> None:
        decision = SQLPolicy().validate(" SELECT id, name FROM users ", "mysql")

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.ok)
        self.assertEqual(decision.code, "OK")
        self.assertEqual(decision.sql, "SELECT id, name FROM users")
        self.assertEqual(decision.tables, ("users",))
        self.assertTrue(decision.evidence)
        self.assertEqual(decision.as_dict()["tables"], ["users"])

    def test_with_select_is_allowed_and_with_dml_is_rejected(self) -> None:
        allowed = SQLPolicy().validate(
            "WITH recent AS (SELECT id FROM users) SELECT id FROM recent",
            "mysql",
        )
        denied = SQLPolicy().validate(
            "WITH changed AS (INSERT INTO users (id) VALUES (1) RETURNING id) "
            "SELECT id FROM changed",
            "mysql",
        )

        self.assertTrue(allowed.allowed)
        self.assertFalse(denied.allowed)
        self.assertIn("node=Insert", denied.evidence)

    def test_writes_ddl_and_unsafe_select_forms_are_rejected(self) -> None:
        cases = {
            "INSERT": "INSERT INTO users (id) VALUES (1)",
            "UPDATE": "UPDATE users SET name = 'Ada'",
            "DELETE": "DELETE FROM users",
            "DDL": "CREATE TABLE users (id INT)",
            "multiple statements": "SELECT 1; SELECT 2",
            "FOR UPDATE": "SELECT id FROM users FOR UPDATE",
            "SELECT INTO": "SELECT id INTO archive_users FROM users",
        }

        for label, sql in cases.items():
            with self.subTest(label=label):
                decision = SQLPolicy().validate(sql, "mysql")
                self.assertFalse(decision.allowed)
                self.assertFalse(decision.ok)

    def test_empty_sql_is_rejected(self) -> None:
        decision = SQLPolicy().validate("   ", "mysql")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "ARGUMENT_INVALID")

    def test_comments_and_string_semicolons_are_not_multiple_statements(self) -> None:
        cases = (
            "SELECT 'first;second' AS value /* semicolon ; in comment */",
            "SELECT 1 -- semicolon ; in comment\n",
        )

        for sql in cases:
            with self.subTest(sql=sql):
                self.assertTrue(SQLPolicy().validate(sql, "mysql").allowed)

    def test_supported_dialect_aliases_are_mapped_and_others_rejected(self) -> None:
        for dialect in ("mysql", "oceanbase_mysql"):
            with self.subTest(dialect=dialect):
                decision = SQLPolicy().validate("SELECT 1", dialect)
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.dialect, "mysql")

        for dialect in ("oracle", "oceanbase_oracle", "dameng"):
            with self.subTest(dialect=dialect):
                decision = SQLPolicy().validate("SELECT 1 FROM dual", dialect)
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.dialect, "oracle")

        for dialect in ("postgres", "sqlite", "sqlserver", ""):
            with self.subTest(dialect=dialect):
                self.assertFalse(SQLPolicy().validate("SELECT 1", dialect).allowed)

    def test_parse_failure_is_rejected(self) -> None:
        decision = SQLPolicy().validate("SELECT FROM", "mysql")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "SQL_INVALID")

    def test_side_effect_blocking_and_external_functions_are_rejected(self) -> None:
        cases = (
            "SELECT SLEEP(1)",
            "SELECT BENCHMARK(1000, MD5('x'))",
            "SELECT GET_LOCK('x', 1)",
            "SELECT RELEASE_LOCK('x')",
            "SELECT RELEASE_ALL_LOCKS()",
            "SELECT LOAD_FILE('/tmp/file')",
            "SELECT UTL_HTTP.REQUEST('http://example.test') FROM dual",
            "SELECT UTL_FILE.FOPEN('x', 'r', 'r') FROM dual",
            "SELECT DBMS_LOCK.SLEEP(1) FROM dual",
            "SELECT DBMS_PIPE.RECEIVE_MESSAGE('x', 1) FROM dual",
            "SELECT DBMS_XMLGEN.GETXML('select 1 from dual') FROM dual",
            "SELECT DBMS_APPLICATION_INFO.SET_MODULE('agent', 'query') FROM dual",
            "SELECT SYS.DBMS_ASSERT.ENQUOTE_NAME('users') FROM dual",
            "SELECT DBMS_RANDOM.VALUE FROM dual",
            "SELECT DBMS_RANDOM/**/.VALUE FROM dual",
            'SELECT "DBMS_RANDOM".VALUE FROM dual',
            "SELECT UTL_CUSTOM.DO_SOMETHING('x') FROM dual",
            "SELECT APP_SCHEMA.MY_FUNC(id) FROM users",
            "SELECT MY_FUNC(id) FROM users",
            "SELECT 1 /*!40101 UNION SELECT 2 */",
            "SELECT sequence_name.NEXTVAL FROM dual",
            "SELECT @value := id FROM users",
        )

        for sql in cases:
            with self.subTest(sql=sql):
                self.assertFalse(SQLPolicy().validate(sql, "mysql").allowed)

    def test_mysql_connection_introspection_functions_remain_allowed(self) -> None:
        decision = SQLPolicy().validate("SELECT CONNECTION_ID(), IS_USED_LOCK('x')", "mysql")

        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
