# -*- coding: utf-8 -*-
"""模型工作台：多方言查询连接（Oracle / MySQL / OceanBase / 达梦 / Redis / MongoDB）。

Oracle 瘦模式依赖 cryptography 的 pbkdf2 等子模块；打包时必须显式导入，否则 DPY-3016。
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from config import HARNESS_CONNECTIONS_FILE, ensure_config_dir

# PyInstaller 静态分析扫不到 oracledb 内部 import，必须在本模块顶层拉齐。
try:
    from cryptography import x509  # noqa: F401
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: F401
    from cryptography.hazmat.primitives.asymmetric import padding  # noqa: F401
    from cryptography.hazmat.primitives.kdf import pbkdf2  # noqa: F401
    import encodings.idna  # noqa: F401
except Exception:
    pass
from tools.sql_guard import is_read_query, leading_verb, reject_reason, strip_sql_comments

PAGE_SIZE = 20
MAX_ROWS = 2000
CELL_MAX = 200

DIALECTS = (
    ('oracle', 'Oracle'),
    ('mysql', 'MySQL'),
    ('oceanbase', 'OceanBase'),
    ('dameng', '达梦'),
    ('redis', 'Redis'),
    ('mongodb', 'MongoDB'),
)

DEFAULT_PORTS = {
    'oracle': 1521,
    'oceanbase': 2881,
    'mysql': 3306,
    'dameng': 5236,
    'redis': 6379,
    'mongodb': 27017,
}

NOSQL = frozenset({'redis', 'mongodb'})


class DbError(Exception):
    pass


def _encrypt(plain: str) -> str:
    if not str(plain or ''):
        return ''
    from tools.secure_store import encrypt_secret
    return encrypt_secret(plain)


def _decrypt(token: str) -> str:
    if not str(token or ''):
        return ''
    from tools.secure_store import decrypt_secret
    return decrypt_secret(token)


def load_connections() -> list[dict]:
    ensure_config_dir()
    try:
        with open(HARNESS_CONNECTIONS_FILE, 'r', encoding='utf-8') as stream:
            raw = json.load(stream)
    except (OSError, ValueError, TypeError):
        return []
    items = raw.get('items') if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if isinstance(item, dict) and item.get('id'):
            result.append(dict(item))
    return result


def save_connections(items: list[dict]) -> list[dict]:
    ensure_config_dir()
    os.makedirs(os.path.dirname(HARNESS_CONNECTIONS_FILE), exist_ok=True)
    payload = {'items': list(items)}
    with open(HARNESS_CONNECTIONS_FILE, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
    return items


def upsert_connection(item: dict, plain_password: str | None = None) -> dict:
    rows = load_connections()
    data = dict(item)
    data['id'] = str(data.get('id') or uuid.uuid4().hex)
    data['name'] = str(data.get('name') or '未命名连接').strip() or '未命名连接'
    data['dialect'] = str(data.get('dialect') or 'oracle').strip().lower()
    data['host'] = str(data.get('host') or '').strip()
    try:
        data['port'] = int(data.get('port') or DEFAULT_PORTS.get(data['dialect'], 1521))
    except (TypeError, ValueError):
        data['port'] = DEFAULT_PORTS.get(data['dialect'], 1521)
    data['database'] = str(data.get('database') or '').strip()
    data['username'] = str(data.get('username') or '').strip()
    if plain_password is not None:
        data['password'] = _encrypt(plain_password)
    elif 'password' not in data:
        data['password'] = ''
    found = False
    for index, row in enumerate(rows):
        if row.get('id') == data['id']:
            if not data.get('password') and row.get('password'):
                data['password'] = row.get('password')
            rows[index] = data
            found = True
            break
    if not found:
        rows.append(data)
    save_connections(rows)
    return data


def delete_connection(conn_id: str) -> None:
    save_connections([row for row in load_connections() if row.get('id') != conn_id])


def open_connection(item: dict):
    dialect = str(item.get('dialect') or 'oracle').lower()
    password = _decrypt(item.get('password') or '')
    host = str(item.get('host') or '').strip()
    port = int(item.get('port') or DEFAULT_PORTS.get(dialect, 1521))
    database = str(item.get('database') or '').strip()
    username = str(item.get('username') or '').strip()
    if dialect in ('oracle',):
        try:
            import oracledb
        except ImportError as exc:
            raise DbError('未安装 oracledb，请安装依赖后重试') from exc
        dsn = database if '/' in database or ':' in database else f'{host}:{port}/{database}'
        try:
            return oracledb.connect(user=username, password=password, dsn=dsn)
        except Exception as exc:
            text = str(exc)
            if 'DPY-3016' in text or 'pbkdf2' in text:
                raise DbError(
                    'Oracle 瘦模式缺少 cryptography（pbkdf2）。请换用最新离线安装包；'
                    '或本机已装 Instant Client 时，把其 bin 加入 PATH 后重试。'
                    f' 原始错误：{text}'
                ) from exc
            raise DbError(f'Oracle 连接失败：{text}') from exc
    if dialect in ('oceanbase', 'mysql'):
        try:
            import pymysql
        except ImportError as exc:
            raise DbError('未安装 pymysql，请安装依赖后重试') from exc
        try:
            return pymysql.connect(
                host=host, port=port, user=username, password=password,
                database=database or None, charset='utf8mb4',
                cursorclass=pymysql.cursors.Cursor,
            )
        except Exception as exc:
            raise DbError(f'MySQL/OceanBase 连接失败：{exc}') from exc
    if dialect == 'dameng':
        try:
            import dmPython
        except ImportError as exc:
            raise DbError('未安装达梦驱动 dmPython。可在本机安装达梦 Python 驱动后再连。') from exc
        try:
            return dmPython.connect(user=username, password=password, server=f'{host}:{port}', schema=database or None)
        except Exception as exc:
            raise DbError(f'达梦连接失败：{exc}') from exc
    if dialect == 'redis':
        try:
            import redis
        except ImportError as exc:
            raise DbError('未安装 redis，请安装依赖后重试') from exc
        db_index = 0
        try:
            db_index = int(database or 0)
        except ValueError:
            db_index = 0
        try:
            client = redis.Redis(
                host=host or '127.0.0.1',
                port=port,
                password=password or None,
                username=username or None,
                db=db_index,
                decode_responses=True,
                socket_connect_timeout=8,
            )
            client.ping()
            return client
        except Exception as exc:
            raise DbError(f'Redis 连接失败：{exc}') from exc
    if dialect == 'mongodb':
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise DbError('未安装 pymongo，请安装依赖后重试') from exc
        if not database:
            raise DbError('MongoDB 请填写库名')
        try:
            kwargs = {'host': host or '127.0.0.1', 'port': port, 'serverSelectionTimeoutMS': 8000}
            if username:
                kwargs['username'] = username
                kwargs['password'] = password
            client = MongoClient(**kwargs)
            client.admin.command('ping')
            db = client[database]
            return db
        except Exception as exc:
            raise DbError(f'MongoDB 连接失败：{exc}') from exc
    raise DbError(f'不支持的数据库类型：{dialect}')


def close_connection(conn) -> None:
    if conn is None:
        return
    client = getattr(conn, 'client', None)
    if client is not None and client is not conn:
        try:
            client.close()
            return
        except Exception:
            pass
    try:
        conn.close()
    except Exception:
        pass


def _cursor(conn):
    return conn.cursor()


def list_tables(conn, dialect: str) -> list[str]:
    dialect = (dialect or 'oracle').lower()
    if dialect == 'redis':
        return _redis_list_keys(conn, limit=80)
    if dialect == 'mongodb':
        try:
            return sorted(str(name) for name in conn.list_collection_names())
        except Exception as exc:
            raise DbError(f'读取集合失败：{exc}') from exc
    cur = _cursor(conn)
    try:
        if dialect in ('oceanbase', 'mysql'):
            cur.execute('SHOW TABLES')
            rows = cur.fetchall() or []
            return [str(row[0]) for row in rows if row]
        if dialect == 'dameng':
            cur.execute("SELECT TABLE_NAME FROM USER_TABLES ORDER BY TABLE_NAME")
        else:
            cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
        rows = cur.fetchall() or []
        return [str(row[0]) for row in rows if row]
    except Exception as exc:
        raise DbError(f'读取表清单失败：{exc}') from exc
    finally:
        try:
            cur.close()
        except Exception:
            pass


def list_columns(conn, dialect: str, table: str) -> list[str]:
    dialect = (dialect or 'oracle').lower()
    table = str(table or '').strip()
    if dialect == 'redis':
        try:
            return [str(conn.type(table) or 'none')]
        except Exception:
            return []
    if dialect == 'mongodb':
        try:
            doc = conn[table].find_one() or {}
            return [str(key) for key in list(doc.keys())[:20]]
        except Exception:
            return []
    cur = _cursor(conn)
    try:
        if dialect in ('oceanbase', 'mysql'):
            cur.execute(f'SHOW COLUMNS FROM `{table.replace("`", "")}`')
            rows = cur.fetchall() or []
            return [str(row[0]) for row in rows if row]
        cur.execute(
            "SELECT column_name FROM user_tab_columns WHERE table_name = :1 ORDER BY column_id",
            [table.upper()],
        )
        rows = cur.fetchall() or []
        return [str(row[0]) for row in rows if row]
    except Exception as exc:
        raise DbError(f'读取字段失败：{exc}') from exc
    finally:
        try:
            cur.close()
        except Exception:
            pass


def schema_summary(conn, dialect: str, table_limit: int = 40, col_limit: int = 12) -> str:
    tables = list_tables(conn, dialect)[:table_limit]
    lines = [f'方言：{dialect}', f'表数量（截取前 {len(tables)}）：']
    for table in tables:
        try:
            cols = list_columns(conn, dialect, table)[:col_limit]
        except Exception:
            cols = []
        lines.append(f'- {table}({", ".join(cols)})' if cols else f'- {table}')
    return '\n'.join(lines)


def _wrap_paged(sql: str, dialect: str, offset: int, limit: int) -> str:
    body = strip_sql_comments(sql)
    if body.endswith(';'):
        body = body[:-1].strip()
    verb = leading_verb(body)
    if verb in ('show', 'desc', 'describe', 'explain', 'pragma'):
        return body
    dialect = (dialect or 'oracle').lower()
    if dialect in ('oceanbase', 'mysql'):
        return f'SELECT * FROM ({body}) peng_q LIMIT {int(limit)} OFFSET {int(offset)}'
    end = int(offset) + int(limit)
    return (
        'SELECT * FROM ('
        f'SELECT peng_q.*, ROWNUM AS peng_rn FROM ({body}) peng_q WHERE ROWNUM <= {end}'
        f') WHERE peng_rn > {int(offset)}'
    )


def _redis_list_keys(conn, limit: int = 80) -> list[str]:
    keys = []
    try:
        for key in conn.scan_iter(match='*', count=min(int(limit), 200)):
            keys.append(str(key))
            if len(keys) >= int(limit):
                break
    except Exception as exc:
        raise DbError(f'Redis SCAN 失败：{exc}') from exc
    return keys


def _parse_redis_command(text: str) -> list[str]:
    import shlex
    raw = strip_sql_comments(text)
    if raw.endswith(';'):
        raw = raw[:-1].strip()
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()
    return [str(item) for item in parts if str(item)]


def _run_redis(conn, sql: str, offset: int, limit: int) -> dict:
    parts = _parse_redis_command(sql)
    if not parts:
        raise DbError('Redis 命令为空')
    cmd = parts[0].lower()
    args = parts[1:]
    rows = []
    columns = ['field', 'value']
    cursor_out = 0
    has_more = False
    if cmd == 'scan':
        cursor = int(offset or 0)
        match = '*'
        count = int(limit)
        if 'match' in [a.lower() for a in args]:
            for i, token in enumerate(args):
                if str(token).lower() == 'match' and i + 1 < len(args):
                    match = args[i + 1]
                if str(token).lower() == 'count' and i + 1 < len(args):
                    try:
                        count = int(args[i + 1])
                    except ValueError:
                        pass
        cursor_out, keys = conn.scan(cursor=cursor, match=match, count=max(count, int(limit)))
        columns = ['key']
        rows = [[str(key)] for key in keys]
        has_more = int(cursor_out or 0) != 0
        return {
            'columns': columns, 'rows': rows, 'offset': int(cursor_out or 0),
            'limit': int(limit), 'has_more': has_more, 'sql': sql,
        }
    if cmd in ('get', 'type', 'ttl', 'pttl', 'strlen', 'exists'):
        key = args[0] if args else ''
        value = conn.execute_command(*parts)
        rows = [[cmd, _stringify(value)]]
    elif cmd == 'mget':
        values = conn.mget(args) if args else []
        rows = [[key, _stringify(val)] for key, val in zip(args, values)]
    elif cmd == 'hgetall':
        mapping = conn.hgetall(args[0]) if args else {}
        rows = [[str(k), _stringify(v)] for k, v in mapping.items()]
    elif cmd in ('hkeys', 'hvals'):
        values = conn.execute_command(*parts) or []
        rows = [[str(item)] for item in values]
        columns = ['value']
    elif cmd == 'lrange':
        values = conn.execute_command(*parts) or []
        rows = [[str(i), _stringify(v)] for i, v in enumerate(values)]
        columns = ['index', 'value']
    elif cmd in ('smembers',):
        values = list(conn.smembers(args[0]) if args else [])
        rows = [[_stringify(v)] for v in values]
        columns = ['value']
    else:
        value = conn.execute_command(*parts)
        if isinstance(value, (list, tuple)):
            rows = [[_stringify(item)] for item in value]
            columns = ['value']
        elif isinstance(value, dict):
            rows = [[str(k), _stringify(v)] for k, v in value.items()]
        else:
            rows = [[cmd, _stringify(value)]]
    sliced = rows[int(offset): int(offset) + int(limit)]
    return {
        'columns': columns,
        'rows': sliced,
        'offset': int(offset) + len(sliced),
        'limit': int(limit),
        'has_more': int(offset) + len(sliced) < len(rows),
        'sql': sql,
    }


def _parse_mongo_query(text: str) -> tuple[str, dict]:
    import json
    import re
    from tools.ai_harness import strip_markdown_fence
    raw = strip_markdown_fence(strip_sql_comments(text))
    match = re.search(r'db\.([A-Za-z0-9_]+)\.find\((.*)\)', raw, re.DOTALL)
    if match:
        collection = match.group(1)
        body = (match.group(2) or '').strip()
        filt = json.loads(body) if body else {}
        if not isinstance(filt, dict):
            filt = {}
        return collection, filt
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise DbError('MongoDB 查询必须是 JSON 对象')
    collection = str(data.get('collection') or data.get('coll') or '').strip()
    if not collection:
        raise DbError('MongoDB JSON 需要 collection')
    filt = data.get('filter') if isinstance(data.get('filter'), dict) else {}
    return collection, filt


def _run_mongo(conn, sql: str, offset: int, limit: int) -> dict:
    collection, filt = _parse_mongo_query(sql)
    cursor = conn[collection].find(filt).skip(int(offset)).limit(int(limit) + 1)
    docs = list(cursor)
    has_more = len(docs) > int(limit)
    docs = docs[: int(limit)]
    keys = []
    for doc in docs:
        for key in doc.keys():
            if key not in keys:
                keys.append(str(key))
    columns = keys or ['_id']
    rows = []
    for doc in docs:
        rows.append([_stringify(doc.get(col)) for col in columns])
    return {
        'columns': columns,
        'rows': rows,
        'offset': int(offset) + len(rows),
        'limit': int(limit),
        'has_more': has_more,
        'sql': sql,
    }


def _stringify(value: Any) -> str:
    if value is None:
        return ''
    text = str(value)
    if len(text) > CELL_MAX:
        return text[:CELL_MAX] + '…'
    return text


def run_read_query(conn, dialect: str, sql: str, *, offset: int = 0, limit: int = PAGE_SIZE) -> dict:
    kind = str(dialect or 'oracle').lower()
    reason = reject_reason(sql, kind)
    if reason:
        raise DbError(reason)
    if kind == 'redis':
        return _run_redis(conn, sql, int(offset), min(int(limit), MAX_ROWS))
    if kind == 'mongodb':
        return _run_mongo(conn, sql, int(offset), min(int(limit), MAX_ROWS))
    if not is_read_query(sql):
        raise DbError('仅允许查询语句')
    wrapped = _wrap_paged(sql, dialect, offset, min(int(limit), MAX_ROWS))
    cur = _cursor(conn)
    try:
        cur.execute(wrapped)
        columns = [str(item[0]) for item in (cur.description or [])]
        rows = cur.fetchall() or []
        drop = [i for i, name in enumerate(columns) if str(name).upper() == 'PENG_RN']
        if drop:
            columns = [name for i, name in enumerate(columns) if i not in drop]
            trimmed = []
            for row in rows:
                trimmed.append(tuple(cell for i, cell in enumerate(row) if i not in drop))
            rows = trimmed
        data = [[_stringify(cell) for cell in row] for row in rows]
        has_more = len(data) >= int(limit)
        return {
            'columns': columns,
            'rows': data,
            'offset': int(offset) + len(data),
            'limit': int(limit),
            'has_more': has_more,
            'sql': sql,
        }
    except Exception as exc:
        raise DbError(f'查询失败：{exc}') from exc
    finally:
        try:
            cur.close()
        except Exception:
            pass
