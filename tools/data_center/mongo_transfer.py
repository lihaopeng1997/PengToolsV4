"""User-requested Mongo Extended JSON transfer; no Qt or connection ownership."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from bson.binary import UuidRepresentation

from .mongo_document import MongoDocumentError, _dumps, _loads
from .nosql_workspace import NoSqlWorkspaceSnapshot
from .workspace_store import WorkspaceTarget, WorkspaceTabKind


MAX_IMPORT_BYTES = 64 * 1024 * 1024
MAX_IMPORT_DOCUMENTS = 10_000
MAX_EXPORT_BYTES = 512 * 1024 * 1024


class MongoTransferError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MongoImportPlan:
    target: WorkspaceTarget
    documents: tuple[str, ...]
    generated_ids: int
    preview: str


def _target(target):
    if (type(target) is not WorkspaceTarget or target.tab_kind is not WorkspaceTabKind.MONGO
            or not target.connection_id or not target.profile_revision or not target.database
            or not target.schema or '\x00' in target.schema):
        raise MongoTransferError('MONGO_TRANSFER_TARGET_INVALID')


def _check_cancel(cancel):
    if cancel is not None and cancel():
        raise MongoTransferError('MONGO_TRANSFER_CANCELLED')


def prepare_import(path, target, *, cancel=None):
    """Capture bounded input once. Applying this plan never rereads the file."""
    _target(target)
    _check_cancel(cancel)
    try:
        with open(path, 'rb') as source:
            raw = source.read(MAX_IMPORT_BYTES + 1)
        if len(raw) > MAX_IMPORT_BYTES:
            raise MongoTransferError('MONGO_IMPORT_LIMIT')
        text = raw.decode('utf-8-sig')
    except MongoTransferError:
        raise
    except Exception:
        raise MongoTransferError('MONGO_IMPORT_READ_FAILED') from None

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise MongoTransferError('MONGO_IMPORT_DUPLICATE_FIELD')
            value[key] = item
        return value

    try:
        try:
            parsed = json.loads(text, object_pairs_hook=unique)
            values = parsed if type(parsed) is list else [parsed]
        except json.JSONDecodeError:
            # Exported files contain one complete EJSON document per line.
            values = [json.loads(line, object_pairs_hook=unique) for line in text.splitlines() if line.strip()]
        if not values or len(values) > MAX_IMPORT_DOCUMENTS:
            raise MongoTransferError('MONGO_IMPORT_LIMIT')
        documents, size, generated = [], 0, 0
        for value in values:
            _check_cancel(cancel)
            document = _loads(json.dumps(value, ensure_ascii=False), UuidRepresentation.UNSPECIFIED)
            canonical = _dumps(document, UuidRepresentation.UNSPECIFIED)
            size += len(canonical.encode('utf-8'))
            if size > MAX_IMPORT_BYTES:
                raise MongoTransferError('MONGO_IMPORT_LIMIT')
            generated += '_id' not in document
            documents.append(canonical)
    except (MongoTransferError, MongoDocumentError):
        raise
    except Exception:
        raise MongoTransferError('MONGO_IMPORT_INVALID_JSON') from None
    preview = '\n\n'.join(documents[:3])[:8_000]
    return MongoImportPlan(target, tuple(documents), generated, preview)


def apply_import(database, plan, *, authorized=False, cancel=None, progress=None):
    if authorized is not True or type(plan) is not MongoImportPlan:
        raise MongoTransferError('MONGO_IMPORT_CONFIRMATION_REQUIRED')
    _target(plan.target)
    if getattr(database, 'name', None) != plan.target.database:
        raise MongoTransferError('MONGO_TRANSFER_TARGET_INVALID')
    if not plan.documents or len(plan.documents) > MAX_IMPORT_DOCUMENTS:
        raise MongoTransferError('MONGO_IMPORT_LIMIT')
    items = []
    stopped = False
    collection = database[plan.target.schema]
    for index, text in enumerate(plan.documents, 1):
        if stopped or cancel is not None and cancel():
            items.append((index, 'skipped'))
            stopped = True
            continue
        # Preserve binary subtypes as Binary, independent of the connection's
        # native UUID representation. Never use upsert or replace an existing id.
        document = _loads(text, UuidRepresentation.UNSPECIFIED)
        try:
            result = collection.insert_one(document)
            state = 'inserted' if result.acknowledged is True else 'unknown'
        except Exception as error:
            # Driver exceptions may contain document contents; expose codes only.
            state = 'duplicate' if getattr(error, 'code', None) == 11000 else 'unknown'
        items.append((index, state))
        if progress is not None:
            progress(index, len(plan.documents))
        stopped = state != 'inserted'
    return {'items': tuple(items), 'inserted': sum(state == 'inserted' for _, state in items),
            'total': len(plan.documents), 'stop_requested': bool(cancel is not None and cancel())}


def export_documents(destination, documents, *, scope, representation=UuidRepresentation.STANDARD,
                     overwrite=False, cancel=None, progress=None):
    """Stream canonical JSON Lines to a private temporary file, then publish."""
    if scope not in {'selected', 'page', 'query'}:
        raise MongoTransferError('MONGO_EXPORT_SCOPE_INVALID')
    path = Path(destination)
    if not path.parent.is_dir() or not path.name:
        raise MongoTransferError('MONGO_EXPORT_DESTINATION_INVALID')
    if not overwrite and os.path.lexists(path):
        raise MongoTransferError('MONGO_EXPORT_EXISTS')
    temp = None
    try:
        _check_cancel(cancel)
        count, size = 0, 0
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.pengtools-mongo-', suffix='.tmp', delete=False) as output:
            temp = Path(output.name)
            for document in documents:
                _check_cancel(cancel)
                canonical = _dumps(document, representation)
                line = (json.dumps(json.loads(canonical), ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
                size += len(line)
                if size > MAX_EXPORT_BYTES:
                    raise MongoTransferError('MONGO_EXPORT_LIMIT')
                output.write(line)
                count += 1
                if progress is not None:
                    progress(count, 0)
            _check_cancel(cancel)
            output.flush()
            os.fsync(output.fileno())
        _check_cancel(cancel)
        if overwrite:
            os.replace(temp, path)
        else:
            os.link(temp, path)  # Atomic and cannot overwrite a late-arriving file.
        return {'documents': count, 'bytes': size, 'scope': scope}
    except (MongoTransferError, MongoDocumentError):
        raise
    except Exception:
        raise MongoTransferError('MONGO_EXPORT_FAILED') from None
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def export_query(database, snapshot, destination, *, overwrite=False, cancel=None, progress=None):
    if type(snapshot) is not NoSqlWorkspaceSnapshot:
        raise MongoTransferError('MONGO_TRANSFER_TARGET_INVALID')
    _target(snapshot.target)
    if getattr(database, 'name', None) != snapshot.target.database:
        raise MongoTransferError('MONGO_TRANSFER_TARGET_INVALID')
    args = snapshot.worker_arguments()
    _check_cancel(cancel)
    cursor = database[args['collection']].find(args['filter'], projection=args['projection'])
    try:
        if args.get('sort'):
            cursor = cursor.sort(args['sort'])
        cursor = cursor.batch_size(100)
        options = getattr(database, 'codec_options', None)
        representation = options.uuid_representation if options is not None else UuidRepresentation.STANDARD
        return export_documents(destination, cursor, scope='query', representation=representation,
                                overwrite=overwrite, cancel=cancel, progress=progress)
    finally:
        cursor.close()
