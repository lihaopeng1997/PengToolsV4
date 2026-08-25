# -*- coding: utf-8 -*-
"""模型工作台：多方言查询连接（Oracle / MySQL / OceanBase / 达梦）。"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from config import HARNESS_CONNECTIONS_FILE, ensure_config_dir
from tools.sql_guard import is_read_query, leading_verb, reject_reason, strip_sql_comments

PAGE_SIZE = 20
MAX_ROWS = 2000
CELL_MAX = 200

DIALECTS = (
    ('oracle', 'Oracle'),
    ('mysql', 'MySQL'),
    ('oceanbase', 'OceanBase'),
    ('dameng', '达梦'),
)

DEFAULT_PORTS = {
    'oracle': 1521,
    'oceanbase': 2881,
    'mysql': 3306,
    'dameng': 5236,
}


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
            raise DbError(f'Oracle 连接失败：{exc}') from exc
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
    raise DbError(f'不支持的数据库类型：{dialect}')


def close_connection(conn) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def _cursor(conn):
    return conn.cursor()


def list_tables(conn, dialect: str) -> list[str]:
    dialect = (dialect or 'oracle').lower()
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


def _stringify(value: Any) -> str:
    if value is None:
        return ''
    text = str(value)
    if len(text) > CELL_MAX:
        return text[:CELL_MAX] + '…'
    return text


def run_read_query(conn, dialect: str, sql: str, *, offset: int = 0, limit: int = PAGE_SIZE) -> dict:
    reason = reject_reason(sql)
    if reason:
        raise DbError(reason)
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
            'offset': int(offset),
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
