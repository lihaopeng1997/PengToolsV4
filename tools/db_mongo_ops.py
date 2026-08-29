# -*- coding: utf-8 -*-
"""MongoDB 专用操作：集合列表、文档查询/计数、聚合、插入/删除。

供 panels/db_mongodb_panel.py 调用；连接由 tools.db_connect.open_connection 建立
（返回 database 对象）。文档字段值统一转文本，避免二进制/特殊类型渲染异常。
"""

from __future__ import annotations

from typing import Any

from tools.db_connect import DbError


def _stringify(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, (dict, list)):
        import json
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def list_collections(conn) -> list[str]:
    try:
        return sorted(str(name) for name in conn.list_collection_names())
    except Exception as exc:
        raise DbError(f'读取集合失败：{exc}') from exc


def list_databases(client) -> list[str]:
    """从 MongoClient 列出数据库名。conn 为 database 对象时回退到 client。"""
    cli = getattr(conn, 'client', client) if hasattr(conn, 'client') else client
    try:
        return sorted(str(name) for name in (cli.list_database_names() or []))
    except Exception:
        return []


def find_docs(conn, collection: str, filt: dict | None = None, sort: list | None = None,
              projection: dict | None = None, skip: int = 0, limit: int = 50) -> list[dict]:
    """查询文档，返回文档列表（字段值保持原类型，由调用方决定渲染）。"""
    try:
        cursor = conn[collection].find(filt or {})
        if projection:
            cursor = cursor.projection(projection)
        if sort:
            cursor = cursor.sort(sort)
        docs = list(cursor.skip(int(skip)).limit(int(limit) + 1))
        return docs
    except Exception as exc:
        raise DbError(f'查询失败：{exc}') from exc


def count_docs(conn, collection: str, filt: dict | None = None) -> int:
    try:
        return int(conn[collection].count_documents(filt or {}))
    except Exception as exc:
        raise DbError(f'计数失败：{exc}') from exc


def aggregate_docs(conn, collection: str, pipeline: list) -> list[dict]:
    try:
        return list(conn[collection].aggregate(pipeline or []))
    except Exception as exc:
        raise DbError(f'聚合失败：{exc}') from exc


def insert_doc(conn, collection: str, doc: dict) -> str:
    if not isinstance(doc, dict):
        raise DbError('插入内容必须是 JSON 对象')
    try:
        result = conn[collection].insert_one(doc)
        return _stringify(getattr(result, 'inserted_id', ''))
    except Exception as exc:
        raise DbError(f'插入失败：{exc}') from exc


def delete_docs(conn, collection: str, filt: dict) -> int:
    if not isinstance(filt, dict):
        raise DbError('删除条件必须是 JSON 对象')
    try:
        result = conn[collection].delete_many(filt)
        return int(getattr(result, 'deleted_count', 0) or 0)
    except Exception as exc:
        raise DbError(f'删除失败：{exc}') from exc


def sample_schema(conn, collection: str, limit: int = 20) -> list[str]:
    """从抽样文档提取字段名列表（保留出现顺序，去重）。"""
    try:
        docs = list(conn[collection].find().limit(int(limit)))
    except Exception:
        return []
    keys: list[str] = []
    for doc in docs:
        if isinstance(doc, dict):
            for key in doc.keys():
                name = str(key)
                if name not in keys:
                    keys.append(name)
    return keys


def parse_mongo_query(text: str) -> dict:
    """把 Shell 风格的 find() 语句或 JSON 解析为查询参数。

    支持两种输入：
      1. db.<coll>.find({...}).sort({...}).limit(N)
      2. {"collection": "...", "filter": {...}, "sort": {...}, "projection": {...}}
    """
    import json
    import re
    from tools.ai_harness import strip_markdown_fence

    raw = strip_markdown_fence(str(text or '')).strip()
    # 方式一：Shell 风格
    match = re.search(r'db\.([A-Za-z0-9_]+)\.find\((.*)', raw, re.DOTALL)
    if match:
        collection = match.group(1)
        rest = (match.group(2) or '').strip()
        filt = {}
        sort = None
        projection = None
        limit = 0
        # 尝试剥离 find({...})
        fm = re.match(r'(\{.*?\})\s*(.*)', rest, re.DOTALL)
        if fm:
            try:
                filt = json.loads(fm.group(1))
            except ValueError:
                filt = {}
            rest = fm.group(2)
        else:
            fm = re.match(r'(\{.*\})', rest, re.DOTALL)
            if fm:
                try:
                    filt = json.loads(fm.group(1))
                except ValueError:
                    filt = {}
        sm = re.search(r'\.sort\((\{.*?\})\)', rest, re.DOTALL)
        if sm:
            try:
                sort = json.loads(sm.group(1))
            except ValueError:
                sort = None
        pm = re.search(r'\.projection?\((\{.*?\})\)', rest, re.DOTALL)
        if pm:
            try:
                projection = json.loads(pm.group(1))
            except ValueError:
                projection = None
        lm = re.search(r'\.limit\((\d+)\)', rest)
        if lm:
            try:
                limit = int(lm.group(1))
            except ValueError:
                limit = 0
        return {'collection': collection, 'filter': filt if isinstance(filt, dict) else {},
                'sort': sort, 'projection': projection, 'limit': limit}
    # 方式二：JSON
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DbError('查询格式无法解析：请用 db.col.find({...}) 或 JSON 对象') from exc
    if not isinstance(data, dict):
        raise DbError('MongoDB 查询必须是 JSON 对象')
    collection = str(data.get('collection') or data.get('coll') or '').strip()
    if not collection:
        raise DbError('MongoDB 查询需要 collection')
    filt = data.get('filter') if isinstance(data.get('filter'), dict) else {}
    return {
        'collection': collection,
        'filter': filt,
        'sort': data.get('sort') if isinstance(data.get('sort'), dict) else None,
        'projection': data.get('projection') if isinstance(data.get('projection'), dict) else None,
        'limit': int(data.get('limit') or 0),
    }
