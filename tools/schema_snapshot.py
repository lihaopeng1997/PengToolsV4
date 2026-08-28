# -*- coding: utf-8 -*-
"""连接结构快照：只存元数据，不存行数据/凭据/执行结果。"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

from config import SCHEMA_SNAPSHOT_DIR, ensure_config_dir
from tools.sql_guard import redact_error

REDIS_KEY_CAP = 5000


def _b(value) -> str:
    """Redis 字节安全解码：二进制 key/field 用 errors='replace' 转文本，不抛 UnicodeDecodeError。"""
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def connection_fingerprint(item: dict) -> str:
    data = item if isinstance(item, dict) else {}
    parts = [
        str(data.get('dialect') or '').strip().lower(),
        str(data.get('host') or '').strip().lower(),
        str(data.get('port') or '').strip(),
        str(data.get('database') or '').strip().lower(),
        str(data.get('username') or '').strip().lower(),
    ]
    return '|'.join(parts)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def snapshot_path(conn_id: str) -> str:
    ensure_config_dir()
    os.makedirs(SCHEMA_SNAPSHOT_DIR, exist_ok=True)
    safe = ''.join(ch for ch in str(conn_id or '') if ch.isalnum() or ch in '-_') or 'unknown'
    return os.path.join(SCHEMA_SNAPSHOT_DIR, f'{safe}.json')


def _atomic_write(path: str, payload: dict) -> None:
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.snap-', suffix='.tmp', dir=directory, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def empty_snapshot(item: dict, *, status='empty') -> dict:
    data = item if isinstance(item, dict) else {}
    return {
        'connection_id': str(data.get('id') or ''),
        'alias': str(data.get('name') or ''),
        'dialect': str(data.get('dialect') or 'oracle').lower(),
        'fingerprint': connection_fingerprint(data),
        'snapshot_id': uuid.uuid4().hex,
        'version': 2,
        'scanned_at': '',
        'status': status,
        'truncated': False,
        'index_metadata_status': 'ok',
        'warning': '',
        'objects': [],
    }


def load_snapshot(conn_id: str) -> dict | None:
    path = snapshot_path(conn_id)
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault('objects', [])
    data.setdefault('status', 'ok')
    data.setdefault('truncated', False)
    data.setdefault('warning', '')
    data.setdefault('version', 1)
    try:
        version = int(data.get('version') or 1)
    except (TypeError, ValueError):
        version = 1
        data['version'] = 1
    if version < 2:
        data.setdefault('index_metadata_status', 'incomplete')
    else:
        data.setdefault('index_metadata_status', 'ok')
    data['objects'] = [_clean_object(item) for item in data.get('objects') or [] if isinstance(item, dict)]
    return data


def save_snapshot(payload: dict) -> dict:
    data = dict(payload or {})
    conn_id = str(data.get('connection_id') or '')
    if not conn_id:
        raise ValueError('snapshot 缺少 connection_id')
    data['objects'] = [_clean_object(item) for item in (data.get('objects') or []) if isinstance(item, dict)]
    _atomic_write(snapshot_path(conn_id), data)
    return data


def delete_snapshot(conn_id: str) -> None:
    path = snapshot_path(conn_id)
    try:
        os.unlink(path)
    except OSError:
        pass


def snapshot_status(item: dict, snap: dict | None) -> dict:
    data = item if isinstance(item, dict) else {}
    current = connection_fingerprint(data)
    if not snap:
        return {'status': 'missing', 'stale': False, 'label': '未扫描'}
    stale = str(snap.get('fingerprint') or '') != current
    status = 'stale' if stale else str(snap.get('status') or 'ok')
    if stale:
        label = '快照已过期，请重新扫描'
    elif status == 'failed':
        label = '扫描失败，保留上次快照'
    elif status == 'ok':
        label = f'已扫描 {snap.get("scanned_at") or ""}'
    else:
        label = str(snap.get('warning') or status)
    return {'status': status, 'stale': stale, 'label': label.strip()}


def _clean_index(item) -> dict | None:
    if isinstance(item, str):
        name = item.strip()
        return {'name': name, 'unique': False, 'index_type': '', 'columns': []} if name else None
    if not isinstance(item, dict):
        return None
    columns = []
    for col in item.get('columns') or []:
        if isinstance(col, str) and col.strip():
            columns.append({'name': col.strip(), 'position': len(columns) + 1})
        elif isinstance(col, dict) and str(col.get('name') or '').strip():
            try:
                position = int(col.get('position') or len(columns) + 1)
            except (TypeError, ValueError):
                position = len(columns) + 1
            columns.append({'name': str(col.get('name')).strip(), 'position': position})
    name = str(item.get('name') or '').strip()
    if not name and not columns:
        return None
    return {
        'name': name,
        'unique': bool(item.get('unique')),
        'index_type': str(item.get('index_type') or ''),
        'columns': columns,
    }


def dameng_index_scan_ready() -> bool:
    """达梦索引视图须在目标版本验证后才能接入，未验证前不得编造 SQL。"""
    return False


def _clean_object(item: dict) -> dict:
    columns = []
    for col in item.get('columns') or []:
        if not isinstance(col, dict):
            continue
        columns.append({
            'name': str(col.get('name') or ''),
            'data_type': str(col.get('data_type') or ''),
            'nullable': bool(col.get('nullable', True)),
            'position': int(col.get('position') or 0),
            'comment': str(col.get('comment') or ''),
            'primary_key': bool(col.get('primary_key')),
            'indexed': bool(col.get('indexed') or col.get('primary_key')),
        })
    has_indexes_key = 'indexes' in item
    indexes = []
    for raw in item.get('indexes') or []:
        cleaned = _clean_index(raw)
        if cleaned:
            indexes.append(cleaned)
    status = str(item.get('index_metadata_status') or '').strip().lower()
    if status not in ('ok', 'unavailable', 'incomplete'):
        if has_indexes_key:
            status = 'ok'
        else:
            status = 'incomplete'
    indexed_names = {
        str(col.get('name') or '').upper()
        for idx in indexes
        for col in idx.get('columns') or []
    }
    if indexed_names:
        for col in columns:
            if str(col.get('name') or '').upper() in indexed_names:
                col['indexed'] = True
    return {
        'owner': str(item.get('owner') or ''),
        'name': str(item.get('name') or ''),
        'object_type': str(item.get('object_type') or 'TABLE'),
        'comment': str(item.get('comment') or ''),
        'inferred': bool(item.get('inferred')),
        'columns': columns,
        'indexes': indexes,
        'index_metadata_status': status,
    }


def _type_name(value) -> str:
    if value is None:
        return 'null'
    return type(value).__name__


def scan_schema(conn, item: dict, cancel=None) -> dict:
    dialect = str((item or {}).get('dialect') or 'oracle').lower()
    payload = empty_snapshot(item, status='ok')
    payload['scanned_at'] = _now()
    if callable(cancel) and cancel():
        payload['status'] = 'failed'
        payload['warning'] = '扫描已取消'
        return payload
    try:
        if dialect == 'redis':
            objects, truncated = _scan_redis(conn)
        elif dialect == 'mongodb':
            objects, truncated = _scan_mongo(conn)
        elif dialect in ('mysql', 'oceanbase'):
            objects, truncated = _scan_information_schema(conn, item)
        else:
            objects, truncated = _scan_oracle_like(conn, dialect)
        payload['objects'] = objects
        payload['truncated'] = truncated
        payload['status'] = 'ok'
        payload['version'] = 2
        if dialect in ('redis', 'mongodb') or (dialect == 'dameng' and not dameng_index_scan_ready()):
            for obj in objects:
                if isinstance(obj, dict):
                    obj['indexes'] = []
                    obj['index_metadata_status'] = 'unavailable'
            payload['index_metadata_status'] = 'unavailable'
            if dialect == 'dameng':
                extra = '达梦索引元数据待目标环境验证，当前不可用'
                payload['warning'] = ((payload.get('warning') or '') + ' ' + extra).strip()
        else:
            statuses = {
                str(obj.get('index_metadata_status') or 'ok')
                for obj in objects
                if isinstance(obj, dict)
            }
            payload['index_metadata_status'] = 'unavailable' if 'unavailable' in statuses else 'ok'
            if payload['index_metadata_status'] == 'unavailable':
                extra = '索引元数据不可用'
                for obj in objects:
                    if isinstance(obj, dict) and obj.get('index_warning'):
                        extra = str(obj.get('index_warning'))
                        break
                payload['warning'] = ((payload.get('warning') or '') + ' ' + extra).strip()
        if truncated:
            payload['warning'] = ((payload.get('warning') or '') + ' 对象数量已截断，仅保留可见范围内的前若干项').strip()
    except Exception as exc:
        old = load_snapshot(str((item or {}).get('id') or ''))
        if old and old.get('objects'):
            old['status'] = 'failed'
            old['warning'] = redact_error(str(exc))
            old['fingerprint'] = payload['fingerprint']
            return old
        payload['status'] = 'failed'
        payload['warning'] = redact_error(str(exc))
    return payload


def _scan_oracle_like(conn, dialect: str) -> tuple[list, bool]:
    cur = conn.cursor()
    objects = {}
    try:
        if dialect == 'dameng':
            cur.execute(
                "SELECT USER AS OWNER, TABLE_NAME, 'TABLE' AS OBJECT_TYPE, '' AS COMMENTS "
                "FROM USER_TABLES"
            )
        else:
            cur.execute(
                "SELECT owner, table_name, 'TABLE', comments FROM all_tab_comments "
                "WHERE table_type IN ('TABLE', 'VIEW')"
            )
        for row in cur.fetchall() or []:
            owner = str(row[0] or '')
            name = str(row[1] or '')
            key = (owner, name)
            objects[key] = {
                'owner': owner,
                'name': name,
                'object_type': str(row[2] or 'TABLE'),
                'comment': str(row[3] or ''),
                'columns': [],
            }
        if dialect == 'dameng':
            cur.execute(
                "SELECT USER, TABLE_NAME, COLUMN_NAME, DATA_TYPE, NULLABLE, COLUMN_ID, '' "
                "FROM USER_TAB_COLUMNS ORDER BY TABLE_NAME, COLUMN_ID"
            )
        else:
            cur.execute(
                "SELECT col.owner, col.table_name, col.column_name, col.data_type, col.nullable, "
                "col.column_id, cc.comments "
                "FROM all_tab_columns col "
                "LEFT JOIN all_col_comments cc ON cc.owner = col.owner "
                "AND cc.table_name = col.table_name AND cc.column_name = col.column_name"
            )
        for row in cur.fetchall() or []:
            key = (str(row[0] or ''), str(row[1] or ''))
            target = objects.setdefault(key, {
                'owner': key[0], 'name': key[1], 'object_type': 'TABLE', 'comment': '', 'columns': [],
            })
            target['columns'].append({
                'name': str(row[2] or ''),
                'data_type': str(row[3] or ''),
                'nullable': str(row[4] or 'Y').upper() != 'N',
                'position': int(row[5] or 0),
                'comment': str(row[6] or ''),
                'primary_key': False,
                'indexed': False,
            })
        if dialect != 'dameng':
            try:
                cur.execute(
                    "SELECT cols.owner, cols.table_name, cols.column_name "
                    "FROM all_constraints cons JOIN all_cons_columns cols "
                    "ON cons.owner = cols.owner AND cons.constraint_name = cols.constraint_name "
                    "WHERE cons.constraint_type = 'P'"
                )
                for row in cur.fetchall() or []:
                    target = objects.get((str(row[0] or ''), str(row[1] or '')))
                    if not target:
                        continue
                    wanted = str(row[2] or '').upper()
                    for col in target.get('columns') or []:
                        if str(col.get('name') or '').upper() == wanted:
                            col['primary_key'] = True
                            col['indexed'] = True
            except Exception:
                pass
            try:
                cur.execute(
                    "SELECT table_owner, table_name, column_name FROM all_ind_columns"
                )
                for row in cur.fetchall() or []:
                    target = objects.get((str(row[0] or ''), str(row[1] or '')))
                    if not target:
                        continue
                    wanted = str(row[2] or '').upper()
                    for col in target.get('columns') or []:
                        if str(col.get('name') or '').upper() == wanted:
                            col['indexed'] = True
            except Exception:
                pass
            _attach_oracle_indexes(cur, objects)
        else:
            for target in objects.values():
                target['indexes'] = []
                target['index_metadata_status'] = 'unavailable'
    finally:
        try:
            cur.close()
        except Exception:
            pass
    return [_clean_object(item) for item in objects.values()], False


def _attach_oracle_indexes(cur, objects: dict) -> None:
    for target in objects.values():
        target.setdefault('indexes', [])
        target['index_metadata_status'] = 'ok'
    try:
        cur.execute(
            "SELECT table_owner, table_name, index_name, uniqueness, index_type "
            "FROM all_indexes"
        )
        meta = {}
        for row in cur.fetchall() or []:
            key = (str(row[0] or ''), str(row[1] or ''), str(row[2] or ''))
            meta[key] = {
                'name': str(row[2] or ''),
                'unique': str(row[3] or '').upper() == 'UNIQUE',
                'index_type': str(row[4] or ''),
                'columns': [],
            }
        cur.execute(
            "SELECT table_owner, table_name, index_name, column_name, column_position "
            "FROM all_ind_columns ORDER BY table_owner, table_name, index_name, column_position"
        )
        for row in cur.fetchall() or []:
            key = (str(row[0] or ''), str(row[1] or ''), str(row[2] or ''))
            item = meta.setdefault(key, {
                'name': str(row[2] or ''),
                'unique': False,
                'index_type': '',
                'columns': [],
            })
            item['columns'].append({'name': str(row[3] or ''), 'position': int(row[4] or 0)})
        grouped = {}
        for (owner, table, _index), item in meta.items():
            grouped.setdefault((owner, table), []).append(item)
        for key, indexes in grouped.items():
            target = objects.get(key)
            if not target:
                continue
            target['indexes'] = indexes
            target['index_metadata_status'] = 'ok'
    except Exception as exc:
        warning = redact_error(str(exc))
        for target in objects.values():
            target['indexes'] = []
            target['index_metadata_status'] = 'unavailable'
            target['index_warning'] = warning


def _scan_information_schema(conn, item: dict) -> tuple[list, bool]:
    database = str((item or {}).get('database') or '').strip()
    cur = conn.cursor()
    objects = {}
    try:
        if database:
            cur.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_COMMENT "
                "FROM information_schema.tables WHERE TABLE_SCHEMA = %s",
                (database,),
            )
        else:
            cur.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_COMMENT "
                "FROM information_schema.tables WHERE TABLE_SCHEMA = DATABASE()"
            )
        for row in cur.fetchall() or []:
            owner = str(row[0] or '')
            name = str(row[1] or '')
            objects[(owner, name)] = {
                'owner': owner,
                'name': name,
                'object_type': str(row[2] or 'TABLE'),
                'comment': str(row[3] or ''),
                'columns': [],
            }
        if database:
            cur.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, "
                "ORDINAL_POSITION, COLUMN_COMMENT, COLUMN_KEY FROM information_schema.columns "
                "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION",
                (database,),
            )
        else:
            cur.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, "
                "ORDINAL_POSITION, COLUMN_COMMENT, COLUMN_KEY FROM information_schema.columns "
                "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION"
            )
        for row in cur.fetchall() or []:
            key = (str(row[0] or ''), str(row[1] or ''))
            target = objects.setdefault(key, {
                'owner': key[0], 'name': key[1], 'object_type': 'TABLE', 'comment': '', 'columns': [],
            })
            key_flag = str(row[7] or '').upper() if len(row) > 7 else ''
            target['columns'].append({
                'name': str(row[2] or ''),
                'data_type': str(row[3] or ''),
                'nullable': str(row[4] or 'YES').upper() != 'NO',
                'position': int(row[5] or 0),
                'comment': str(row[6] or ''),
                'primary_key': key_flag == 'PRI',
                'indexed': key_flag in ('PRI', 'UNI', 'MUL'),
            })
        _attach_mysql_indexes(cur, objects, database)
    finally:
        try:
            cur.close()
        except Exception:
            pass
    return [_clean_object(item) for item in objects.values()], False


def _attach_mysql_indexes(cur, objects: dict, database: str) -> None:
    for target in objects.values():
        target.setdefault('indexes', [])
        target['index_metadata_status'] = 'ok'
    sql = (
        "SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, NON_UNIQUE, INDEX_TYPE, "
        "COLUMN_NAME, SEQ_IN_INDEX FROM information_schema.statistics"
    )
    try:
        if database:
            cur.execute(sql + " WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX", (database,))
        else:
            cur.execute(sql + " WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX")
        grouped = {}
        for row in cur.fetchall() or []:
            key = (str(row[0] or ''), str(row[1] or ''))
            name = str(row[2] or '')
            bucket = grouped.setdefault(key, {})
            item = bucket.setdefault(name, {
                'name': name,
                'unique': str(row[3]) in ('0', '0.0', 'false', 'False') or row[3] == 0,
                'index_type': str(row[4] or ''),
                'columns': [],
            })
            item['columns'].append({'name': str(row[5] or ''), 'position': int(row[6] or 0)})
        for key, indexes in grouped.items():
            target = objects.get(key)
            if not target:
                continue
            target['indexes'] = list(indexes.values())
            target['index_metadata_status'] = 'ok'
    except Exception as exc:
        warning = redact_error(str(exc))
        for target in objects.values():
            target['indexes'] = []
            target['index_metadata_status'] = 'unavailable'
            target['index_warning'] = warning


def _flatten_doc(doc, prefix: str = '', out: dict = None) -> dict:
    """递归展开 MongoDB 文档：嵌套字段用点号路径，数组标记 []。"""
    if out is None:
        out = {}
    if not isinstance(doc, dict):
        out[prefix or '_id'] = doc
        return out
    for key, value in doc.items():
        path = f'{prefix}.{key}' if prefix else str(key)
        if isinstance(value, dict):
            _flatten_doc(value, path, out)
        elif isinstance(value, list):
            if value and all(isinstance(item, dict) for item in value):
                _flatten_doc(value[0], f'{path}[]', out)
            else:
                out[path] = value
        else:
            out[path] = value
    return out


def _scan_mongo(conn) -> tuple[list, bool]:
    names = list(conn.list_collection_names())
    objects = []
    for name in names:
        merged = {}
        try:
            cursor = conn[name].find().limit(20)
            for doc in cursor:
                if isinstance(doc, dict):
                    _flatten_doc(doc, out=merged)
        except Exception:
            merged = {}
        if not merged:
            merged = {'_id': None}
        columns = []
        for index, key in enumerate(merged.keys(), start=1):
            columns.append({
                'name': str(key),
                'data_type': _type_name(merged.get(key)),
                'nullable': True,
                'position': index,
                'comment': '',
            })
        objects.append({
            'owner': '',
            'name': str(name),
            'object_type': 'COLLECTION',
            'comment': '',
            'inferred': True,
            'columns': columns,
        })
    return [_clean_object(item) for item in objects], False


def _redis_key_columns(conn, name: str, kind: str) -> list[dict]:
    """按 key 类型用只读命令生成真实字段结构（只存字段名+类型，绝不存值）。"""
    kind = (kind or 'none').lower()
    try:
        if kind == 'string':
            return [{'name': 'value', 'data_type': 'string', 'nullable': True, 'position': 1, 'comment': ''}]
        if kind == 'hash':
            fields = conn.hkeys(name) or []
            columns = []
            for index, field in enumerate(fields[:20], start=1):
                raw = conn.hget(name, field)
                columns.append({'name': _b(field), 'data_type': _type_name(raw), 'nullable': True, 'position': index, 'comment': ''})
            return columns or [{'name': 'field', 'data_type': 'string', 'nullable': True, 'position': 1, 'comment': ''}]
        if kind == 'list':
            values = conn.lrange(name, 0, 0) or []
            data_type = _type_name(values[0]) if values else 'string'
            return [{'name': '[0]', 'data_type': data_type, 'nullable': True, 'position': 1, 'comment': ''}]
        if kind == 'set':
            members = list(conn.sscan(name)[1] or [])
            data_type = _type_name(members[0]) if members else 'string'
            return [{'name': 'member', 'data_type': data_type, 'nullable': True, 'position': 1, 'comment': ''}]
        if kind == 'zset':
            entries = conn.zrange(name, 0, 0, withscores=True) or []
            data_type = _type_name(entries[0][0]) if entries else 'string'
            return [
                {'name': 'member', 'data_type': data_type, 'nullable': True, 'position': 1, 'comment': ''},
                {'name': 'score', 'data_type': 'double', 'nullable': True, 'position': 2, 'comment': ''},
            ]
        if kind == 'stream':
            return [{'name': 'entry', 'data_type': 'stream', 'nullable': True, 'position': 1, 'comment': ''}]
    except Exception:
        pass
    return [{'name': 'value', 'data_type': kind, 'nullable': True, 'position': 1, 'comment': ''}]


def _scan_redis(conn) -> tuple[list, bool]:
    objects = []
    truncated = False
    try:
        for key in conn.scan_iter(match='*', count=200):
            name = _b(key)
            try:
                kind = _b(conn.type(name)) or 'none'
            except Exception:
                kind = 'unknown'
            columns = _redis_key_columns(conn, name, kind)
            objects.append({
                'owner': '',
                'name': name,
                'object_type': kind.upper(),
                'comment': f'Redis {kind}',
                'columns': columns,
            })
            if len(objects) >= REDIS_KEY_CAP:
                truncated = True
                break
    except Exception as exc:
        raise RuntimeError(f'Redis SCAN 失败：{redact_error(str(exc))}') from exc
    return [_clean_object(item) for item in objects], truncated


def clip_snapshot_for_prompt(
    snap: dict | None,
    *,
    selected_tables=None,
    selected_fields=None,
    max_chars: int = 8000,
) -> str:
    if not snap:
        return ''
    tables = [str(item).strip() for item in (selected_tables or []) if str(item).strip()]
    fields = [str(item).strip() for item in (selected_fields or []) if str(item).strip()]
    wanted = {name.upper() for name in tables}
    lines = []
    for obj in snap.get('objects') or []:
        name = str(obj.get('name') or '')
        if wanted and name.upper() not in wanted and str(obj.get('owner') or '').upper() not in wanted:
            if f"{obj.get('owner')}.{name}".upper() not in wanted:
                continue
        owner = str(obj.get('owner') or '')
        prefix = f"{owner}." if owner else ''
        cols = obj.get('columns') or []
        if fields:
            allow = {item.upper() for item in fields}
            cols = [col for col in cols if str(col.get('name') or '').upper() in allow]
        col_text = ', '.join(
            f"{col.get('name')} {col.get('data_type')}" for col in cols[:40]
        )
        comment = str(obj.get('comment') or '')
        extra = f' -- {comment}' if comment else ''
        lines.append(f"- {prefix}{name}({col_text}){extra}")
        if sum(len(line) for line in lines) > max_chars:
            break
    text = '\n'.join(lines)
    return text[:max_chars]


def format_object_label(obj: dict | None) -> str:
    data = obj if isinstance(obj, dict) else {}
    owner = str(data.get('owner') or '').strip()
    name = str(data.get('name') or '').strip()
    qn = f'{owner}.{name}' if owner and name else (name or owner)
    kind = str(data.get('object_type') or 'TABLE')
    comment = str(data.get('comment') or '').strip()
    label = f'{qn}  [{kind}]' if qn else f'[{kind}]'
    if comment:
        label = f'{label}  {comment}'
    return label


def format_field_label(col: dict | None) -> str:
    data = col if isinstance(col, dict) else {}
    name = str(data.get('name') or '').strip()
    dtype = str(data.get('data_type') or '').strip()
    comment = str(data.get('comment') or '').strip()
    parts = [part for part in (name, dtype, comment) if part]
    return '  '.join(parts)


def search_objects(snap: dict | None, keyword: str = '') -> list[dict]:
    needle = str(keyword or '').strip().lower()
    result = []
    for obj in (snap or {}).get('objects') or []:
        hay = ' '.join([
            str(obj.get('name') or ''),
            str(obj.get('owner') or ''),
            str(obj.get('comment') or ''),
            str(obj.get('object_type') or ''),
        ]).lower()
        if needle and needle not in hay:
            continue
        result.append(obj)
    return result


def search_fields(obj: dict | None, keyword: str = '') -> list[dict]:
    needle = str(keyword or '').strip().lower()
    result = []
    for col in (obj or {}).get('columns') or []:
        hay = ' '.join([str(col.get('name') or ''), str(col.get('comment') or ''), str(col.get('data_type') or '')]).lower()
        if needle and needle not in hay:
            continue
        result.append(col)
    result.sort(key=lambda col: str(col.get('name') or '').lower())
    return result
