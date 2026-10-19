"""Captured raw-key identities and bounded native Redis value pages."""

from dataclasses import dataclass

from .workspace_store import WorkspaceTabKind, WorkspaceTarget


STRING_PAGE_BYTES = 64 * 1024
COLLECTION_PAGE_ITEMS = 200
MAX_PAGE_ITEMS = 1_000
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_ITEM_BYTES = 4 * 1024 * 1024


class RedisValueError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RedisKeyContext:
    target: WorkspaceTarget
    key: bytes
    selection_id: str

    def __post_init__(self):
        if (type(self.target) is not WorkspaceTarget or self.target.tab_kind is not WorkspaceTabKind.REDIS
                or not self.target.connection_id or not self.target.profile_revision or not self.target.database.isdigit()
                or type(self.key) is not bytes or len(self.key) > 64 * 1024 or not self.selection_id):
            raise RedisValueError('REDIS_VALUE_TARGET_INVALID')


@dataclass(frozen=True, slots=True)
class RedisValuePage:
    context: RedisKeyContext
    kind: str
    ttl: int
    position: int | bytes
    next_position: int | bytes
    has_more: bool
    total: int | None
    data: bytes | tuple


def _check(cancel):
    if cancel is not None and cancel():
        raise RedisValueError('REDIS_VALUE_CANCELLED')


def _raw(value):
    if type(value) is not bytes:
        raise RedisValueError('REDIS_VALUE_BYTES_REQUIRED')
    if len(value) > MAX_ITEM_BYTES:
        raise RedisValueError('REDIS_VALUE_ITEM_LIMIT')
    return value


def read_value_page(connection, context, position=0, *, expected_kind='', cancel=None):
    if type(context) is not RedisKeyContext:
        raise RedisValueError('REDIS_VALUE_TARGET_INVALID')
    _check(cancel)
    key = context.key
    kind = connection.type(key)
    kind = kind.decode('ascii') if type(kind) is bytes else kind
    ttl = int(connection.ttl(key))
    if kind == 'none' or ttl == -2:
        raise RedisValueError('REDIS_VALUE_MISSING')
    if expected_kind and kind != expected_kind:
        raise RedisValueError('REDIS_VALUE_KEY_CHANGED')
    total = None
    if kind != 'stream' and (type(position) is not int or position < 0):
        raise RedisValueError('REDIS_VALUE_POSITION_INVALID')
    if kind in {'string', 'list'}:
        total = int(connection.strlen(key) if kind == 'string' else connection.llen(key))
        if position > total:
            raise RedisValueError('REDIS_VALUE_KEY_CHANGED')
        size = STRING_PAGE_BYTES if kind == 'string' else COLLECTION_PAGE_ITEMS
        stop = min(total, position + size)
        _check(cancel)
        if kind == 'string':
            data = _raw(connection.getrange(key, position, stop - 1)) if stop > position else b''
        else:
            data = tuple(_raw(value) for value in connection.lrange(key, position, stop - 1)) if stop > position else ()
        if len(data) != stop - position:
            raise RedisValueError('REDIS_VALUE_KEY_CHANGED')
        next_position, has_more = stop, stop < total
    elif kind in {'hash', 'set', 'zset'}:
        _check(cancel)
        method = getattr(connection, {'hash': 'hscan', 'set': 'sscan', 'zset': 'zscan'}[kind])
        next_position, values = method(key, cursor=position, count=COLLECTION_PAGE_ITEMS)
        next_position = int(next_position)
        if next_position < 0:
            raise RedisValueError('REDIS_VALUE_POSITION_INVALID')
        has_more = next_position != 0
        if kind == 'hash':
            data = tuple((_raw(field), _raw(value)) for field, value in values.items())
        elif kind == 'zset':
            data = tuple((_raw(member), str(score)) for member, score in values)
        else:
            data = tuple(_raw(value) for value in values)
    elif kind == 'stream':
        if position != 0 and type(position) is not bytes:
            raise RedisValueError('REDIS_VALUE_POSITION_INVALID')
        _check(cancel)
        # Include the previous id for compatibility with Redis 5, then remove
        # that boundary entry. Never infer completion from XLEN under mutation.
        values = connection.xrange(key, min=position or b'-', max=b'+', count=COLLECTION_PAGE_ITEMS + 2)
        values = [entry for entry in values if entry[0] != position]
        has_more = len(values) > COLLECTION_PAGE_ITEMS
        values = values[:COLLECTION_PAGE_ITEMS]
        data = tuple((_raw(identifier), tuple((_raw(field), _raw(value)) for field, value in fields.items())) for identifier, fields in values)
        next_position = data[-1][0] if data else position
    else:
        raise RedisValueError('REDIS_VALUE_UNSUPPORTED_TYPE')
    count, size = 0, 0
    def measure(value):
        nonlocal size
        if type(value) is bytes:
            size += len(value)
        elif type(value) is str:
            size += len(value.encode('utf-8'))
        elif type(value) is tuple:
            for child in value:
                measure(child)
    measure(data)
    count = 1 if type(data) is bytes else len(data)
    if count > MAX_PAGE_ITEMS or size > MAX_PAGE_BYTES:
        raise RedisValueError('REDIS_VALUE_PAGE_LIMIT')
    _check(cancel)
    final_kind = connection.type(key)
    if final_kind not in (kind, kind.encode('ascii')):
        raise RedisValueError('REDIS_VALUE_KEY_CHANGED')
    return RedisValuePage(context, kind, ttl, position, next_position, has_more, total, data)


def page_display_value(page):
    """Build bounded legacy presentation data on the worker, keeping raw page."""
    from tools.db_redis_ops import inspect_redis_bytes

    if page.kind == 'hash':
        return [{'field': inspect_redis_bytes(field), 'value': inspect_redis_bytes(value)} for field, value in page.data]
    if page.kind == 'zset':
        return [{'member': inspect_redis_bytes(member), 'score': score} for member, score in page.data]
    if page.kind in {'list', 'set'}:
        return [inspect_redis_bytes(value) for value in page.data]
    if page.kind == 'stream':
        import base64
        import json
        return json.dumps([{'id': identifier.decode('ascii'), 'fields': [
            {'field_base64': base64.b64encode(field).decode('ascii'), 'value_base64': base64.b64encode(value).decode('ascii')}
            for field, value in fields]} for identifier, fields in page.data], ensure_ascii=False, indent=2)
    return inspect_redis_bytes(page.data)
