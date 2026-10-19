"""Actual Oracle SDK LOB objects with injected in-memory owner-thread I/O."""

import threading
import unittest
import json
import tempfile
from pathlib import Path

import oracledb
from tools.data_center.lob_values import detach_oracle_row
from tools.data_center.manual_driver import ManualRelationalDriver
from tools.data_center.manual_session import ManualSession, ManualSessionError, ManualSessionTarget, StatementKind, StatementClassification, TransactionEffect
from tools.data_center.result_export import ExportTarget


class _LobImpl:
    def __init__(self, value, dbtype, calls, *, entered=None, release=None, reported_size=None):
        self.value, self.dbtype, self.calls = value, dbtype, calls
        self.entered, self.release, self.reported_size = entered, release, reported_size

    def get_size(self):
        self.calls.append(('size', threading.get_ident()))
        return self.reported_size if self.reported_size is not None else len(self.value) if isinstance(self.value, bytes) else len(self.value.encode('utf-16-le')) // 2

    def read(self, offset, amount):
        self.calls.append(('read', threading.get_ident(), offset, amount))
        if self.entered is not None:
            self.entered.set()
            if not self.release.wait(3):
                raise AssertionError('test did not release blocked LOB read')
        if isinstance(self.value, bytes):
            return self.value[offset - 1:offset - 1 + amount]
        return self.value.encode('utf-16-le')[(offset - 1) * 2:(offset - 1 + amount) * 2].decode('utf-16-le')

    def free_lob(self):
        pass


def lob(value, calls, *, dbtype=None, **kwargs):
    return oracledb.LOB._from_impl(_LobImpl(value, dbtype or (oracledb.DB_TYPE_BLOB if isinstance(value, bytes) else oracledb.DB_TYPE_CLOB), calls, **kwargs))


class _Cursor:
    description = (('text',), ('binary',))
    rowcount = -1

    def __init__(self, rows, calls):
        self.rows, self.calls = list(rows), calls

    def execute(self, *_args):
        self.calls.append(('execute', threading.get_ident()))

    def fetchmany(self, size):
        self.calls.append(('fetch', threading.get_ident(), size))
        output, self.rows = self.rows[:size], self.rows[size:]
        return output

    def close(self):
        self.calls.append(('cursor-close', threading.get_ident()))
        self.rows.clear()


class _Connection:
    autocommit = False

    def __init__(self, cursor, calls):
        self._cursor, self.calls = cursor, calls

    def cursor(self):
        return self._cursor

    def rollback(self):
        self.calls.append(('rollback', threading.get_ident()))

    def close(self):
        self.calls.append(('connection-close', threading.get_ident()))


class LobValueTests(unittest.TestCase):
    def test_sdk_clob_supplementary_boundary_and_blob_are_detached_exactly(self):
        calls = []
        text = 'x' * 32767 + '😀尾' * 20000
        binary = b'\x00\xff' * 40000
        row = detach_oracle_row((lob(text, calls), lob(binary, calls)), max_bytes=2_000_000, cancelled=lambda: False)
        self.assertEqual(row, (text, binary))
        self.assertTrue(all(call[1] == threading.get_ident() for call in calls))
        self.assertTrue(all(call[3] <= 32768 for call in calls if call[0] == 'read'))
        self.assertTrue(any(call[0] == 'read' and call[3] == 32767 for call in calls))

    def test_oversize_blob_is_rejected_before_read_and_unknown_readable_objects_are_untouched(self):
        calls = []
        value = lob(b'tiny', calls, reported_size=10**12)
        with self.assertRaises(ManualSessionError) as caught:
            detach_oracle_row((value,), max_bytes=1000, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, 'RESULT_VALUE_LIMIT')
        self.assertFalse(any(call[0] == 'read' for call in calls))
        class Other:
            def read(self):
                raise AssertionError('arbitrary readable value must not be invoked')
        other = Other()
        self.assertIs(detach_oracle_row((other,), max_bytes=1000, cancelled=lambda: False)[0], other)

    def test_actual_manual_owner_returns_detached_pages_and_closes_on_same_thread(self):
        calls = []
        caller = threading.get_ident()
        expected = [('text-' + str(i), bytes([i])) for i in range(53)]
        def factory(_target):
            rows = [(lob(text, calls), lob(binary, calls)) for text, binary in expected]
            return ManualRelationalDriver(_Connection(_Cursor(rows, calls), calls), 'oracle')
        session = ManualSession(ManualSessionTarget('lob-tab', 'synthetic', 'v1'), factory, lambda _sql: StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE), page_size=50)
        try:
            first = session.query('SELECT text, binary FROM synthetic').result(timeout=3)
            second = session.fetch_next_page().result(timeout=3)
            self.assertEqual(first.rows + second.rows, tuple(expected))
            self.assertTrue(first.has_more)
            self.assertFalse(second.has_more)
            self.assertTrue(all(type(value) in (str, bytes) for row in first.rows + second.rows for value in row))
        finally:
            session.close(disposition='rollback').result(timeout=3)
        owner_ids = {call[1] for call in calls}
        self.assertEqual(len(owner_ids), 1)
        self.assertNotIn(caller, owner_ids)
        self.assertIn('connection-close', [call[0] for call in calls])

    def test_cancelled_blocked_read_stays_pending_until_owner_finishes(self):
        calls, entered, release = [], threading.Event(), threading.Event()
        def factory(_target):
            value = lob('loaded-text', calls, entered=entered, release=release)
            return ManualRelationalDriver(_Connection(_Cursor([(value, b'')], calls), calls), 'oceanbase_oracle')
        session = ManualSession(ManualSessionTarget('lob-cancel', 'synthetic', 'v1'), factory, lambda _sql: StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE))
        future = session.query('SELECT text, binary FROM synthetic')
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(session.cancel_current())
            self.assertFalse(future.done())
            self.assertTrue(session.poll_cleanup().busy)
            release.set()
            with self.assertRaises(ManualSessionError) as caught:
                future.result(timeout=3)
            self.assertEqual(caught.exception.code, 'CANCELLED')
            self.assertEqual(sum(call[0] == 'read' for call in calls), 1)
        finally:
            release.set()
            session.close(disposition='rollback').result(timeout=3)

    def test_full_export_consumes_same_cursor_with_detached_lobs(self):
        calls = []
        def factory(_target):
            rows = [(lob(f'payload-{i}', calls), lob(bytes([i]), calls)) for i in range(73)]
            return ManualRelationalDriver(_Connection(_Cursor(rows, calls), calls), 'oracle')
        session = ManualSession(ManualSessionTarget('lob-export', 'synthetic', 'v1'), factory,
                                lambda _sql: StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE), page_size=50)
        try:
            first = session.query('SELECT text, binary FROM synthetic').result(timeout=3)
            with tempfile.TemporaryDirectory(prefix='lob-export-') as temporary:
                destination = Path(temporary) / 'result.json'
                receipt = session.export_full_query(destination, 'json', ExportTarget(first.query_id, first.tab_id, first.generation)).result(timeout=3)
                document = json.loads(destination.read_text(encoding='utf-8'))
                self.assertEqual(receipt.rows, 73)
                self.assertEqual([row[0]['v'] for row in document['rows']], [f'payload-{i}' for i in range(73)])
                self.assertTrue(all(row[0]['t'] == 'str' for row in document['rows']))
            self.assertEqual(sum(call[0] == 'execute' for call in calls), 1)
        finally:
            session.close(disposition='rollback').result(timeout=3)

    def test_full_export_cancellation_waits_for_blocked_lob_read_and_is_not_fetch_error(self):
        calls, entered, release = [], threading.Event(), threading.Event()
        def factory(_target):
            rows = [(lob(f'payload-{i}', calls, **({'entered': entered, 'release': release} if i == 51 else {})), b'') for i in range(53)]
            return ManualRelationalDriver(_Connection(_Cursor(rows, calls), calls), 'oracle')
        session = ManualSession(ManualSessionTarget('lob-export-cancel', 'synthetic', 'v1'), factory,
                                lambda _sql: StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE), page_size=50)
        try:
            first = session.query('SELECT text, binary FROM synthetic').result(timeout=3)
            with tempfile.TemporaryDirectory(prefix='lob-export-cancel-') as temporary:
                destination = Path(temporary) / 'result.json'
                future = session.export_full_query(destination, 'json', ExportTarget(first.query_id, first.tab_id, first.generation))
                self.assertTrue(entered.wait(2))
                self.assertTrue(session.cancel_current())
                self.assertFalse(future.done())
                self.assertTrue(session.poll_cleanup().busy)
                reads = sum(call[0] == 'read' for call in calls)
                release.set()
                with self.assertRaises(ManualSessionError) as caught:
                    future.result(timeout=3)
                self.assertEqual(caught.exception.code, 'CANCELLED')
                self.assertEqual(sum(call[0] == 'read' for call in calls), reads)
                self.assertFalse(destination.exists())
        finally:
            release.set()
            session.close(disposition='rollback').result(timeout=3)

    def test_server_read_failure_is_not_retried(self):
        calls = []
        value = lob('payload', calls)
        def fail(offset, amount):
            calls.append(('read', threading.get_ident(), offset, amount))
            raise RuntimeError('synthetic server failure')
        value._impl.read = fail
        with self.assertRaises(RuntimeError):
            detach_oracle_row((value,), max_bytes=1000, cancelled=lambda: False)
        self.assertEqual(sum(call[0] == 'read' for call in calls), 1)

    def test_manual_result_reports_value_limit_without_reading_or_exporting_partial_data(self):
        calls = []
        def factory(_target):
            value = lob(b'tiny', calls, reported_size=10**12)
            return ManualRelationalDriver(_Connection(_Cursor([(value, b'')], calls), calls), 'oracle')
        session = ManualSession(ManualSessionTarget('lob-limit', 'synthetic', 'v1'), factory,
                                lambda _sql: StatementClassification(StatementKind.QUERY, TransactionEffect.NO_CHANGE))
        try:
            with self.assertRaises(ManualSessionError) as caught:
                session.query('SELECT text, binary FROM synthetic').result(timeout=3)
            self.assertEqual(caught.exception.code, 'RESULT_VALUE_LIMIT')
            self.assertFalse(any(call[0] == 'read' for call in calls))
            self.assertFalse(session.result_source_status.can_export_full)
        finally:
            session.close(disposition='rollback').result(timeout=3)


if __name__ == '__main__':
    unittest.main()
