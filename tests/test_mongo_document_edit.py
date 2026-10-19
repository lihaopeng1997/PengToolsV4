"""Typed manual document replacements on synthetic collections, no server."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from bson import BSON, ObjectId
from bson.binary import Binary, UuidRepresentation
from bson.codec_options import CodecOptions
from bson.decimal128 import Decimal128
from bson.int64 import Int64

from tools.data_center.mongo_document import (
    MongoDocumentError, make_document_target, load_document, prepare_edit, apply_edit,
)
from tools.data_center.workspace_store import WorkspaceTarget, WorkspaceTabKind


class MongoDocumentTests(unittest.TestCase):
    def _database(self, document, representation=UuidRepresentation.STANDARD):
        document = deepcopy(document)
        calls = []

        class Collection:
            def find_one(self, selector):
                calls.append(("find_one", deepcopy(selector)))
                return deepcopy(document)

            def replace_one(self, selector, replacement, *, upsert):
                calls.append(("replace_one", deepcopy(selector), deepcopy(replacement), upsert))
                return SimpleNamespace(acknowledged=True, matched_count=1, modified_count=1)

        collection = Collection()

        class Database:
            name = "db"
            codec_options = CodecOptions(uuid_representation=representation)

            def __getitem__(self, name):
                if name != "records":
                    raise AssertionError("wrong collection")
                return collection

        target = WorkspaceTarget("tab", "fixture", "revision", "db", "records", WorkspaceTabKind.MONGO)
        context = make_document_target(target, "records", document["_id"])
        return Database(), collection, context, calls

    def test_full_document_replacement_preserves_wire_types_and_literal_id_scope(self):
        for representation in (UuidRepresentation.STANDARD, UuidRepresentation.JAVA_LEGACY, UuidRepresentation.UNSPECIFIED):
            with self.subTest(representation=representation):
                value = uuid.UUID("65000000-0000-0000-0000-000000000001")
                value = value if representation != UuidRepresentation.UNSPECIFIED else Binary.from_uuid(value)
                identifier = ObjectId("650000000000000000000001") if representation == UuidRepresentation.STANDARD else value
                document = {"_id": identifier, "name": "old", "hidden": {
                    "number": Int64(7), "amount": Decimal128("1.20"), "binary": Binary(b"\x00\xff", subtype=2),
                    "date": datetime(2026, 10, 15, tzinfo=timezone.utc), "uuid": value}}
                database, collection, context, calls = self._database(document, representation)
                loaded = load_document(database, context)
                edited = loaded.document_json.replace('"name": "old"', '"name": "new"')
                plan = prepare_edit(loaded, edited)
                self.assertTrue(plan.changed)
                self.assertIn('-  "name": "old"', plan.preview)
                self.assertIn('+  "name": "new"', plan.preview)
                self.assertEqual(apply_edit(database, plan, authorized=True), {"matched": 1, "modified": 1, "unchanged": False})
                self.assertEqual(calls[-1][1], {"_id": {"$eq": document["_id"]}})
                self.assertFalse(calls[-1][3])
                expected = {**document, "name": "new"}
                self.assertEqual(BSON.encode(calls[-1][2], codec_options=database.codec_options), BSON.encode(expected, codec_options=database.codec_options))

    def test_id_changes_duplicate_fields_unauthorized_and_noop_never_write(self):
        database, _collection, context, calls = self._database({"_id": Int64(5), "name": "old"})
        loaded = load_document(database, context)
        for text in ('{"_id":5,"name":"new"}', '{"name":"new"}', '{"_id":{"$numberLong":"5"},"name":1,"name":2}', '[]'):
            with self.subTest(text=text):
                with self.assertRaises(MongoDocumentError):
                    prepare_edit(loaded, text)
        changed = prepare_edit(loaded, loaded.document_json.replace('"old"', '"new"'))
        with self.assertRaises(MongoDocumentError):
            apply_edit(database, changed)
        unchanged = prepare_edit(loaded, loaded.document_json)
        self.assertFalse(unchanged.changed)
        self.assertEqual(apply_edit(database, unchanged, authorized=True), {"matched": 0, "modified": 0, "unchanged": True})
        self.assertFalse(any(call[0] == "replace_one" for call in calls))

    def test_selector_is_copied_and_operator_looking_id_remains_literal(self):
        identifier = {"$ne": "fixture"}
        database, _collection, context, calls = self._database({"_id": identifier, "name": "old"})
        identifier["$ne"] = "changed outside capture"
        loaded = load_document(database, context)
        self.assertEqual(calls[0][1], {"_id": {"$eq": {"$ne": "fixture"}}})
        plan = prepare_edit(loaded, loaded.document_json.replace('"old"', '"new"'))
        apply_edit(database, plan, authorized=True)
        self.assertEqual(calls[-1][1], calls[0][1])

    def test_missing_scope_changed_codec_unacknowledged_and_size_limits(self):
        database, collection, context, calls = self._database({"_id": 1, "name": "old"})
        loaded = load_document(database, context)
        plan = prepare_edit(loaded, loaded.document_json.replace('"old"', '"new"'))
        database.name = "other"
        with self.assertRaises(MongoDocumentError):
            apply_edit(database, plan, authorized=True)
        database.name = "db"
        database.codec_options = CodecOptions(uuid_representation=UuidRepresentation.UNSPECIFIED)
        with self.assertRaises(MongoDocumentError):
            apply_edit(database, plan, authorized=True)
        database.codec_options = CodecOptions(uuid_representation=UuidRepresentation.STANDARD)
        with patch.object(collection, "replace_one", return_value=SimpleNamespace(acknowledged=False)):
            with self.assertRaisesRegex(MongoDocumentError, "MONGO_EDIT_OUTCOME_UNKNOWN"):
                apply_edit(database, plan, authorized=True)
        with patch.object(collection, "replace_one", return_value=SimpleNamespace(acknowledged=True, matched_count=0, modified_count=0)):
            with self.assertRaisesRegex(MongoDocumentError, "MONGO_EDIT_NOT_FOUND"):
                apply_edit(database, plan, authorized=True)
        with patch("tools.data_center.mongo_document.MAX_DOCUMENT_TEXT_BYTES", 8):
            with self.assertRaisesRegex(MongoDocumentError, "MONGO_EDIT_TOO_LARGE"):
                prepare_edit(loaded, loaded.document_json)

    def test_reserved_literal_ejson_is_rejected_instead_of_silently_changing_type(self):
        database, _collection, context, _calls = self._database({"_id": 1, "literal": {"$numberLong": "5"}})
        with self.assertRaisesRegex(MongoDocumentError, "MONGO_EDIT_EJSON_AMBIGUOUS"):
            load_document(database, context)


if __name__ == "__main__":
    unittest.main()
