"""Explicit single-key, binary-safe viewing export. Not a Redis backup format."""

import base64
import json
import os
from pathlib import Path
import tempfile

from .workspace_store import WorkspaceTarget, WorkspaceTabKind


CHUNK_BYTES = 64 * 1024
PAGE_ITEMS = 16
MAX_VALUE_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
MAX_COMMANDS = 100_000


class RedisExportError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _bytes(value):
    if type(value) is not bytes:
        raise RedisExportError('REDIS_EXPORT_BYTES_REQUIRED')
    if len(value) > MAX_VALUE_BYTES:
        raise RedisExportError('REDIS_EXPORT_VALUE_LIMIT')
    return {'base64': base64.b64encode(value).decode('ascii'), 'bytes': len(value)}


def _check(cancel):
    if cancel is not None and cancel():
        raise RedisExportError('REDIS_EXPORT_CANCELLED')


def _records(connection, key, kind, cancel):
    if kind == 'string':
        length = int(connection.strlen(key))
        for offset in range(0, length, CHUNK_BYTES):
            _check(cancel)
            stop = min(offset + CHUNK_BYTES, length)
            value = connection.getrange(key, offset, stop - 1)
            if type(value) is not bytes or len(value) != stop - offset:
                raise RedisExportError('REDIS_EXPORT_KEY_CHANGED')
            yield {'offset': offset, 'chunk': _bytes(value)}
        if int(connection.strlen(key)) != length:
            raise RedisExportError('REDIS_EXPORT_KEY_CHANGED')
    elif kind == 'list':
        length = int(connection.llen(key))
        for offset in range(0, length, PAGE_ITEMS):
            _check(cancel)
            stop = min(offset + PAGE_ITEMS, length)
            values = connection.lrange(key, offset, stop - 1)
            if len(values) != stop - offset:
                raise RedisExportError('REDIS_EXPORT_KEY_CHANGED')
            for index, value in enumerate(values, offset):
                yield {'index': index, 'value': _bytes(value)}
        if int(connection.llen(key)) != length:
            raise RedisExportError('REDIS_EXPORT_KEY_CHANGED')
    elif kind in {'hash', 'set', 'zset'}:
        cursor = 0
        for _ in range(MAX_COMMANDS):
            _check(cancel)
            scan = getattr(connection, {'hash': 'hscan', 'set': 'sscan', 'zset': 'zscan'}[kind])
            cursor, values = scan(key, cursor=cursor, count=PAGE_ITEMS)
            entries = values.items() if kind == 'hash' else values
            for entry in entries:
                if kind == 'hash':
                    yield {'field': _bytes(entry[0]), 'value': _bytes(entry[1])}
                elif kind == 'zset':
                    yield {'member': _bytes(entry[0]), 'score': str(entry[1])}
                else:
                    yield {'member': _bytes(entry)}
            cursor = int(cursor)
            if cursor == 0:
                break
        else:
            raise RedisExportError('REDIS_EXPORT_COMMAND_LIMIT')
    elif kind == 'stream':
        previous = None
        for _ in range(MAX_COMMANDS):
            _check(cancel)
            batch = connection.xrange(key, min=previous or b'-', max=b'+', count=PAGE_ITEMS)
            batch = [entry for entry in batch if entry[0] != previous]
            if not batch:
                break
            for identifier, fields in batch:
                yield {'id': _bytes(identifier), 'fields': [{'field': _bytes(field), 'value': _bytes(value)} for field, value in fields.items()]}
            previous = batch[-1][0]
        else:
            raise RedisExportError('REDIS_EXPORT_COMMAND_LIMIT')
    else:
        raise RedisExportError('REDIS_EXPORT_UNSUPPORTED_TYPE')


def export_key(connection, target, key, destination, *, overwrite=False, cancel=None, progress=None):
    if (type(target) is not WorkspaceTarget or target.tab_kind is not WorkspaceTabKind.REDIS
            or not target.connection_id or not target.profile_revision or not target.database.isdigit()
            or type(key) is not bytes or len(key) > 64 * 1024):
        raise RedisExportError('REDIS_EXPORT_TARGET_INVALID')
    path = Path(destination)
    if not path.parent.is_dir() or not path.name:
        raise RedisExportError('REDIS_EXPORT_DESTINATION_INVALID')
    if not overwrite and os.path.lexists(path):
        raise RedisExportError('REDIS_EXPORT_EXISTS')
    temp = None
    try:
        _check(cancel)
        kind = connection.type(key)
        kind = kind.decode('ascii') if type(kind) is bytes else kind
        ttl = int(connection.ttl(key))
        if kind == 'none' or ttl == -2:
            raise RedisExportError('REDIS_EXPORT_MISSING')
        header = {'format': 'pengtools.redis-view.v1', 'view_only': True, 'consistent_snapshot': False,
                  'connection_id': target.connection_id, 'database': target.database,
                  'key': _bytes(key), 'type': kind, 'ttl_seconds_at_start': ttl,
                  'scan_duplicates_possible': kind in {'hash', 'set', 'zset'}}
        count, size = 0, 0
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.pengtools-redis-', suffix='.tmp', dir=path.parent, delete=False) as output:
            temp = Path(output.name)
            def write(text):
                nonlocal size
                raw = text.encode('utf-8')
                size += len(raw)
                if size > MAX_OUTPUT_BYTES:
                    raise RedisExportError('REDIS_EXPORT_OUTPUT_LIMIT')
                output.write(raw)
            write(json.dumps(header, ensure_ascii=False)[:-1] + ', "records": [')
            for record in _records(connection, key, kind, cancel):
                _check(cancel)
                write((',' if count else '') + '\n' + json.dumps(record, ensure_ascii=False))
                count += 1
                if progress is not None and count % 100 == 0:
                    progress(count, 0)
            _check(cancel)
            final_type = connection.type(key)
            if final_type not in (kind, kind.encode('ascii')):
                raise RedisExportError('REDIS_EXPORT_KEY_CHANGED')
            write('\n], "records_written": ' + str(count) + ', "read_completed": true}\n')
            output.flush()
            os.fsync(output.fileno())
        _check(cancel)
        if overwrite:
            os.replace(temp, path)
        else:
            os.link(temp, path)
        return {'records': count, 'bytes': size, 'type': kind}
    except RedisExportError:
        raise
    except Exception:
        raise RedisExportError('REDIS_EXPORT_FAILED') from None
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
