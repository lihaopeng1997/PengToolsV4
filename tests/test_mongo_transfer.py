"""Mongo transfer with real BSON encoding and isolated files/fake collections."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bson import BSON, ObjectId, json_util
from bson.binary import Binary, UuidRepresentation
from bson.codec_options import CodecOptions
from bson.decimal128 import Decimal128
from pymongo.errors import DuplicateKeyError
from pymongo.results import InsertOneResult

from tools.data_center.mongo_transfer import (
    MongoTransferError, apply_import, export_documents, export_query, prepare_import,
)
from tools.data_center.nosql_workspace import capture_mongo_run
from tools.data_center.workspace_store import WorkspaceTabKind, WorkspaceTarget


class MongoTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='pengtools-mongo-transfer-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'documents.jsonl'
        self.target = WorkspaceTarget('tab', 'fixture', 'revision', 'records_db', 'records', WorkspaceTabKind.MONGO)
        self.documents = [{'_id': ObjectId('650000000000000000000001'), 'amount': Decimal128('1.20'),
                           'binary': Binary(b'\x00\xff', 2), 'uuid': Binary(b'0123456789012345', 4)}, {'_id': 2, 'name': '中文'}]

    def test_export_import_preserves_bson_and_captured_file_without_overwrite(self):
        receipt = export_documents(self.path, iter(self.documents), scope='page')
        self.assertEqual(receipt['documents'], 2)
        self.assertEqual(len(self.path.read_text(encoding='utf-8').splitlines()), 2)
        plan = prepare_import(self.path, self.target)
        self.path.write_text('{"_id":"changed after preview"}', encoding='utf-8')
        inserted = []

        def insert_one(document):
            inserted.append(deepcopy(document))
            return InsertOneResult(document['_id'], True)

        database = SimpleNamespace(name='records_db')
        class Database:
            name = database.name
            def __getitem__(_database, name):
                self.assertEqual(name, 'records')
                return SimpleNamespace(insert_one=insert_one)
        result = apply_import(Database(), plan, authorized=True)
        self.assertEqual(result['items'], ((1, 'inserted'), (2, 'inserted')))
        for original, actual in zip(self.documents, inserted):
            self.assertEqual(BSON.encode(actual), BSON.encode(original))
        before = self.path.read_bytes()
        with self.assertRaises(MongoTransferError):
            export_documents(self.path, self.documents, scope='selected')
        self.assertEqual(self.path.read_bytes(), before)

    def test_array_single_document_duplicates_and_budgets_are_preflight_only(self):
        for value in (self.documents, self.documents[0]):
            self.path.write_text(json_util.dumps(value), encoding='utf-8')
            plan = prepare_import(self.path, self.target)
            self.assertEqual(len(plan.documents), 2 if type(value) is list else 1)
        self.path.write_text('{"name":"generated"}', encoding='utf-8')
        self.assertEqual(prepare_import(self.path, self.target).generated_ids, 1)
        for text in ('[{"x":1,"x":2}]', '[1]', '', '[{}', '{"_id":1}\n{"x":1,"x":2}'):
            self.path.write_text(text, encoding='utf-8')
            with self.subTest(text=text), self.assertRaises(ValueError):
                prepare_import(self.path, self.target)
        self.path.write_text('[{},{}]', encoding='utf-8')
        with patch('tools.data_center.mongo_transfer.MAX_IMPORT_DOCUMENTS', 1), self.assertRaises(MongoTransferError):
            prepare_import(self.path, self.target)

    def test_import_partial_failure_and_cancel_report_each_item_without_retry(self):
        self.path.write_text('[{"_id":1},{"_id":2},{"_id":3}]', encoding='utf-8')
        plan = prepare_import(self.path, self.target)
        calls = []
        def insert_one(document):
            calls.append(document['_id'])
            if len(calls) == 2:
                raise DuplicateKeyError('synthetic', code=11000)
            return InsertOneResult(document['_id'], True)
        class Database:
            name = 'records_db'
            def __getitem__(_database, _name):
                return SimpleNamespace(insert_one=insert_one)
        with self.assertRaises(MongoTransferError):
            apply_import(Database(), plan)
        self.assertEqual(calls, [])
        result = apply_import(Database(), plan, authorized=True)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(result['items'], ((1, 'inserted'), (2, 'duplicate'), (3, 'skipped')))
        calls.clear()
        result = apply_import(Database(), plan, authorized=True, cancel=lambda: bool(calls))
        self.assertEqual(result['items'], ((1, 'inserted'), (2, 'skipped'), (3, 'skipped')))

    def test_export_cancel_budget_and_late_destination_leave_no_false_complete_file(self):
        def cancelled_documents():
            yield self.documents[0]
            state['cancel'] = True
            yield self.documents[1]
        state = {'cancel': False}
        with self.assertRaises(MongoTransferError):
            export_documents(self.path, cancelled_documents(), scope='query', cancel=lambda: state['cancel'])
        self.assertFalse(self.path.exists())
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.path.write_bytes(b'existing')
        with patch('tools.data_center.mongo_transfer.MAX_EXPORT_BYTES', 1), self.assertRaises(MongoTransferError):
            export_documents(self.path, self.documents, scope='page', overwrite=True)
        self.assertEqual(self.path.read_bytes(), b'existing')
        self.path.unlink()
        def race():
            yield self.documents[0]
            self.path.write_bytes(b'late destination')
        with self.assertRaises(MongoTransferError):
            export_documents(self.path, race(), scope='selected')
        self.assertEqual(self.path.read_bytes(), b'late destination')
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.path])

    def test_entire_query_streams_original_spec_without_count_skip_or_limit_and_closes_cursor(self):
        events = []
        owner = self
        class Cursor:
            def sort(self, value):
                events.append(('sort', value))
                return self
            def batch_size(self, value):
                events.append(('batch_size', value))
                return self
            def __iter__(self):
                return iter(owner.documents)
            def close(self):
                events.append(('close',))
        class Collection:
            def find(self, filter_value, *, projection):
                events.append(('find', filter_value, projection))
                return Cursor()
        class Database:
            name = 'records_db'
            codec_options = CodecOptions(uuid_representation=UuidRepresentation.UNSPECIFIED)
            def __getitem__(self, name):
                owner.assertEqual(name, 'records')
                return Collection()
        snapshot = capture_mongo_run(self.target, '{}', '{}', action='query', mode='standalone',
                                     database='records_db', collection='records', filter={'active': True},
                                     sort={'name': 1}, projection={'name': 1}, skip=40, limit=20)
        result = export_query(Database(), snapshot, self.path)
        self.assertEqual(result['documents'], 2)
        self.assertEqual(events, [('find', {'active': True}, {'name': 1}), ('sort', {'name': 1}), ('batch_size', 100), ('close',)])
        self.path.unlink()
        with patch('tools.data_center.mongo_transfer.MAX_EXPORT_BYTES', 1), self.assertRaises(MongoTransferError):
            export_query(Database(), snapshot, self.path)
        self.assertEqual(events[-1], ('close',))
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
