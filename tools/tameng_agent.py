# -*- coding: utf-8 -*-
"""TamengAgent：仅基于当前有效 Schema 快照做字段证据链。不连库、不调模型、不使用 Qt。"""

from __future__ import annotations

import re

from tools.ai_object_context import field_qualified, qualified_name
from tools.schema_search import build_schema_search_index, search_schema_index
from tools.schema_snapshot import connection_fingerprint, snapshot_status
from tools.sql_guard import ai_draft_safety, classify_statement, split_sql_statements, strip_sql_comments

STATES = (
    'NO_CONNECTION', 'SNAPSHOT_MISSING', 'SNAPSHOT_STALE', 'SNAPSHOT_V1',
    'READY', 'RESOLVING', 'NEEDS_SELECTION', 'GENERATING', 'VALIDATING',
    'DRAFT_READY', 'BLOCKED',
)

FIELD_SYNONYMS = {
    '创建日期': ('创建日期', '创建时间', 'create date', 'created date', 'createdate', 'create_date', 'created_date'),
    '创建时间': ('创建时间', '创建日期', 'create time', 'created time', 'createtime', 'create_time', 'created_time'),
}

_SQL_KEYWORDS = frozenset({
    'select', 'from', 'where', 'and', 'or', 'not', 'in', 'is', 'null', 'as',
    'join', 'left', 'right', 'inner', 'outer', 'full', 'cross', 'on', 'group',
    'by', 'order', 'asc', 'desc', 'having', 'union', 'all', 'distinct', 'count',
    'sum', 'avg', 'min', 'max', 'case', 'when', 'then', 'else', 'end', 'insert',
    'into', 'values', 'update', 'set', 'delete', 'create', 'alter', 'drop',
    'table', 'view', 'index', 'with', 'exists', 'like', 'between', 'limit',
    'offset', 'dual', 'rownum', 'fetch', 'first', 'rows', 'only', 'over',
    'partition', 'using', 'natural', 'sysdate', 'systimestamp', 'current',
    'date', 'timestamp', 'true', 'false',
})
_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_$#]*')
_OBJ_RE = re.compile(
    r'\b(?:from|join|update|into)\s+([A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)?)',
    re.IGNORECASE,
)
_ALIAS_RE = re.compile(
    r'\b(?:from|join)\s+[A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)?\s+(?:as\s+)?([A-Za-z_][\w$#]*)',
    re.IGNORECASE,
)


def normalize_identifier(value: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def normalize_text(value: str) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip().lower())


def _snapshot_version(snap: dict | None) -> int:
    try:
        return int((snap or {}).get('version') or 1)
    except (TypeError, ValueError):
        return 1


def snapshot_gate(connection: dict | None, snapshot: dict | None, *, wants_index: bool = False) -> dict:
    if not connection:
        return {
            'ok': False,
            'state': 'NO_CONNECTION',
            'reason': '未选择数据库连接，无法生成基于结构的 SQL 草案。',
            'next_action': '选择连接',
        }
    if not snapshot:
        return {
            'ok': False,
            'state': 'SNAPSHOT_MISSING',
            'reason': '尚未扫描当前连接的结构，不会猜测表或字段。',
            'next_action': '扫描结构',
        }
    status = snapshot_status(connection, snapshot)
    if status.get('stale') or str(snapshot.get('fingerprint') or '') != connection_fingerprint(connection):
        return {
            'ok': False,
            'state': 'SNAPSHOT_STALE',
            'reason': '当前连接配置已变化，原快照不再适用。',
            'next_action': '重新扫描结构',
        }
    snap_status = str(snapshot.get('status') or '')
    if snap_status in ('failed', 'empty', 'missing') and not snapshot.get('objects'):
        return {
            'ok': False,
            'state': 'SNAPSHOT_MISSING',
            'reason': '尚未扫描当前连接的结构，不会猜测表或字段。',
            'next_action': '扫描结构',
        }
    if snap_status == 'failed':
        return {
            'ok': False,
            'state': 'SNAPSHOT_STALE',
            'reason': '快照扫描失败，请重新扫描。',
            'next_action': '重新扫描结构',
        }
    if not snapshot.get('objects'):
        return {
            'ok': False,
            'state': 'SNAPSHOT_MISSING',
            'reason': '尚未扫描当前连接的结构，不会猜测表或字段。',
            'next_action': '扫描结构',
        }
    version = _snapshot_version(snapshot)
    if wants_index and version < 2:
        return {
            'ok': False,
            'state': 'SNAPSHOT_V1',
            'reason': '索引信息不完整，请重新扫描。',
            'next_action': '重新扫描结构',
            'version': version,
        }
    if version < 2:
        return {
            'ok': True,
            'state': 'SNAPSHOT_V1',
            'reason': '索引信息不完整，请重新扫描后再生成涉及索引建议的 SQL。',
            'next_action': '',
            'version': version,
        }
    return {'ok': True, 'state': 'READY', 'reason': '', 'next_action': '', 'version': version}


def extract_intent_terms(question: str) -> dict:
    text = str(question or '').strip()
    lowered = text.lower()
    order = ''
    if any(mark in text for mark in ('倒序', '降序')) or re.search(r'\bdesc\b', lowered):
        order = 'DESC'
    elif any(mark in text for mark in ('正序', '升序')) or re.search(r'\basc\b', lowered):
        order = 'ASC'
    wants_index = any(mark in text for mark in ('索引', 'index'))
    wants_join = any(mark in text for mark in ('关联', '联表', 'join', '联合查询'))
    tables = []
    for match in re.findall(r'[A-Za-z][A-Za-z0-9_$#]*', text):
        if match.lower() in _SQL_KEYWORDS:
            continue
        if normalize_identifier(match) and match.lower() not in ('desc', 'asc', 'order', 'by', 'select', 'from'):
            tables.append(match)
    field_terms = []
    for key, variants in FIELD_SYNONYMS.items():
        if any(variant in lowered or variant in text for variant in variants):
            field_terms.append(key)
    remainder = text
    for stop in ('查询', '倒序', '正序', '降序', '升序', '联合查询', '关联', '索引', '中', '的', '按', '一下', '帮我', '查一下', '查下', '数据', '请', '给我'):
        remainder = remainder.replace(stop, ' ')
    for match in re.findall(r'[\u4e00-\u9fff]{2,8}', remainder):
        if match not in field_terms:
            field_terms.append(match)
    return {
        'raw': text,
        'tables': tables,
        'field_terms': field_terms,
        'order': order,
        'wants_index': wants_index,
        'wants_join': wants_join,
        'aggregate': bool(re.search(r'count|统计|合计|求和', lowered)),
    }


def _objects(snapshot: dict | None) -> list[dict]:
    return [item for item in ((snapshot or {}).get('objects') or []) if isinstance(item, dict)]


def _synonym_hit(term: str, col: dict) -> bool:
    comment = normalize_text(col.get('comment'))
    name_n = normalize_identifier(col.get('name'))
    for key, variants in FIELD_SYNONYMS.items():
        bag = {normalize_text(key), *(normalize_text(item) for item in variants)}
        if normalize_text(term) in bag or term == key:
            if comment in bag or name_n in {normalize_identifier(item) for item in variants}:
                return True
    return False


def _token_objects(snapshot: dict | None, tokens: dict | None) -> list[dict]:
    ctx = tokens if isinstance(tokens, dict) else {}
    wanted = {
        normalize_identifier(item.get('qualified_name') or item.get('name'))
        for item in (ctx.get('selected_objects') or [])
    }
    wanted |= {
        normalize_identifier(item.get('object_qualified_name') or '')
        for item in (ctx.get('selected_fields') or [])
    }
    if not wanted:
        return []
    hits = []
    for obj in _objects(snapshot):
        key = normalize_identifier(qualified_name(obj) or obj.get('name'))
        if key in wanted:
            hits.append(obj)
    return hits


def resolve_candidates(
    snapshot: dict | None,
    intent: dict,
    *,
    tokens=None,
    current_table=None,
    current_fields=None,
    confirmed=None,
) -> dict:
    intent = intent if isinstance(intent, dict) else extract_intent_terms('')
    objects = _objects(snapshot)
    truncated = bool((snapshot or {}).get('truncated'))
    token_objs = _token_objects(snapshot, tokens)
    table_terms = [str(item) for item in (intent.get('tables') or []) if str(item).strip()]
    field_terms = [str(item) for item in (intent.get('field_terms') or []) if str(item).strip()]
    raw_query = str(intent.get('raw') or '').strip()
    confirmed_keys = {normalize_identifier(item) for item in (confirmed or []) if str(item).strip()}

    search_index = build_schema_search_index(snapshot) if objects else None

    # 1. Object resolution (Priority: Explicit Tokens > Current UI Selection > Schema Search)
    object_hits = []
    if token_objs:
        object_hits = [
            {'object': obj, 'rank': 1, 'reason': '用户已选 Token', 'term': qualified_name(obj), 'source': 'explicit_token'}
            for obj in token_objs
        ]
    elif current_table and isinstance(current_table, dict) and any(qualified_name(current_table) == qualified_name(obj) for obj in objects):
        # Check if user query explicitly refers to another table by name
        explicit_other = False
        if table_terms and search_index:
            for t_term in table_terms:
                res = search_schema_index(search_index, t_term, limit=5)
                for r in res:
                    if r.get('kind') == 'table' and qualified_name(r.get('object') or {}) != qualified_name(current_table):
                        explicit_other = True
                        break
                if explicit_other:
                    break

        if not explicit_other:
            object_hits = [
                {'object': current_table, 'rank': 1, 'reason': '当前选中表', 'term': qualified_name(current_table), 'source': 'current_selection'}
            ]

    # If still no object hits, use schema_search to find tables from query / terms
    if not object_hits and search_index:
        search_terms = []
        if raw_query:
            search_terms.append(raw_query)
        search_terms.extend(table_terms)
        search_terms.extend(field_terms)

        seen_terms = set()
        for term in search_terms:
            t_clean = term.strip()
            if not t_clean or t_clean.lower() in seen_terms:
                continue
            seen_terms.add(t_clean.lower())
            results = search_schema_index(search_index, t_clean, limit=10)
            for r in results:
                if r.get('kind') == 'table' and isinstance(r.get('object'), dict):
                    rank = 2 if r.get('object_type') == 'TABLE' else 3
                    object_hits.append({
                        'object': r['object'],
                        'rank': rank,
                        'reason': f"搜索命中表：{r.get('title', '')}",
                        'term': t_clean,
                        'source': 'schema_search',
                    })

    if not object_hits and table_terms:
        if truncated:
            return {
                'state': 'BLOCKED',
                'reason': '快照已截断，请扩大扫描范围后重试。',
                'objects': [],
                'fields': [],
                'needs_selection': False,
                'auto_confirmed': False,
            }
        return {
            'state': 'BLOCKED',
            'reason': f'当前快照未找到“{table_terms[0]}”。可查看候选字段或修改描述。',
            'objects': [],
            'fields': [],
            'needs_selection': False,
            'auto_confirmed': False,
        }

    # Deduplicate candidate objects
    unique_objects = []
    seen_obj = set()
    for item in sorted(object_hits, key=lambda row: row['rank']):
        qn = qualified_name(item['object'])
        if qn in seen_obj:
            continue
        seen_obj.add(qn)
        unique_objects.append(item)

    # 2. Field resolution (Priority: Explicit Tokens > Current UI Fields > Schema Search / Synonyms)
    field_hits = []
    search_target_objects = [item['object'] for item in unique_objects] or objects

    ctx_fields = []
    for item in ((tokens or {}).get('selected_fields') or []):
        ctx_fields.append(str(item.get('qualified_name') or item.get('name') or ''))

    if ctx_fields:
        for obj in search_target_objects:
            for col in obj.get('columns') or []:
                qn = field_qualified(obj, col)
                if qn in ctx_fields or str(col.get('name') or '') in ctx_fields:
                    field_hits.append({
                        'object': obj,
                        'column': col,
                        'rank': 1,
                        'reason': '用户已选字段 Token',
                        'term': str(col.get('name') or ''),
                        'source': 'explicit_token',
                    })
    elif current_fields and isinstance(current_fields, list) and len(unique_objects) == 1 and unique_objects[0].get('source') == 'current_selection':
        for col in current_fields:
            if isinstance(col, dict):
                field_hits.append({
                    'object': unique_objects[0]['object'],
                    'column': col,
                    'rank': 1,
                    'reason': '当前选中字段',
                    'term': str(col.get('name') or ''),
                    'source': 'current_selection',
                })
    else:
        # Search fields using field_terms, raw query, schema_search, and synonyms
        matched_cols = set()
        if search_index:
            field_queries = []
            if raw_query:
                field_queries.append(raw_query)
            field_queries.extend(field_terms)
            field_queries.extend(table_terms)

            seen_f_queries = set()
            for f_q in field_queries:
                fq_clean = f_q.strip()
                if not fq_clean or fq_clean.lower() in seen_f_queries:
                    continue
                seen_f_queries.add(fq_clean.lower())

                # Check domain synonyms on target objects
                for obj in search_target_objects:
                    for col in obj.get('columns') or []:
                        if _synonym_hit(fq_clean, col):
                            col_key = field_qualified(obj, col)
                            if col_key not in matched_cols:
                                matched_cols.add(col_key)
                                field_hits.append({
                                    'object': obj,
                                    'column': col,
                                    'rank': 2,
                                    'reason': '同义词匹配字段',
                                    'term': fq_clean,
                                    'source': 'synonym',
                                })

                # Search schema_search index
                res = search_schema_index(search_index, fq_clean, limit=30)
                for r in res:
                    if r.get('kind') == 'field' and isinstance(r.get('object'), dict) and isinstance(r.get('column'), dict):
                        target_qns = {qualified_name(o) for o in search_target_objects} if unique_objects else None
                        obj_qn = qualified_name(r['object'])
                        if target_qns and obj_qn not in target_qns:
                            continue
                        col_key = field_qualified(r['object'], r['column'])
                        if col_key not in matched_cols:
                            matched_cols.add(col_key)
                            field_hits.append({
                                'object': r['object'],
                                'column': r['column'],
                                'rank': 3,
                                'reason': f"搜索命中字段：{r.get('title', '')}",
                                'term': fq_clean,
                                'source': 'schema_search',
                            })

    # Apply user confirmation filter if confirmed keys were supplied
    if confirmed_keys:
        field_hits = [
            item for item in field_hits
            if normalize_identifier(field_qualified(item['object'], item['column'])) in confirmed_keys
            or normalize_identifier(item['column'].get('name')) in confirmed_keys
        ]

    # Deduplicate fields
    field_hits.sort(key=lambda item: (item['rank'], str(item['column'].get('name') or '')))
    unique_fields = []
    seen_f = set()
    for item in field_hits:
        key = field_qualified(item['object'], item['column'])
        if key in seen_f:
            continue
        seen_f.add(key)
        unique_fields.append(item)

    # Check multi-table without join
    if len(unique_objects) > 1 and not token_objs and len(table_terms) > 1 and not intent.get('wants_join'):
        return {
            'state': 'BLOCKED',
            'reason': '请补充关联条件',
            'objects': unique_objects,
            'fields': unique_fields,
            'needs_selection': False,
            'auto_confirmed': False,
            'join_blocked': True,
        }

    table_names = {qualified_name(item['object']) for item in unique_objects}
    if len(table_names) > 1 and not intent.get('wants_join') and not token_objs and not confirmed_keys:
        return {
            'state': 'NEEDS_SELECTION',
            'reason': '找到多个匹配表或字段，请选择要使用的表和字段。',
            'objects': unique_objects,
            'fields': unique_fields,
            'needs_selection': True,
            'auto_confirmed': False,
        }

    # Ambiguity check: multiple candidate fields for the same search term or weak synonyms
    term_to_fields: dict = {}
    for item in unique_fields:
        t = str(item.get('term') or '').strip().lower()
        term_to_fields.setdefault(t, []).append(item)
    has_multi_match_per_term = any(len(cols) > 1 for cols in term_to_fields.values())

    if (has_multi_match_per_term or len(table_names) > 1) and not confirmed_keys and not ctx_fields:
        return {
            'state': 'NEEDS_SELECTION',
            'reason': '找到多个候选字段，请选择要使用的字段。',
            'objects': unique_objects,
            'fields': unique_fields,
            'needs_selection': True,
            'auto_confirmed': False,
        }

    if unique_objects or unique_fields:
        return {
            'state': 'READY',
            'reason': '',
            'objects': unique_objects,
            'fields': unique_fields,
            'needs_selection': False,
            'auto_confirmed': True,
        }

    return {
        'state': 'BLOCKED',
        'reason': '当前快照没有可确认的表或字段证据。',
        'objects': [],
        'fields': [],
        'needs_selection': False,
        'auto_confirmed': False,
    }


def build_evidence_context(resolution: dict, snapshot: dict | None) -> dict:
    snap = snapshot if isinstance(snapshot, dict) else {}
    resolution = resolution if isinstance(resolution, dict) else {}
    tables = []
    confirmed = []
    used_qns = set()
    field_items = list(resolution.get('fields') or [])
    object_items = list(resolution.get('objects') or [])
    if not object_items:
        object_items = [{'object': item['object']} for item in field_items]
    for obj_row in object_items:
        obj = obj_row.get('object') if isinstance(obj_row, dict) else None
        if not isinstance(obj, dict):
            continue
        qn = qualified_name(obj)
        if qn in used_qns:
            continue
        used_qns.add(qn)
        cols = []
        related = [item for item in field_items if qualified_name(item.get('object') or {}) == qn]
        source_cols = [item.get('column') for item in related if isinstance(item.get('column'), dict)]
        if not source_cols:
            source_cols = list(obj.get('columns') or [])
        for col in source_cols:
            cols.append({
                'name': str(col.get('name') or ''),
                'data_type': str(col.get('data_type') or ''),
                'comment': str(col.get('comment') or ''),
                'indexed': bool(col.get('indexed')),
                'primary_key': bool(col.get('primary_key')),
            })
            confirmed.append(field_qualified(obj, col) if related else field_qualified(obj, col))
        index_rows = []
        wanted = {str(col.get('name') or '').upper() for col in source_cols}
        for idx in obj.get('indexes') or []:
            names = [str(col.get('name') or '') for col in (idx.get('columns') or [])]
            if wanted and not ({item.upper() for item in names} & wanted):
                continue
            index_rows.append({'name': str(idx.get('name') or ''), 'columns': names})
        tables.append({
            'qualified_name': qn,
            'object_type': str(obj.get('object_type') or 'TABLE'),
            'comment': str(obj.get('comment') or ''),
            'index_metadata_status': str(obj.get('index_metadata_status') or snap.get('index_metadata_status') or ''),
            'columns': cols,
            'indexes': index_rows,
        })
    if field_items:
        confirmed = [field_qualified(item['object'], item['column']) for item in field_items]
    return {
        'dialect': str(snap.get('dialect') or 'oracle'),
        'snapshot_id': str(snap.get('snapshot_id') or ''),
        'scanned_at': str(snap.get('scanned_at') or ''),
        'truncated': bool(snap.get('truncated')),
        'version': _snapshot_version(snap),
        'tables': tables,
        'confirmed_fields': confirmed,
        'allow_join': False,
        'field_evidence': [
            {
                'qualified_name': field_qualified(item['object'], item['column']),
                'name': str(item['column'].get('name') or ''),
                'data_type': str(item['column'].get('data_type') or ''),
                'comment': str(item['column'].get('comment') or ''),
                'reason': str(item.get('reason') or ''),
                'index': _index_for_column(item['object'], item['column']),
            }
            for item in field_items
        ],
    }


def _index_for_column(obj: dict, col: dict) -> str:
    wanted = str(col.get('name') or '').upper()
    for idx in obj.get('indexes') or []:
        names = [str(item.get('name') or '').upper() for item in (idx.get('columns') or [])]
        if wanted in names:
            return str(idx.get('name') or '')
    return ''


def format_evidence_bar(evidence: dict | None) -> str:
    rows = []
    for item in (evidence or {}).get('field_evidence') or []:
        parts = [
            str(item.get('qualified_name') or item.get('name') or ''),
            str(item.get('data_type') or ''),
            str(item.get('comment') or ''),
            str(item.get('index') or ''),
        ]
        rows.append(' · '.join(part for part in parts if part))
    return '；'.join(rows)


def evidence_prompt_text(evidence: dict | None) -> str:
    data = evidence if isinstance(evidence, dict) else {}
    lines = [
        f"方言：{data.get('dialect') or 'oracle'}",
        f"snapshot_id：{data.get('snapshot_id') or ''}",
        f"扫描时间：{data.get('scanned_at') or ''}",
        '已确认字段：' + (', '.join(data.get('confirmed_fields') or []) or '（无）'),
    ]
    for table in data.get('tables') or []:
        cols = ', '.join(
            f"{col.get('name')} {col.get('data_type')} {col.get('comment')}".strip()
            for col in table.get('columns') or []
        )
        idx = ', '.join(
            f"{item.get('name')}({','.join(item.get('columns') or [])})"
            for item in table.get('indexes') or []
        )
        extra = f" 索引:{idx}" if idx else ''
        lines.append(f"- {table.get('qualified_name')} [{table.get('object_type')}] {table.get('comment')} :: {cols}{extra}")
    return '\n'.join(lines)


def _strip_literals(sql: str) -> str:
    text = strip_sql_comments(sql)
    text = re.sub(r"'(?:''|[^'])*'", ' ', text)
    text = re.sub(r'"(?:""|[^"])*"', ' ', text)
    return text


def _sql_objects(sql: str) -> list[str]:
    return [match.group(1) for match in _OBJ_RE.finditer(_strip_literals(sql))]


def _sql_aliases(sql: str) -> set[str]:
    aliases = set()
    for match in _ALIAS_RE.finditer(_strip_literals(sql)):
        name = match.group(1)
        if name.lower() in _SQL_KEYWORDS:
            continue
        aliases.add(name.upper())
    return aliases


def validate_generated_sql(sql: str, evidence: dict, dialect: str) -> dict:
    data = evidence if isinstance(evidence, dict) else {}
    text = str(sql or '').strip()
    result = {
        'allowed': False,
        'reason': '',
        'unknown_objects': [],
        'unknown_fields': [],
        'evidence_used': list(data.get('confirmed_fields') or []),
        'risk_level': 'unknown',
    }
    if not text:
        result['reason'] = '模型未返回 SQL。'
        return result
    parts = split_sql_statements(text)
    if len(parts) != 1:
        result['reason'] = '草案被拦截：模型返回了多条 SQL。'
        return result
    info = classify_statement(parts[0], dialect)
    if info.get('category') == 'unknown':
        result['reason'] = '草案被拦截：语句分类无法识别。'
        return result
    body = _strip_literals(parts[0])
    if re.search(r'\bjoin\b', body, re.I) and not data.get('allow_join'):
        result['reason'] = '草案被拦截：模型生成了未被确认的 Join 条件。'
        result['risk_level'] = 'unknown'
        return result
    evidence_objects = []
    evidence_fields = set()
    for table in data.get('tables') or []:
        qn = str(table.get('qualified_name') or '')
        name = qn.split('.')[-1] if qn else ''
        evidence_objects.extend([qn.upper(), name.upper()])
        evidence_objects.extend(part.upper() for part in qn.split('.') if part)
        for col in table.get('columns') or []:
            evidence_fields.add(str(col.get('name') or '').upper())
            evidence_fields.add(f"{name}.{col.get('name')}".upper())
            if qn:
                evidence_fields.add(f"{qn}.{col.get('name')}".upper())
    for item in data.get('confirmed_fields') or []:
        evidence_fields.add(str(item).upper())
        evidence_fields.add(str(item).split('.')[-1].upper())
    used_objects = _sql_objects(parts[0])
    unknown_objects = []
    for raw in used_objects:
        ident = raw.upper()
        short = ident.split('.')[-1]
        if ident not in evidence_objects and short not in evidence_objects:
            unknown_objects.append(raw)
    if unknown_objects:
        result['unknown_objects'] = unknown_objects
        result['reason'] = '草案引用了当前快照不存在的对象，已拦截且未写入 SQL 编辑器。'
        return result
    aliases = _sql_aliases(parts[0])
    unknown_fields = []
    for token in _IDENT_RE.findall(body):
        upper = token.upper()
        if token.lower() in _SQL_KEYWORDS:
            continue
        if upper in aliases:
            continue
        if upper in evidence_objects:
            continue
        if upper in evidence_fields:
            continue
        unknown_fields.append(token)
    if unknown_fields:
        result['unknown_fields'] = unknown_fields
        result['reason'] = f'草案引用了当前快照不存在的字段 {unknown_fields[0]}，已拦截且未写入 SQL 编辑器。'
        return result
    kind = str(dialect or data.get('dialect') or 'oracle').lower()
    lowered = body.lower()
    if kind in ('oracle', 'oceanbase') and re.search(r'\blimit\b', lowered) and 'fetch' not in lowered:
        result['reason'] = '草案被拦截：输出方言与当前连接方言不兼容。'
        return result
    if kind == 'mysql' and 'rownum' in lowered:
        result['reason'] = '草案被拦截：输出方言与当前连接方言不兼容。'
        return result
    safety = ai_draft_safety(parts[0], kind)
    if info.get('needs_confirm') or safety.get('fail_closed'):
        result['risk_level'] = 'write' if info.get('category') in ('dml', 'ddl') else 'unknown'
        if info.get('category') == 'ddl':
            result['risk_level'] = 'ddl'
        result['allowed'] = True
        result['reason'] = str(safety.get('reason') or '写入/结构变更草案，必须人工确认后才会执行')
        return result
    result.update(allowed=True, reason='', risk_level='read' if info.get('is_read') else 'unknown')
    return result


def prepare_request(
    question: str,
    snapshot: dict | None,
    connection: dict | None,
    *,
    tokens=None,
    current_table=None,
    current_fields=None,
    confirmed=None,
) -> dict:
    intent = extract_intent_terms(question)
    gate = snapshot_gate(connection, snapshot, wants_index=bool(intent.get('wants_index')))
    if not gate.get('ok'):
        return {
            'ok': False,
            'state': gate.get('state'),
            'reason': gate.get('reason'),
            'next_action': gate.get('next_action'),
            'intent': intent,
            'call_model': False,
        }
    resolution = resolve_candidates(
        snapshot,
        intent,
        tokens=tokens,
        current_table=current_table,
        current_fields=current_fields,
        confirmed=confirmed,
    )
    if resolution.get('state') == 'NEEDS_SELECTION':
        return {
            'ok': False,
            'state': 'NEEDS_SELECTION',
            'reason': resolution.get('reason'),
            'next_action': '选择字段',
            'intent': intent,
            'resolution': resolution,
            'call_model': False,
        }
    if resolution.get('state') != 'READY':
        return {
            'ok': False,
            'state': 'BLOCKED',
            'reason': resolution.get('reason') or '当前快照没有可确认的表或字段证据。',
            'next_action': '查看候选',
            'intent': intent,
            'resolution': resolution,
            'call_model': False,
        }
    evidence = build_evidence_context(resolution, snapshot)
    if not evidence.get('tables'):
        return {
            'ok': False,
            'state': 'BLOCKED',
            'reason': '当前快照没有可确认的表或字段证据。',
            'next_action': '选择表和字段',
            'intent': intent,
            'call_model': False,
        }
    return {
        'ok': True,
        'state': 'READY',
        'reason': gate.get('reason') or '',
        'intent': intent,
        'resolution': resolution,
        'evidence': evidence,
        'call_model': True,
    }
