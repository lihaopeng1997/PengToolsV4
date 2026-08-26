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
        'version': 1,
        'scanned_at': '',
        'status': status,
        'truncated': False,
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
    return {
        'owner': str(item.get('owner') or ''),
        'name': str(item.get('name') or ''),
        'object_type': str(item.get('object_type') or 'TABLE'),
        'comment': str(item.get('comment') or ''),
        'inferred': bool(item.get('inferred')),
        'columns': columns,
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
        if truncated:
            payload['warning'] = '对象数量已截断，仅保留可见范围内的前若干项'
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
    finally:
        try:
            cur.close()
        except Exception:
            pass
    return [_clean_object(item) for item in objects.values()], False


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
    finally:
        try:
            cur.close()
        except Exception:
            pass
    return [_clean_object(item) for item in objects.values()], False


def _scan_mongo(conn) -> tuple[list, bool]:
    names = list(conn.list_collection_names())
    objects = []
    for name in names:
        columns = []
        try:
            doc = conn[name].find_one() or {}
        except Exception:
            doc = {}
        if isinstance(doc, dict):
            for index, key in enumerate(doc.keys(), start=1):
                columns.append({
                    'name': str(key),
                    'data_type': _type_name(doc.get(key)),
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


def _scan_redis(conn) -> tuple[list, bool]:
    objects = []
    truncated = False
    try:
        for key in conn.scan_iter(match='*', count=200):
            name = str(key)
            try:
                kind = str(conn.type(name) or 'none')
            except Exception:
                kind = 'unknown'
            objects.append({
                'owner': '',
                'name': name,
                'object_type': kind,
                'comment': '',
                'columns': [{'name': kind, 'data_type': kind, 'nullable': True, 'position': 1, 'comment': ''}],
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
