"""Target-bound DBAPI adapters for manual, explicit-transaction SQL sessions.

This module intentionally does not import a DB driver at import time.  Profile
loading, secret decryption, and connection construction happen only when the
``ManualSession`` owner thread invokes its factory.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import re
import threading
from typing import Any

from tools.data_center.manual_session import (
    ManualCursor,
    ManualSessionError,
    ManualSessionTarget,
    ManualSessionState,
    StatementClassification,
    StatementKind,
    TransactionEffect,
    MAX_RESULT_BYTES,
)
from tools.data_center.lob_values import detach_oracle_row
from tools.data_center.profile_host import ConnectionChoice, DataCenterProfileHost, ProfileHostError
from tools.data_center.sql_script import SqlScriptError, scan_sql, selected_procedural_sql


class ManualDriverError(ManualSessionError):
    """A safe adapter failure code; vendor error text is never retained."""

    def __init__(self, code: str):
        super().__init__(str(code or "MANUAL_DRIVER_FAILED"))


def _validate_direct_relational_profile(
    profile: Mapping[str, Any],
    *,
    allow_data_center_transport: bool = False,
    provider: str = "",
    mode: str = "",
) -> None:
    """Fail closed for transport options the direct hosts cannot honor."""

    typed_transport_keys = {
        "ssh_enabled", "ssh_host", "ssh_port", "ssh_username", "ssh_auth_method",
        "ssh_password_token", "ssh_private_key_path", "ssh_key_passphrase_token",
        "tls_enabled", "tls_ca_path", "tls_client_cert_path", "tls_client_key_path",
        "tls_client_key_password_token",
    }
    from tools.data_center.connection_transport import (
        ConnectionTransportError,
        DataCenterConnectionTransport,
    )

    try:
        transport = DataCenterConnectionTransport.from_profile(profile, provider, mode)
    except ConnectionTransportError as error:
        raise ManualDriverError(error.code) from None
    if transport is not None and not allow_data_center_transport:
        raise ManualDriverError("MANUAL_PROVIDER_UNSUPPORTED")
    checked_profile = (
        {key: value for key, value in profile.items() if key not in typed_transport_keys}
        if allow_data_center_transport else profile
    )

    # Keep this list aligned with the read-only host's direct-connector policy.
    # These options are deliberately rejected rather than silently downgraded.
    unsupported = (
        "dsn", "uri", "url", "connection_string", "connect_string",
        "ssh", "ssh_tunnel", "ssh_host", "ssh_port", "ssh_username", "ssh_user",
        "ssh_password", "ssh_password_token", "ssh_key", "ssh_key_path",
        "ssl", "tls", "ssl_ca", "ssl_cert", "ssl_key", "ssl_key_password",
        "ssl_verify_cert", "ssl_verify_identity", "ssl_disabled",
        "ssl_server_dn_match", "ssl_server_cert_dn", "wallet_location",
        "wallet_password", "ssl_path", "ssl_pwd", "protocol",
        "access_token",
    )
    if any(key in checked_profile and checked_profile.get(key) not in (None, "", False, 0, {}, [], ()) for key in unsupported):
        raise ManualDriverError("MANUAL_PROVIDER_UNSUPPORTED")


def _secret_token(profile: Mapping[str, Any]) -> Any:
    for key in (
        "password", "password_ciphertext", "password_token", "secret_ciphertext",
        "secret_token", "credential_token",
    ):
        if key in profile:
            return profile.get(key)
    return ""


def _connect_direct_relational(
    choice: ConnectionChoice,
    profile: Mapping[str, Any],
    secret: Any,
    *,
    connect_timeout: float,
    read_timeout: float,
) -> Any:
    """Use the existing direct connector without creating a read-only lease."""
    try:
        from tools.data_center.readonly_relational_connectors import connect_relational
        return connect_relational(
            choice.provider,
            profile,
            secret,
            mode=choice.mode,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            connection_id=choice.connection_id,
            profile_revision=choice.revision,
        )
    except Exception as error:
        code = getattr(error, "code", "")
        if code in {
            "UNSUPPORTED_TRANSPORT", "UNSUPPORTED_ORACLE_DSN", "ODBC_SCHEMA_CONFIRM_REQUIRED",
            "ODBC_DRIVER_UNAVAILABLE", "ODBC_DRIVER_AMBIGUOUS", "ODBC_STATEMENT_TIMEOUT_UNAVAILABLE",
        }:
            raise ManualDriverError("MANUAL_PROVIDER_UNSUPPORTED") from None
        raise ManualDriverError("CONNECT_FAILED") from None


class _DetachedOracleCursor:
    """One-row fetch keeps materialization bounded independently of page size."""

    def __init__(self, cursor, owner):
        self._cursor = cursor
        self._owner = owner

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def fetchmany(self, size):
        self._owner._require_owner_thread()
        if self._owner._lob_cancelled.is_set():
            raise ManualDriverError('CANCELLED')
        rows = self._cursor.fetchmany(1)
        return tuple(detach_oracle_row(row, max_bytes=MAX_RESULT_BYTES,
                                      cancelled=self._owner._lob_cancelled.is_set) for row in rows)

    def close(self):
        self._owner._require_owner_thread()
        return self._cursor.close()


class ManualRelationalDriver:
    """DBAPI bridge; every ordinary call is made by ManualSession's owner."""

    def __init__(
        self,
        connection: Any,
        provider: str,
        *,
        schema: str = "",
        explicit_schema: bool = False,
        exact_schema: bool = False,
        scope_validator: Callable[[], Any] | None = None,
    ):
        self._connection = connection
        self._provider = str(provider or "").casefold()
        self._schema = str(schema or "")
        self._explicit_schema = bool(explicit_schema)
        self._exact_schema = bool(exact_schema)
        self._scope_validator = scope_validator
        self._scope_invalid_code = ""
        self._scope_configured = False
        self._owner_thread_id = threading.get_ident()
        self._lob_cancelled = threading.Event()

    def configure_manual_commit(self) -> None:
        self._require_owner_thread()
        self._validate_scope()
        connection = self._connection
        current = getattr(connection, "get_autocommit", None)
        if callable(current):
            enabled = bool(current())
        else:
            enabled = bool(getattr(connection, "autocommit", True))
        if enabled:
            switch = getattr(connection, "autocommit", None)
            if callable(switch):
                switch(False)
            else:
                try:
                    setattr(connection, "autocommit", False)
                except Exception:
                    raise ManualDriverError("AUTOCOMMIT_CONTROL_UNAVAILABLE") from None
        current = getattr(connection, "get_autocommit", None)
        if callable(current):
            enabled = bool(current())
        else:
            enabled = bool(getattr(connection, "autocommit", True))
        if enabled:
            raise ManualDriverError("AUTOCOMMIT_DISABLE_UNVERIFIED")
        self._validate_scope()
        if self._provider in {"oracle", "oceanbase_oracle"} and self._schema:
            self._configure_oracle_schema()
        self._scope_configured = True

    def _configure_oracle_schema(self) -> None:
        expected = self._schema if self._exact_schema else str(self._schema or "").strip()
        if not expected or "\x00" in expected:
            raise ManualDriverError("PROFILE_MISMATCH")
        actual = self._read_oracle_current_schema()
        self._validate_scope()
        if actual == expected:
            return
        if not self._explicit_schema:
            # A default login-owner capture is verified, never silently changed.
            raise ManualDriverError("PROFILE_MISMATCH")
        self._validate_scope()
        statement = (
            "ALTER SESSION SET CURRENT_SCHEMA = "
            f"{_oracle_identifier_sql(self._schema, preserve_whitespace=self._exact_schema)}"
        )
        self._execute_control(statement)
        if self._read_oracle_current_schema() != expected:
            raise ManualDriverError("PROFILE_MISMATCH")

    def _read_oracle_current_schema(self) -> str:
        cursor = None
        try:
            cursor = self._connection.cursor()
            cursor.execute("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL")
            fetchone = getattr(cursor, "fetchone", None)
            row = fetchone() if callable(fetchone) else None
            if row is None:
                fetchmany = getattr(cursor, "fetchmany", None)
                rows = fetchmany(1) if callable(fetchmany) else ()
                row = rows[0] if rows else None
            value = row[0] if isinstance(row, (tuple, list)) and row else None
            if value is None:
                raise ManualDriverError("PROFILE_MISMATCH")
            value_text = str(value)
            return value_text if self._exact_schema else value_text.strip()
        except ManualDriverError:
            raise
        except Exception:
            raise ManualDriverError("PROFILE_MISMATCH") from None
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    def execute(self, sql: str, parameters: Any) -> Any:
        self._require_owner_thread()
        self._validate_scope()
        self._lob_cancelled.clear()
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql) if parameters is None else cursor.execute(sql, parameters)
        except Exception:
            try:
                cursor.close()
            except Exception:
                pass
            raise
        if self._provider in {"oracle", "oceanbase_oracle"}:
            return _DetachedOracleCursor(cursor, self)
        return cursor

    def request_interrupt(self) -> None:
        """Only signal cooperative LOB cancellation; no foreign-thread DB call."""
        self._lob_cancelled.set()

    def validate_block(self, sql: str, provider: str) -> None:
        self._require_owner_thread()
        self._validate_scope()
        if provider != self._provider or selected_procedural_sql(sql, provider) != sql:
            raise ManualDriverError("BLOCK_TARGET_MISMATCH")
        if self._provider in {"mysql", "oceanbase_mysql"}:
            # CLIENT.MULTI_STATEMENTS (1 << 16) must stay disabled. The server
            # alone parses semicolons in a routine's single CREATE statement.
            flags = getattr(self._connection, "client_flag", None)
            if type(flags) is not int or flags & (1 << 16):
                raise ManualDriverError("BLOCK_SINGLE_STATEMENT_REQUIRED")

    def execute_block(self, sql: str, provider: str) -> Any:
        """Send one reviewed block to the vendor, without client SQL splitting."""
        self.validate_block(sql, provider)
        cursor = self.execute(sql, None)
        try:
            return cursor.rowcount
        finally:
            cursor.close()  # drains any driver results on this owner

    def block_transaction_state(self) -> ManualSessionState:
        """Inspect the current transaction only; never infer a successful commit."""
        self._require_owner_thread()
        try:
            self._validate_scope()
            connection = self._connection
            getter = getattr(connection, "get_autocommit", None)
            autocommit = getter() if callable(getter) else getattr(connection, "autocommit", None)
            if autocommit is not False:
                return ManualSessionState.UNKNOWN
            if self._provider in {"mysql", "oceanbase_mysql"}:
                status = getattr(connection, "server_status", None)
                if type(status) is int:
                    return ManualSessionState.DIRTY if status & 1 else ManualSessionState.CLEAN
                return ManualSessionState.UNKNOWN
            active = getattr(connection, "transaction_in_progress", None)
            if type(active) is bool:
                return ManualSessionState.DIRTY if active else ManualSessionState.CLEAN
            # Oracle's default create_transaction=FALSE does not create a new
            # transaction: docs.oracle.com/en/database/oracle/oracle-database/21/arpls/DBMS_TRANSACTION.html
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT DBMS_TRANSACTION.LOCAL_TRANSACTION_ID FROM DUAL")
                row = cursor.fetchone()
                if row is not None and len(row) == 1:
                    if row[0] is None:
                        return ManualSessionState.CLEAN
                    if type(row[0]) is str and row[0]:
                        return ManualSessionState.DIRTY
            finally:
                cursor.close()
        except Exception:
            pass
        return ManualSessionState.UNKNOWN

    def executemany(
        self,
        sql: str,
        rows: Sequence[Sequence[Any] | Mapping[str | int, Any]],
    ) -> ManualCursor:
        """Run a bound DML batch on the caller's existing owner connection.

        This adapter deliberately returns the cursor because DBAPI driver
        ``executemany`` return values differ. It never finalizes a transaction.
        """

        self._require_owner_thread()
        self._validate_scope()
        if not isinstance(sql, str) or not sql.strip():
            raise ManualDriverError("BATCH_ARGUMENT_INVALID")
        if (
            not isinstance(rows, Sequence)
            or isinstance(rows, (str, bytes, bytearray))
            or not rows
        ):
            raise ManualDriverError("BATCH_ARGUMENT_INVALID")
        batch_sql = sql
        batch_rows = rows
        if self._provider == "oceanbase_oracle":
            batch_sql, batch_rows = _translate_numeric_batch(sql, rows)
        try:
            cursor = self._connection.cursor()
        except ManualDriverError:
            raise
        except Exception:
            raise ManualDriverError("BATCH_EXECUTION_FAILED") from None
        try:
            run = getattr(cursor, "executemany", None)
            if not callable(run):
                raise ManualDriverError("BATCH_EXECUTION_UNSUPPORTED")
            run(batch_sql, batch_rows)
        except ManualDriverError:
            try:
                cursor.close()
            except Exception:
                pass
            raise
        except Exception:
            try:
                cursor.close()
            except Exception:
                pass
            raise ManualDriverError("BATCH_EXECUTION_FAILED") from None
        return cursor

    def commit(self) -> Any:
        self._require_owner_thread()
        # A signed-ref refresh cannot change the final decision for a
        # transaction already opened by this configured owner.
        if not (self._scope_validator is not None and self._scope_configured):
            self._validate_scope()
        return self._connection.commit()

    def rollback(self) -> Any:
        self._require_owner_thread()
        # Once setup succeeded, an old signed tree row must not prevent the
        # owner from explicitly rolling back work already opened in this
        # transaction. A driver that never finished setup remains cleanup-only.
        if not (self._scope_validator is not None and self._scope_configured):
            self._validate_scope()
        return self._connection.rollback()

    def close(self) -> Any:
        self._require_owner_thread()
        return self._connection.close()

    def ddl_transaction_behavior(self, sql: str):
        from tools.data_center.manual_session import DdlTransactionBehavior

        text = _mask_sql(sql).lstrip()
        if self._provider in {"mysql", "oceanbase_mysql"} and text.upper().startswith("CREATE TEMPORARY "):
            return DdlTransactionBehavior.UNKNOWN
        return DdlTransactionBehavior.IMPLICIT_COMMIT

    def create_savepoint(self, name: str) -> None:
        safe_name = _savepoint_name(name)
        quoted = f"`{safe_name}`" if self._provider in {"mysql", "oceanbase_mysql"} else safe_name
        self._execute_control(f"SAVEPOINT {quoted}")

    def rollback_to_savepoint(self, name: str) -> None:
        safe_name = _savepoint_name(name)
        quoted = f"`{safe_name}`" if self._provider in {"mysql", "oceanbase_mysql"} else safe_name
        suffix = " SAVEPOINT" if self._provider in {"mysql", "oceanbase_mysql"} else ""
        self._execute_control(f"ROLLBACK TO{suffix} {quoted}", allow_stale_scope=True)

    def release_savepoint(self, name: str) -> None:
        self._require_owner_thread()
        if not (self._scope_validator is not None and self._scope_configured):
            self._validate_scope()
        safe_name = _savepoint_name(name)
        if self._provider in {"oracle", "oceanbase_oracle"}:
            # Oracle drops savepoints on commit; RELEASE SAVEPOINT is not SQL.
            return
        self._execute_control(f"RELEASE SAVEPOINT `{safe_name}`", allow_stale_scope=True)

    def _execute_control(self, sql: str, *, allow_stale_scope: bool = False) -> None:
        self._require_owner_thread()
        if not (allow_stale_scope and self._scope_validator is not None and self._scope_configured):
            self._validate_scope()
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql)
        finally:
            cursor.close()

    def _validate_scope(self) -> None:
        if self._scope_validator is None:
            return
        if self._scope_invalid_code:
            raise ManualDriverError(self._scope_invalid_code)
        try:
            self._scope_validator()
        except ManualDriverError as error:
            self._scope_invalid_code = error.code or "TABLE_SCOPE_INVALID"
            raise ManualDriverError(self._scope_invalid_code) from None
        except Exception:
            self._scope_invalid_code = "TABLE_SCOPE_INVALID"
            raise ManualDriverError("TABLE_SCOPE_INVALID") from None

    def _require_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ManualDriverError("OWNER_THREAD_REQUIRED")


def _translate_numeric_batch(
    sql: str,
    rows: Sequence[Sequence[Any] | Mapping[str | int, Any]],
) -> tuple[str, tuple[tuple[Any, ...], ...] | Sequence[Sequence[Any] | Mapping[str, Any]]]:
    """Translate Oracle numeric binds for the OceanBase ODBC executemany path.

    The connection's ODBC cursor proxy translates ``execute`` calls, but its
    generic ``executemany`` forwarding cannot rewrite one parameter row at a
    time. This mirrors that bind policy while leaving quoted text and comments
    untouched.
    """

    text = str(sql)
    output: list[str] = []
    bind_indexes: list[int] = []
    state = "normal"
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if state == "normal":
            alt_quote = _oracle_alt_quote_end(text, index)
            if alt_quote is not None:
                terminator, content_start = alt_quote
                end = text.find(terminator, content_start)
                if end < 0:
                    raise ManualDriverError("BATCH_BIND_MISMATCH")
                stop = end + len(terminator)
                output.append(text[index:stop])
                index = stop
                continue
            if char == "'":
                output.append(char)
                state = "single"
                index += 1
                continue
            if char == '"':
                output.append(char)
                state = "double"
                index += 1
                continue
            if char == "`":
                output.append(char)
                state = "backtick"
                index += 1
                continue
            if char == "-" and next_char == "-":
                output.append("--")
                state = "line_comment"
                index += 2
                continue
            if char == "/" and next_char == "*":
                output.append("/*")
                state = "block_comment"
                index += 2
                continue
            if char == ":" and next_char in "123456789":
                previous = text[index - 1] if index else ""
                end = index + 2
                while end < len(text) and text[end].isdigit():
                    end += 1
                following = text[end] if end < len(text) else ""
                if (
                    previous != ":"
                    and not (previous and (previous.isalnum() or previous in "_$#"))
                    and not (following and (following.isalnum() or following in "_$#"))
                ):
                    try:
                        bind_indexes.append(int(text[index + 1 : end]))
                    except (ValueError, OverflowError):
                        raise ManualDriverError("BATCH_BIND_MISMATCH") from None
                    output.append("?")
                    index = end
                    continue
            output.append(char)
            index += 1
            continue

        if state == "line_comment":
            output.append(char)
            if char in "\r\n":
                state = "normal"
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and next_char == "/":
                output.append("*/")
                state = "normal"
                index += 2
            else:
                output.append(char)
                index += 1
            continue

        quote = {"single": "'", "double": '"', "backtick": "`"}[state]
        output.append(char)
        if char == quote:
            if next_char == quote:
                output.append(next_char)
                index += 2
                continue
            state = "normal"
        index += 1

    if not bind_indexes:
        return text, rows

    converted: list[tuple[Any, ...]] = []
    for row in rows:
        values: list[Any] = []
        for bind_index in bind_indexes:
            if isinstance(row, Mapping):
                if str(bind_index) in row:
                    values.append(row[str(bind_index)])
                elif bind_index in row:
                    values.append(row[bind_index])
                else:
                    raise ManualDriverError("BATCH_BIND_MISMATCH")
            elif isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
                if bind_index > len(row):
                    raise ManualDriverError("BATCH_BIND_MISMATCH")
                values.append(row[bind_index - 1])
            else:
                raise ManualDriverError("BATCH_BIND_MISMATCH")
        converted.append(tuple(values))
    return "".join(output), tuple(converted)


def _oracle_alt_quote_end(text: str, index: int) -> tuple[str, int] | None:
    """Return an Oracle q-quote terminator at ``index`` when present.

    Oracle's alternative quoting form is q'X...X' (or q'[... ]' with paired
    delimiters); NQ'...' is the national-character variant. Bind-like text in
    the literal must never be interpreted as an ODBC parameter.
    """

    if index >= len(text):
        return None
    previous = text[index - 1] if index else ""
    if previous and (previous.isalnum() or previous in "_$#"):
        return None
    first = text[index].casefold()
    if first == "n" and index + 2 < len(text) and text[index + 1].casefold() == "q":
        quote_index = index + 2
    elif first == "q":
        quote_index = index + 1
    else:
        return None
    if quote_index + 1 >= len(text) or text[quote_index] != "'":
        return None
    opening = text[quote_index + 1]
    closing = {"[": "]", "{": "}", "<": ">", "(": ")"}.get(opening, opening)
    return closing + "'", quote_index + 2


def _savepoint_name(name: str) -> str:
    value = str(name or "")
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in value):
        raise ManualDriverError("INVALID_SAVEPOINT")
    return value


def _mask_sql(sql: str) -> str:
    """Mask comments and quoted literals while keeping words and separators."""

    text = str(sql or "")
    result = list(text)
    index = 0
    state = "normal"
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if state == "normal":
            if char == "'":
                result[index] = " "
                state = "single"
            elif char == '"':
                result[index] = " "
                state = "double"
            elif char == "`":
                result[index] = " "
                state = "backtick"
            elif char == "-" and next_char == "-":
                result[index] = result[index + 1] = " "
                index += 1
                state = "line"
            elif char == "/" and next_char == "*":
                result[index] = result[index + 1] = " "
                index += 1
                state = "block"
        elif state in {"single", "double", "backtick"}:
            result[index] = " "
            closing = {"single": "'", "double": '"', "backtick": "`"}[state]
            if char == closing:
                if next_char == closing:
                    result[index + 1] = " "
                    index += 1
                else:
                    state = "normal"
        elif state == "line":
            if char not in "\r\n":
                result[index] = " "
            else:
                state = "normal"
        elif state == "block":
            if char == "*" and next_char == "/":
                result[index] = result[index + 1] = " "
                index += 1
                state = "normal"
            elif char not in "\r\n":
                result[index] = " "
        index += 1
    return "".join(result)


def classify_mysql_statement(sql: str) -> StatementClassification:
    """Declare both SQL kind and transaction effect for MySQL-family syntax."""

    from tools.data_center.manual_session import DdlTransactionBehavior

    text = str(sql or "").strip()
    if not text:
        return StatementClassification(StatementKind.OTHER, TransactionEffect.UNKNOWN)
    try:
        script = scan_sql(text, "mysql")
    except SqlScriptError:
        return StatementClassification(StatementKind.MULTI_STATEMENT, TransactionEffect.UNKNOWN)
    if len(script.segments) != 1:
        return StatementClassification(StatementKind.MULTI_STATEMENT, TransactionEffect.UNKNOWN)
    segment = script.segments[0]
    masked = script.code[segment.start:segment.end].strip()
    upper = " ".join(masked.upper().split())
    first = upper.split(" ", 1)[0] if upper else ""

    if first in {"COMMIT", "ROLLBACK", "BEGIN", "SAVEPOINT", "RELEASE", "START"} or (
        first == "SET" and _is_transaction_control_set(upper)
    ):
        return StatementClassification(StatementKind.TRANSACTION_CONTROL, TransactionEffect.UNKNOWN)
    if first == "WITH":
        if any(word in upper for word in (" INSERT ", " UPDATE ", " DELETE ", " REPLACE ", " MERGE ")):
            return StatementClassification(StatementKind.DML, TransactionEffect.DIRTY)
        if re_search(r"\b(SELECT)\b", upper):
            if re_search(r"\b(FOR\s+UPDATE|LOCK\s+IN\s+SHARE\s+MODE)\b", upper):
                return StatementClassification(StatementKind.QUERY, TransactionEffect.DIRTY)
            if re_search(r"\bINTO\b", upper):
                return StatementClassification(StatementKind.QUERY, TransactionEffect.DIRTY)
            return StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE)
        return StatementClassification(StatementKind.OTHER, TransactionEffect.UNKNOWN)
    if first == "SELECT":
        if re_search(r"\b(FOR\s+UPDATE|LOCK\s+IN\s+SHARE\s+MODE)\b", upper):
            return StatementClassification(StatementKind.QUERY, TransactionEffect.DIRTY)
        if re_search(r"\bINTO\b", upper):
            return StatementClassification(StatementKind.QUERY, TransactionEffect.DIRTY)
        return StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE)
    if first in {"INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE"}:
        return StatementClassification(StatementKind.DML, TransactionEffect.DIRTY)
    if first in {"CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME"}:
        behavior = DdlTransactionBehavior.UNKNOWN if upper.startswith("CREATE TEMPORARY ") else DdlTransactionBehavior.IMPLICIT_COMMIT
        effect = TransactionEffect.UNKNOWN if behavior is DdlTransactionBehavior.UNKNOWN else TransactionEffect.IMPLICIT_COMMIT
        return StatementClassification(StatementKind.DDL, effect)
    if first in {"GRANT", "REVOKE"}:
        return StatementClassification(StatementKind.DCL, TransactionEffect.IMPLICIT_COMMIT)
    return StatementClassification(StatementKind.OTHER, TransactionEffect.UNKNOWN)


def re_search(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _is_transaction_control_set(statement: str) -> bool:
    """Recognize SET forms that can alter autocommit or transaction scope."""
    return re_search(
        r"^SET\s+(?:(?:SESSION|LOCAL|GLOBAL)\s+)?"
        r"(?:@@(?:(?:SESSION|GLOBAL)\.)?)?(?:AUTOCOMMIT|TRANSACTION)\b",
        statement,
    )


def classify_oracle_statement(sql: str) -> StatementClassification:
    """Conservative Oracle-family transaction classification."""
    text = str(sql or "").strip()
    if not text:
        return StatementClassification(StatementKind.OTHER, TransactionEffect.UNKNOWN)
    try:
        script = scan_sql(text, "oracle")
    except SqlScriptError:
        return StatementClassification(StatementKind.MULTI_STATEMENT, TransactionEffect.UNKNOWN)
    if len(script.segments) != 1:
        return StatementClassification(StatementKind.MULTI_STATEMENT, TransactionEffect.UNKNOWN)
    segment = script.segments[0]
    masked = script.code[segment.start:segment.end].strip()
    upper = " ".join(masked.upper().split())
    first = upper.split(" ", 1)[0] if upper else ""

    if first in {"COMMIT", "ROLLBACK", "SAVEPOINT", "BEGIN", "DECLARE", "START"} or (
        first == "SET" and _is_transaction_control_set(upper)
    ):
        return StatementClassification(StatementKind.TRANSACTION_CONTROL, TransactionEffect.UNKNOWN)
    if first == "WITH":
        if re_search(r"\b(INSERT|UPDATE|DELETE|MERGE)\b", upper):
            return StatementClassification(StatementKind.DML, TransactionEffect.DIRTY)
        if re_search(r"\bSELECT\b", upper):
            if re_search(r"\b(FOR\s+UPDATE|INTO)\b", upper):
                return StatementClassification(StatementKind.QUERY, TransactionEffect.DIRTY)
            return StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE)
        return StatementClassification(StatementKind.OTHER, TransactionEffect.UNKNOWN)
    if first == "SELECT":
        if re_search(r"\b(FOR\s+UPDATE|INTO)\b", upper):
            return StatementClassification(StatementKind.QUERY, TransactionEffect.DIRTY)
        return StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE)
    if first in {"INSERT", "UPDATE", "DELETE", "MERGE"}:
        return StatementClassification(StatementKind.DML, TransactionEffect.DIRTY)
    if first in {"CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME", "COMMENT"}:
        return StatementClassification(StatementKind.DDL, TransactionEffect.IMPLICIT_COMMIT)
    if first in {"GRANT", "REVOKE"}:
        return StatementClassification(StatementKind.DCL, TransactionEffect.IMPLICIT_COMMIT)
    return StatementClassification(StatementKind.OTHER, TransactionEffect.UNKNOWN)


def classify_relational_statement(sql: str, provider: str) -> StatementClassification:
    value = str(provider or "").casefold()
    if value in {"mysql", "oceanbase_mysql"}:
        return classify_mysql_statement(sql)
    if value in {"oracle", "oceanbase_oracle"}:
        return classify_oracle_statement(sql)
    return StatementClassification(StatementKind.OTHER, TransactionEffect.UNKNOWN)


class RelationalManualDriverFactory:
    """Resolve a fixed relational profile and secret inside the session owner."""

    def __init__(
        self,
        choice: ConnectionChoice,
        *,
        profile_host: DataCenterProfileHost | None = None,
        connector: Callable[[Mapping[str, Any], Any], Any] | None = None,
        secret_resolver: Callable[[Any], Any] | None = None,
        connect_timeout: float = 8.0,
        read_timeout: float = 15.0,
        table_scope: Any | None = None,
    ) -> None:
        self.choice = choice
        self.table_scope = table_scope
        if choice.provider not in {"mysql", "oracle", "oceanbase_mysql", "oceanbase_oracle"}:
            raise ManualDriverError("MANUAL_PROVIDER_UNSUPPORTED")
        if table_scope is not None and (
            getattr(table_scope, "choice", None) != choice
            or not callable(getattr(table_scope, "validate_current", None))
        ):
            raise ManualDriverError("TABLE_SCOPE_MISMATCH")
        self.profile_host = profile_host or DataCenterProfileHost()
        self.connector = connector
        self.secret_resolver = secret_resolver or self._decrypt_secret
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)

    @staticmethod
    def _decrypt_secret(value: Any) -> Any:
        from tools.secure_store import decrypt_secret

        if not value:
            return ""
        try:
            result = decrypt_secret(value)
        except Exception:
            raise ManualDriverError("SECRET_UNAVAILABLE") from None
        if result is None or str(result) == "":
            raise ManualDriverError("SECRET_UNAVAILABLE")
        return result

    def __call__(self, target: ManualSessionTarget) -> ManualRelationalDriver:
        expected = self.choice
        if (
            target.connection_id != expected.connection_id
            or target.profile_revision != expected.revision
            or target.database != expected.database
        ):
            raise ManualDriverError("TARGET_MISMATCH")
        table_ref = self._validate_table_scope(target) if self.table_scope is not None else None
        try:
            profile = self.profile_host.load_connection_profile(target.connection_id, target.profile_revision)
            current = self.profile_host.capture_connection(profile, database=target.database)
        except ProfileHostError as error:
            raise ManualDriverError(error.code) from None
        if (
            current.connection_id != expected.connection_id
            or current.revision != expected.revision
            or current.provider != expected.provider
            or current.mode != expected.mode
            or current.dialect != expected.dialect
            or current.database != target.database
        ):
            raise ManualDriverError("PROFILE_MISMATCH")
        if self.table_scope is not None:
            table_ref = self._validate_table_scope(target)
            schema = table_ref.schema if expected.provider in {"oracle", "oceanbase_oracle"} else profile_schema_for_provider(profile, expected.provider)
            explicit_schema = expected.provider in {"oracle", "oceanbase_oracle"}
        else:
            schema = profile_schema_for_provider(profile, expected.provider)
            if target.schema != schema:
                raise ManualDriverError("PROFILE_MISMATCH")
            explicit_schema = _has_oracle_explicit_schema(profile) if expected.provider in {
                "oracle", "oceanbase_oracle"
            } else False
        _validate_direct_relational_profile(
            profile,
            allow_data_center_transport=self.connector is None,
            provider=expected.provider,
            mode=expected.mode,
        )
        if self.table_scope is not None:
            table_ref = self._validate_table_scope(target)
        secret = self.secret_resolver(_secret_token(profile))
        if self.table_scope is not None:
            table_ref = self._validate_table_scope(target)
            if expected.provider in {"mysql", "oceanbase_mysql"}:
                # Bind the selected database only in the transient connector
                # input; the loaded profile and its revision remain untouched.
                connector_profile = dict(profile)
                connector_profile["database"] = table_ref.database
            else:
                connector_profile = profile
        else:
            connector_profile = profile
        try:
            if self.connector is None:
                connection = _connect_direct_relational(
                    expected,
                    connector_profile,
                    secret,
                    connect_timeout=self.connect_timeout,
                    read_timeout=self.read_timeout,
                )
            else:
                connection = self.connector(connector_profile, secret)
        except ManualDriverError:
            raise
        except Exception:
            raise ManualDriverError("CONNECT_FAILED") from None
        if connection is None:
            raise ManualDriverError("CONNECT_FAILED")
        scope_validator = None
        if self.table_scope is not None:
            if expected.provider in {"oracle", "oceanbase_oracle"}:
                schema = table_ref.schema
            # The returned driver becomes the owner-thread cleanup handle.
            # Its scope validator runs before configure and every public
            # operation, so a scope invalidated by connector code makes it
            # cleanup-only without losing the live connection reference here.
            scope_validator = lambda: self._validate_table_scope(target)
        return ManualRelationalDriver(
            connection,
            expected.provider,
            schema=schema,
            explicit_schema=explicit_schema,
            exact_schema=self.table_scope is not None and expected.provider in {"oracle", "oceanbase_oracle"},
            scope_validator=scope_validator if self.table_scope is not None else None,
        )

    def _validate_table_scope(self, target: ManualSessionTarget) -> Any:
        if self.table_scope is None:
            return None
        try:
            return self.table_scope.validate_current(target, self.choice)
        except ManualDriverError:
            raise
        except Exception:
            raise ManualDriverError("TABLE_SCOPE_INVALID") from None


class MySqlManualDriver(ManualRelationalDriver):
    """Compatibility name for the MySQL DBAPI bridge."""

    def __init__(self, connection: Any):
        super().__init__(connection, "mysql")


class MySqlManualDriverFactory(RelationalManualDriverFactory):
    """Compatibility name for the original MySQL-specific factory."""


def profile_schema_for_provider(profile: Mapping[str, Any], provider: str) -> str:
    """Capture the name-resolution scope without confusing Oracle service and owner."""
    selected = str(provider or "").strip().casefold().replace("-", "_")
    if selected in {"mysql", "oceanbase_mysql"}:
        return _first_profile_value(
            profile, ("database", "db_name", "database_name", "schema")
        )
    if selected in {"oracle", "oceanbase_oracle"}:
        explicit = _first_profile_value(profile, ("schema", "owner"))
        if explicit:
            return _normalize_oracle_identifier(explicit, allow_quoted=True)
        username = _first_profile_value(profile, ("username", "user"))
        if not username:
            return ""
        try:
            return _normalize_oracle_identifier(username, allow_quoted=True)
        except ManualDriverError:
            # Preserve an empty, honest scope rather than guessing from service/database.
            return ""
    return ""


def _first_profile_value(profile: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(profile.get(key) or "").strip()
        if value:
            return value
    return ""


def _has_oracle_explicit_schema(profile: Mapping[str, Any]) -> bool:
    return bool(_first_profile_value(profile, ("schema", "owner")))


def _normalize_oracle_identifier(value: Any, *, allow_quoted: bool) -> str:
    text = str(value or "").strip()
    if not text or "\x00" in text:
        raise ManualDriverError("PROFILE_MISMATCH")
    if allow_quoted and text.startswith('"') and text.endswith('"') and len(text) >= 2:
        body = text[1:-1]
        index = 0
        while index < len(body):
            if body[index] == '"':
                if index + 1 >= len(body) or body[index + 1] != '"':
                    raise ManualDriverError("PROFILE_MISMATCH")
                index += 2
            else:
                index += 1
        if not body:
            raise ManualDriverError("PROFILE_MISMATCH")
        return body.replace('""', '"')
    if '"' in text or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", text):
        raise ManualDriverError("PROFILE_MISMATCH")
    return text.upper()


def _oracle_identifier_sql(value: str, *, preserve_whitespace: bool = False) -> str:
    raw = str(value or "")
    if not preserve_whitespace:
        raw = raw.strip()
    if not raw or "\x00" in raw:
        raise ManualDriverError("PROFILE_MISMATCH")
    return '"' + raw.replace('"', '""') + '"'


__all__ = [
    "ManualDriverError",
    "ManualRelationalDriver",
    "RelationalManualDriverFactory",
    "MySqlManualDriver",
    "MySqlManualDriverFactory",
    "classify_oracle_statement",
    "classify_mysql_statement",
    "classify_relational_statement",
    "profile_schema_for_provider",
]
