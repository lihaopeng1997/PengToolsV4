"""Manual, one-document Mongo edits with frozen scope and typed Extended JSON.

These helpers are not Agent tools. A caller must load the complete document,
prepare a replacement, and obtain user approval before invoking apply_edit.
"""

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import timezone
import json
import difflib

from bson import BSON, json_util
from bson.binary import UuidRepresentation
from bson.codec_options import CodecOptions, DatetimeConversion

from tools.data_center.workspace_store import WorkspaceTarget, WorkspaceTabKind


MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_TEXT_BYTES = 64 * 1024 * 1024
MONGO_JSON_OPTIONS = json_util.CANONICAL_JSON_OPTIONS.with_options(
    tz_aware=True, tzinfo=timezone.utc, uuid_representation=UuidRepresentation.STANDARD,
    datetime_conversion=DatetimeConversion.DATETIME_AUTO,
)
_BSON_OPTIONS = CodecOptions(uuid_representation=UuidRepresentation.STANDARD)


class MongoDocumentError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MongoDocumentTarget:
    target: WorkspaceTarget
    collection: str
    identifier_json: str
    _identifier_value: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class LoadedMongoDocument:
    context: MongoDocumentTarget
    document_json: str
    uuid_representation: int = UuidRepresentation.STANDARD


@dataclass(frozen=True, slots=True)
class MongoDocumentPlan:
    loaded: LoadedMongoDocument
    replacement_json: str
    changed: bool
    preview: str = ""


def _bson(value, uuid_representation=UuidRepresentation.STANDARD):
    try:
        encoded = BSON.encode(value, codec_options=_BSON_OPTIONS.with_options(uuid_representation=uuid_representation))
    except Exception:
        raise MongoDocumentError("MONGO_EDIT_BSON_UNSUPPORTED") from None
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise MongoDocumentError("MONGO_EDIT_TOO_LARGE")
    return encoded


def _loads(text, uuid_representation=UuidRepresentation.STANDARD):
    try:
        size = len(text.encode("utf-8")) if type(text) is str else MAX_DOCUMENT_TEXT_BYTES + 1
    except UnicodeError:
        raise MongoDocumentError("MONGO_EDIT_INVALID_JSON") from None
    if size > MAX_DOCUMENT_TEXT_BYTES:
        raise MongoDocumentError("MONGO_EDIT_TOO_LARGE")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MongoDocumentError("MONGO_EDIT_DUPLICATE_FIELD")
            result[key] = value
        return result

    try:
        json.loads(text, object_pairs_hook=unique)
        value = json_util.loads(text, json_options=MONGO_JSON_OPTIONS.with_options(uuid_representation=uuid_representation))
    except MongoDocumentError:
        raise
    except Exception:
        raise MongoDocumentError("MONGO_EDIT_INVALID_JSON") from None
    if type(value) is not dict:
        raise MongoDocumentError("MONGO_EDIT_DOCUMENT_REQUIRED")
    _bson(value, uuid_representation)
    return value


def _dumps(document, uuid_representation=UuidRepresentation.STANDARD):
    original = _bson(document, uuid_representation)
    try:
        text = json_util.dumps(document, json_options=MONGO_JSON_OPTIONS.with_options(uuid_representation=uuid_representation), ensure_ascii=False, indent=2)
    except Exception:
        raise MongoDocumentError("MONGO_EDIT_BSON_UNSUPPORTED") from None
    # EJSON reserved literal objects must never silently change a BSON type.
    try:
        roundtrip = _bson(_loads(text, uuid_representation), uuid_representation)
    except MongoDocumentError as error:
        if error.code == "MONGO_EDIT_TOO_LARGE":
            raise
        raise MongoDocumentError("MONGO_EDIT_EJSON_AMBIGUOUS") from None
    if roundtrip != original:
        raise MongoDocumentError("MONGO_EDIT_EJSON_AMBIGUOUS")
    return text


def make_document_target(target, collection, identifier):
    if (type(target) is not WorkspaceTarget or target.tab_kind is not WorkspaceTabKind.MONGO
            or not target.connection_id or not target.profile_revision or not target.database
            or type(collection) is not str or not collection or "\x00" in collection
            or target.schema and target.schema != collection):
        raise MongoDocumentError("MONGO_EDIT_TARGET_INVALID")
    return MongoDocumentTarget(target, collection, _dumps({"_id": identifier}), deepcopy(identifier))


def _identifier(context):
    if type(context) is not MongoDocumentTarget:
        raise MongoDocumentError("MONGO_EDIT_TARGET_INVALID")
    return deepcopy(context._identifier_value)


def load_document(database, context):
    if getattr(database, "name", None) != context.target.database:
        raise MongoDocumentError("MONGO_EDIT_TARGET_INVALID")
    identifier = _identifier(context)
    options = getattr(database, "codec_options", _BSON_OPTIONS)
    representation = options.uuid_representation
    document = database[context.collection].find_one({"_id": {"$eq": identifier}})
    if document is None:
        raise MongoDocumentError("MONGO_EDIT_NOT_FOUND")
    if not isinstance(document, dict) or "_id" not in document:
        raise MongoDocumentError("MONGO_EDIT_ID_REQUIRED")
    if _bson({"_id": document["_id"]}, representation) != _bson({"_id": identifier}, representation):
        raise MongoDocumentError("MONGO_EDIT_TARGET_INVALID")
    context = replace(context, identifier_json=_dumps({"_id": document["_id"]}, representation))
    return LoadedMongoDocument(context, _dumps(document, representation), representation)


def prepare_edit(loaded, edited_text):
    if type(loaded) is not LoadedMongoDocument:
        raise MongoDocumentError("MONGO_EDIT_TARGET_INVALID")
    representation = loaded.uuid_representation
    original = _loads(loaded.document_json, representation)
    replacement = _loads(edited_text, representation)
    if "_id" not in replacement:
        raise MongoDocumentError("MONGO_EDIT_ID_REQUIRED")
    if _bson({"_id": replacement["_id"]}, representation) != _bson({"_id": _identifier(loaded.context)}, representation):
        raise MongoDocumentError("MONGO_EDIT_ID_IMMUTABLE")
    body = _dumps(replacement, representation)
    changed = _bson(original, representation) != _bson(replacement, representation)
    preview = "".join(difflib.unified_diff(loaded.document_json.splitlines(True), body.splitlines(True),
                                        fromfile="loaded document", tofile="replacement document")) if changed else ""
    return MongoDocumentPlan(loaded, body, changed, preview)


def apply_edit(database, plan, *, authorized=False):
    if authorized is not True or type(plan) is not MongoDocumentPlan:
        raise MongoDocumentError("MONGO_EDIT_CONFIRMATION_REQUIRED")
    verified = prepare_edit(plan.loaded, plan.replacement_json)
    if getattr(database, "name", None) != plan.loaded.context.target.database:
        raise MongoDocumentError("MONGO_EDIT_TARGET_INVALID")
    if getattr(database, "codec_options", _BSON_OPTIONS).uuid_representation != plan.loaded.uuid_representation:
        raise MongoDocumentError("MONGO_EDIT_TARGET_INVALID")
    if not verified.changed:
        return {"matched": 0, "modified": 0, "unchanged": True}
    identifier = _identifier(plan.loaded.context)
    replacement = _loads(verified.replacement_json, plan.loaded.uuid_representation)
    result = database[plan.loaded.context.collection].replace_one(
        {"_id": {"$eq": identifier}}, replacement, upsert=False)
    if result.acknowledged is not True:
        raise MongoDocumentError("MONGO_EDIT_OUTCOME_UNKNOWN")
    matched, modified = result.matched_count, result.modified_count
    if type(matched) is not int or type(modified) is not int or matched not in {0, 1} or modified not in {0, 1}:
        raise MongoDocumentError("MONGO_EDIT_OUTCOME_UNKNOWN")
    if matched == 0:
        raise MongoDocumentError("MONGO_EDIT_NOT_FOUND")
    return {"matched": matched, "modified": modified, "unchanged": False}
