"""Offline SDK surface check: real PyMongo cursor, no server or network I/O."""

import unittest
from unittest.mock import patch

from bson import ObjectId
from pymongo import MongoClient
from pymongo.cursor import Cursor

from tools.db_mongo_ops import find_docs
from tools.data_center.mongo_transfer import export_query
from tools.data_center.nosql_workspace import capture_mongo_run
from tools.data_center.workspace_store import WorkspaceTarget, WorkspaceTabKind


class MongoQueryDriverApiTests(unittest.TestCase):
    def test_full_export_configures_and_closes_real_offline_cursor(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        client = MongoClient('mongodb://127.0.0.1:1', connect=False)
        try:
            database = client['synthetic']
            collection = database['records']
            original_find = collection.find
            cursors = []
            def captured_find(*args, **kwargs):
                cursor = original_find(*args, **kwargs)
                cursors.append(cursor)
                return cursor
            target = WorkspaceTarget('tab', 'fixture', 'revision', 'synthetic', 'records', WorkspaceTabKind.MONGO)
            snapshot = capture_mongo_run(target, '{}', '{}', action='query', mode='standalone',
                                         database='synthetic', collection='records', filter={'state': 'fixture'},
                                         sort={'created_at': -1}, projection={'_id': 1}, skip=100, limit=20)
            class Database:
                name = database.name
                codec_options = database.codec_options
                def __getitem__(self, name):
                    return collection
            with TemporaryDirectory(prefix='pengtools-mongo-sdk-') as temp, \
                    patch.object(collection, 'find', new=captured_find), \
                    patch.object(Cursor, '__iter__', new=lambda _cursor: iter([{'_id': 1}])):
                result = export_query(Database(), snapshot, Path(temp) / 'query.jsonl')
            self.assertEqual(result['documents'], 1)
            self.assertEqual(cursors[0]._projection, {'_id': 1})
            self.assertEqual(cursors[0]._ordering, {'created_at': -1})
            self.assertEqual((cursors[0]._skip, cursors[0]._limit, cursors[0]._batch_size), (0, 0, 100))
            self.assertTrue(cursors[0]._killed)
            self.assertFalse(client._topology._opened)
        finally:
            client.close()

    def test_projection_sort_and_lookahead_use_actual_pymongo_cursor_api(self):
        client = MongoClient("mongodb://127.0.0.1:1", connect=False)
        try:
            collection = client["synthetic"]["records"]
            original_find = collection.find
            cursors = []
            docs = [{"_id": ObjectId("650000000000000000000001")}]

            def captured_find(*args, **kwargs):
                cursor = original_find(*args, **kwargs)
                cursors.append(cursor)
                return cursor

            class Database:
                name = "synthetic"

                def __getitem__(self, name):
                    return collection

            with patch.object(collection, "find", new=captured_find), \
                    patch.object(Cursor, "__iter__", new=lambda _cursor: iter(docs)):
                output = find_docs(Database(), "records", {"state": "fixture"},
                                   sort={"created_at": -1}, projection={"_id": 1}, skip=4, limit=2)
            self.assertEqual(output, docs)
            self.assertEqual(cursors[0]._projection, {"_id": 1})
            self.assertEqual(cursors[0]._ordering, {"created_at": -1})
            self.assertEqual((cursors[0]._skip, cursors[0]._limit), (4, 3))
            self.assertFalse(client._topology._opened)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
