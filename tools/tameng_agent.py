# -*- coding: utf-8 -*-
"""TamengAgent：仅基于当前有效 Schema 快照做字段证据链。不连库、不调模型、不使用 Qt。"""

from __future__ import annotations

import re

from tools.ai_object_context import field_qualified, qualified_name
from tools.schema_search import build_schema_search_index, get_cached_schema_search_index, search_schema_index
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

_SQL_FUNCTIONS = frozenset({
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'NVL', 'COALESCE', 'TO_CHAR', 'TO_DATE',
    'TRUNC', 'SUBSTR', 'UPPER', 'LOWER', 'CASE', 'DATE_FORMAT', 'IFNULL', 'CONCAT',
    'ROUND', 'LENGTH', 'NOW', 'SYSDATE', 'SYSTIMESTAMP', 'CURRENT_TIMESTAMP',
    'DATEDIFF', 'INSTR', 'TRIM', 'LPAD', 'RPAD', 'GREATEST', 'LEAST', 'ABS',
    'CEIL', 'FLOOR', 'CAST', 'SUBSTRING', 'MOD', 'NULLIF', 'ROW_NUMBER',
    'RANK', 'DENSE_RANK', 'LEAD', 'LAG', 'STDDEV', 'VARIANCE', 'WM_CONCAT',
    'GROUP_CONCAT', 'LISTAGG', 'EXTRACT', 'YEAR', 'MONTH', 'DAY', 'HOUR',
})

MAX_TABLES = 6
MAX_FIELDS_PER_TABLE = 12
MAX_TOTAL_FIELDS = 40
MAX_CONTEXT_CHARS = 12000

_CHINESE_STOPWORDS = frozenset({
    '查询', '查下', '查一下', '帮我', '给我', '请', '中', '的', '数据', '条数', '多少条',
    '数量', '总数', '合计', '统计', '求和', '平均', '倒序', '降序', '正序', '升序',
    '等于', '不等于', '大于等于', '小于等于', '大于', '小于', '索引', '关联', '联表',
    'join', '按', '一下', '有哪些', '所有', '全部', '包含', '条', '最近', '显示',
    '看下', '看一看', '检索', '获取', '找出', '列表', '信息', '内容', '字段', '表',
})

_COND_RE = re.compile(
    r'(?P<field>[A-Za-z_][\w$#]*|[\u4e00-\u9fff]{2,8})\s*'
    r'(?P<op>==|=|!=|<>|>=|<=|>|<|等于|不等于|大于等于|小于等于|大于|小于|是|为)\s*'
    r"['\"]?([^\s,，;；。()'\"\[\]]+)['\"]?"
)

_OP_MAP = {
    '等于': '=', '==': '=', '=': '=', '是': '=', '为': '=',
    '不等于': '!=', '<>': '!=', '!=': '!=',
    '大于等于': '>=', '>=': '>=',
    '小于等于': '<=', '<=': '<=',
    '大于': '>', '>': '>',
    '小于': '<', '<': '<',
}

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


def extract_schema_terms(question: str) -> dict:
    text = str(question or '').strip()
    lowered = text.lower()

    # 1. Condition hints extraction
    condition_hints = []
    val_tokens = set()
    for m in _COND_RE.finditer(text):
        f = m.group('field').strip()
        op_raw = m.group('op').strip()
        val = m.group(3).strip()
        op = _OP_MAP.get(op_raw, '=')
        if f.lower() not in _SQL_KEYWORDS and f not in _CHINESE_STOPWORDS:
            condition_hints.append({
                'field': f.upper() if re.match(r'^[A-Za-z_]', f) else f,
                'op': op,
                'val': val,
                'raw': m.group(0),
            })
            val_tokens.add(val.lower())

    # 2. Sorting, aggregation, index, join flags
    order = ''
    if any(mark in text for mark in ('倒序', '降序')) or re.search(r'\bdesc\b', lowered):
        order = 'DESC'
    elif any(mark in text for mark in ('正序', '升序')) or re.search(r'\basc\b', lowered):
        order = 'ASC'
    wants_index = any(mark in text for mark in ('索引', 'index'))
    wants_join = any(mark in text for mark in ('关联', '联表', 'join', '联合查询'))
    aggregate = bool(re.search(r'count|统计|合计|求和|平均|最大|最小|多少条|总数|数量', text, re.I))

    # 3. ASCII identifiers
    raw_idents = re.findall(r'[A-Za-z][A-Za-z0-9_$#]*', text)
    identifiers = []
    tables = []
    field_terms = []

    for match in raw_idents:
        m_lower = match.lower()
        if m_lower in _SQL_KEYWORDS or m_lower in ('desc', 'asc', 'order', 'by', 'select', 'from'):
            continue
        if m_lower in val_tokens:
            continue
        identifiers.append(match)
        tables.append(match)

    # 4. Chinese semantic terms & domain synonyms
    for key, variants in FIELD_SYNONYMS.items():
        if any(variant in lowered or variant in text for variant in variants):
            if key not in field_terms:
                field_terms.append(key)

    remainder = text
    for stop in _CHINESE_STOPWORDS:
        remainder = remainder.replace(stop, ' ')
    for ch_match in re.findall(r'[\u4e00-\u9fff]{2,8}', remainder):
        if ch_match not in field_terms and ch_match not in _CHINESE_STOPWORDS:
            field_terms.append(ch_match)

    for ch in field_terms:
        if ch not in tables:
            tables.append(ch)

    return {
        'raw': text,
        'identifiers': identifiers,
        'chinese_terms': field_terms,
        'condition_hints': condition_hints,
        'tables': tables,
        'field_terms': field_terms,
        'order': order,
        'wants_index': wants_index,
        'wants_join': wants_join,
        'aggregate': aggregate,
        'values': list(val_tokens),
    }


def extract_intent_terms(question: str) -> dict:
    return extract_schema_terms(question)


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


def rank_schema_candidates(
    terms: dict,
    snapshot: dict | None,
    *,
    tokens: dict | None = None,
    current_table: dict | None = None,
    current_fields: list | None = None,
    connection: dict | None = None,
) -> dict:
    objects = _objects(snapshot)
    if not objects:
        return {'ranked_tables': [], 'ranked_fields': []}

    search_index = get_cached_schema_search_index(snapshot)
    raw_query = str(terms.get('raw') or '').strip()
    identifiers = [str(x).upper() for x in (terms.get('identifiers') or [])]
    chinese_terms = [str(x) for x in (terms.get('chinese_terms') or [])]
    condition_fields = {str(h.get('field') or '').upper() for h in (terms.get('condition_hints') or [])}

    # Pre-parse token hints
    token_obj_qns = set()
    token_field_qns = set()
    if isinstance(tokens, dict):
        for item in (tokens.get('selected_objects') or []):
            token_obj_qns.add(normalize_identifier(item.get('qualified_name') or item.get('name')))
        for item in (tokens.get('selected_fields') or []):
            token_field_qns.add(normalize_identifier(item.get('object_qualified_name') or ''))
            token_field_qns.add(normalize_identifier(item.get('qualified_name') or item.get('name')))

    curr_table_qn = normalize_identifier(qualified_name(current_table)) if isinstance(current_table, dict) else ''
    curr_field_names = {normalize_identifier(c.get('name')) for c in (current_fields or []) if isinstance(c, dict)}

    ranked_tables = []
    for obj in objects:
        qn = qualified_name(obj)
        norm_qn = normalize_identifier(qn)
        table_name = str(obj.get('name') or '').upper()
        norm_name = normalize_identifier(table_name)
        owner = str(obj.get('owner') or '').upper()
        comment = str(obj.get('comment') or '')
        columns = obj.get('columns') or []
        col_name_set = {str(c.get('name') or '').upper(): c for c in columns if isinstance(c, dict)}

        score = 0
        reasons = []

        # 1. Match table name (exact 95-100, fuzzy 80)
        for ident in identifiers:
            if ident in (norm_name, norm_qn):
                score += 100 if ident == norm_qn else 95
                reasons.append(f"精确匹配表名: {ident}")
            elif ident in norm_name and len(ident) >= 3:
                score += 80
                reasons.append(f"模糊匹配表名: {ident}")

        # 2. Match table comment
        for ch in chinese_terms:
            if ch in comment:
                score += 75
                reasons.append(f"注释语义匹配表: {comment}")

        # 3. Match columns in this table
        for ident in identifiers:
            if ident in col_name_set:
                score += 85 if score == 0 else 20
                reasons.append(f"包含匹配字段名: {ident}")
        for ch in chinese_terms:
            if any(ch in str(c.get('comment') or '') or _synonym_hit(ch, c) for c in columns):
                score += 70 if score == 0 else 15
                reasons.append(f"包含匹配注释/同义词字段: {ch}")

        # 4. Token hints boost (+30)
        if norm_qn in token_obj_qns or norm_name in token_obj_qns:
            score += 30
            reasons.append("用户 Token 提示表 (+30)")
        if norm_qn in token_field_qns:
            score += 30
            reasons.append("用户 Token 提示字段所在表 (+30)")

        # 5. Tree selection boost (+10)
        if curr_table_qn and norm_qn == curr_table_qn:
            score += 10
            reasons.append("当前树选中表 (+10)")

        # 6. Schema search fallback if score is 0
        if score == 0 and search_index:
            for s_term in [raw_query, *identifiers, *chinese_terms]:
                if not s_term.strip():
                    continue
                res = search_schema_index(search_index, s_term, limit=5)
                for r in res:
                    if r.get('kind') == 'table' and qualified_name(r.get('object') or {}) == qn:
                        score += 60
                        reasons.append(f"搜索索引命中表: {r.get('title', '')}")
                        break
                if score > 0:
                    break

        if score > 0:
            ranked_tables.append({
                'object': obj,
                'score': score,
                'reasons': reasons,
                'qualified_name': qn,
                'name': table_name,
                'owner': owner,
            })

    ranked_tables.sort(key=lambda x: x['score'], reverse=True)

    # Rank fields for candidate tables
    target_tables = [item['object'] for item in ranked_tables[:MAX_TABLES]] if ranked_tables else objects
    ranked_fields = []
    for obj in target_tables:
        obj_qn = qualified_name(obj)
        for col in (obj.get('columns') or []):
            col_name = str(col.get('name') or '').upper()
            col_comment = str(col.get('comment') or '')
            fqn = field_qualified(obj, col)
            norm_fqn = normalize_identifier(fqn)
            norm_cname = normalize_identifier(col_name)

            col_score = 0
            col_reasons = []

            # 1. Exact identifier match (95)
            if col_name in identifiers or norm_cname in [normalize_identifier(i) for i in identifiers]:
                col_score += 95
                col_reasons.append(f"精确匹配字段名: {col_name}")

            # 2. Condition hint match (90)
            if col_name in condition_fields or any(str(h.get('field') or '').upper() in (col_name, normalize_text(col_comment).upper()) for h in (terms.get('condition_hints') or [])):
                col_score += 90
                col_reasons.append("条件约束字段")

            # 3. Synonym match (80)
            for ch in chinese_terms:
                if _synonym_hit(ch, col):
                    col_score += 80
                    col_reasons.append(f"同义词匹配: {ch}")

            # 4. Comment semantic match (75)
            for ch in chinese_terms:
                if ch in col_comment:
                    col_score += 75
                    col_reasons.append(f"注释语义匹配: {ch}")

            # 5. Token boost (+30)
            if norm_fqn in token_field_qns or norm_cname in token_field_qns:
                col_score += 30
                col_reasons.append("用户 Token 提示字段 (+30)")

            # 6. Current field selection boost (+10)
            if norm_cname in curr_field_names:
                col_score += 10
                col_reasons.append("当前选中字段 (+10)")

            # 7. Schema search fallback
            if col_score == 0 and search_index:
                for s_term in [raw_query, *identifiers, *chinese_terms]:
                    if not s_term.strip():
                        continue
                    res = search_schema_index(search_index, s_term, limit=10)
                    for r in res:
                        if (r.get('kind') == 'field' and
                            qualified_name(r.get('object') or {}) == obj_qn and
                            str((r.get('column') or {}).get('name') or '').upper() == col_name):
                            col_score += 65
                            col_reasons.append(f"搜索索引命中字段: {r.get('title', '')}")
                            break
                    if col_score > 0:
                        break

            if col_score > 0:
                ranked_fields.append({
                    'object': obj,
                    'column': col,
                    'score': col_score,
                    'reasons': col_reasons,
                    'qualified_name': fqn,
                    'name': col_name,
                    'term': next((ch for ch in chinese_terms if ch in col_comment or _synonym_hit(ch, col)), col_name),
                })

    ranked_fields.sort(key=lambda x: x['score'], reverse=True)

    return {
        'ranked_tables': ranked_tables,
        'ranked_fields': ranked_fields,
    }


def assess_schema_ambiguity(
    ranked_tables: list,
    ranked_fields: list,
    connection: dict | None = None,
    *,
    confirmed: list | None = None,
    tokens: dict | None = None,
) -> dict:
    confirmed_keys = {normalize_identifier(item) for item in (confirmed or []) if str(item).strip()}
    if confirmed_keys:
        return {'ambiguous': False, 'reason': '', 'disambiguated_tables': ranked_tables}

    has_tokens = bool((tokens or {}).get('selected_objects') or (tokens or {}).get('selected_fields'))
    if has_tokens:
        return {'ambiguous': False, 'reason': '', 'disambiguated_tables': ranked_tables}

    # Cross-schema identical table names
    if len(ranked_tables) > 1:
        top_score = ranked_tables[0]['score']
        top_tables = [t for t in ranked_tables if t['score'] >= top_score - 10]
        names = {t['name'] for t in top_tables}
        if len(names) == 1 and len(top_tables) > 1:
            conn_schema = str((connection or {}).get('schema') or (connection or {}).get('username') or '').strip().upper()
            if conn_schema:
                matching = [t for t in top_tables if t['owner'].upper() == conn_schema]
                if matching:
                    disambiguated = matching + [t for t in ranked_tables if t not in top_tables]
                    return {'ambiguous': False, 'reason': '', 'disambiguated_tables': disambiguated}
            return {
                'ambiguous': True,
                'state': 'NEEDS_SELECTION',
                'reason': f"存在多个同名表“{next(iter(names))}”（不同 Schema），请指定 Schema 或选择表。",
                'tables': top_tables,
                'fields': ranked_fields,
            }

    # Field ambiguity within same search term in top table
    if ranked_fields:
        high_fields = [f for f in ranked_fields if f['score'] >= 75]
        term_groups: dict = {}
        for f in high_fields:
            t = str(f.get('term') or '').strip().lower()
            if t:
                term_groups.setdefault(t, []).append(f)
        for t, items in term_groups.items():
            distinct_names = {item['name'] for item in items}
            if len(distinct_names) > 1:
                return {
                    'ambiguous': True,
                    'state': 'NEEDS_SELECTION',
                    'reason': '找到多个候选字段，请选择要使用的字段。',
                    'fields': items,
                }

    # Cross-table field ambiguity when user only searched field name (e.g. '查询保单号')
    if len(ranked_tables) > 1 and ranked_fields:
        top_t_score = ranked_tables[0]['score']
        if top_t_score < 90:
            tables_with_field = {qualified_name(f['object']) for f in ranked_fields if f['score'] >= 75}
            if len(tables_with_field) > 1:
                return {
                    'ambiguous': True,
                    'state': 'NEEDS_SELECTION',
                    'reason': '找到多个匹配表或字段，请选择要使用的表和字段。',
                    'fields': ranked_fields,
                }

    return {'ambiguous': False, 'reason': '', 'disambiguated_tables': ranked_tables}


def build_retrieved_evidence(
    ranked_tables: list,
    ranked_fields: list,
    snapshot: dict | None,
    terms: dict,
    connection: dict | None = None,
    *,
    confirmed: list | None = None,
) -> dict:
    snap = snapshot if isinstance(snapshot, dict) else {}
    conn = connection if isinstance(connection, dict) else {}

    confirmed_keys = {normalize_identifier(item) for item in (confirmed or []) if str(item).strip()}

    selected_table_items = ranked_tables[:MAX_TABLES]
    evidence_tables = []
    all_confirmed_fields = []
    field_evidence_list = []
    total_fields_budget = MAX_TOTAL_FIELDS

    for t_item in selected_table_items:
        obj = t_item['object']
        qn = qualified_name(obj)
        columns = list(obj.get('columns') or [])
        indexes = list(obj.get('indexes') or [])

        t_fields = [f for f in ranked_fields if qualified_name(f['object']) == qn]

        ordered_cols = []
        for f in t_fields:
            col = f['column']
            if col not in ordered_cols:
                ordered_cols.append(col)
        for c in columns:
            if c.get('primary_key') and c not in ordered_cols:
                ordered_cols.append(c)
        for c in columns:
            if c.get('indexed') and c not in ordered_cols:
                ordered_cols.append(c)
        for c in columns:
            if c not in ordered_cols:
                ordered_cols.append(c)

        limit = min(MAX_FIELDS_PER_TABLE, total_fields_budget)
        chosen_cols = ordered_cols[:limit]
        total_fields_budget = max(0, total_fields_budget - len(chosen_cols))

        cols_meta = []
        for col in chosen_cols:
            c_name = str(col.get('name') or '')
            fqn = field_qualified(obj, col)
            is_confirmed = (
                not confirmed_keys or
                normalize_identifier(fqn) in confirmed_keys or
                normalize_identifier(c_name) in confirmed_keys
            )
            cols_meta.append({
                'name': c_name,
                'data_type': str(col.get('data_type') or ''),
                'comment': str(col.get('comment') or '')[:100],
                'indexed': bool(col.get('indexed')),
                'primary_key': bool(col.get('primary_key')),
            })
            if is_confirmed:
                all_confirmed_fields.append(fqn)

        chosen_names = {c['name'].upper() for c in cols_meta}
        idx_meta = []
        for idx in indexes:
            idx_cols = [str(c) if isinstance(c, str) else str(c.get('name') or '') for c in (idx.get('columns') or [])]
            if chosen_names and not ({ic.upper() for ic in idx_cols} & chosen_names):
                continue
            idx_meta.append({'name': str(idx.get('name') or ''), 'columns': idx_cols})

        evidence_tables.append({
            'qualified_name': qn,
            'object_type': str(obj.get('object_type') or 'TABLE'),
            'comment': str(obj.get('comment') or '')[:200],
            'index_metadata_status': str(obj.get('index_metadata_status') or snap.get('index_metadata_status') or ''),
            'columns': cols_meta,
            'indexes': idx_meta,
        })

    for f in ranked_fields:
        obj = f['object']
        col = f['column']
        fqn = field_qualified(obj, col)
        if confirmed_keys and normalize_identifier(fqn) not in confirmed_keys and normalize_identifier(col.get('name')) not in confirmed_keys:
            continue
        field_evidence_list.append({
            'qualified_name': fqn,
            'name': str(col.get('name') or ''),
            'data_type': str(col.get('data_type') or ''),
            'comment': str(col.get('comment') or '')[:100],
            'reason': '；'.join(f['reasons']),
            'index': _index_for_column(obj, col),
        })

    base_dialect = str(conn.get('dialect') or snap.get('dialect') or 'oracle')
    ob_mode = ''
    if base_dialect.lower() == 'oceanbase':
        from tools.db_contracts import normalize_oceanbase_mode
        ob_mode = normalize_oceanbase_mode(conn.get('mode') or snap.get('oceanbase_mode'))

    top_t_score = ranked_tables[0]['score'] if ranked_tables else 0
    top_f_score = ranked_fields[0]['score'] if ranked_fields else 0
    if top_t_score >= 95 and (top_f_score >= 85 or not terms.get('identifiers') and not terms.get('chinese_terms')):
        confidence = 'high'
    elif top_t_score >= 95:
        confidence = 'high'
    elif top_t_score >= 70:
        confidence = 'medium'
    else:
        confidence = 'low'

    summary_parts = ['自动匹配', '高置信' if confidence == 'high' else '候选']
    if selected_table_items:
        summary_parts.append(selected_table_items[0]['qualified_name'])
    if ranked_fields:
        summary_parts.append(ranked_fields[0]['name'])
    retrieval_summary = ' · '.join(summary_parts)

    evidence = {
        'dialect': base_dialect,
        'oceanbase_mode': ob_mode,
        'effective_dialect': get_effective_sql_dialect(base_dialect, ob_mode),
        'snapshot_id': str(snap.get('snapshot_id') or ''),
        'scanned_at': str(snap.get('scanned_at') or ''),
        'truncated': bool(snap.get('truncated')),
        'version': _snapshot_version(snap),
        'tables': evidence_tables,
        'confirmed_fields': all_confirmed_fields,
        'allow_join': bool(terms.get('wants_join')),
        'field_evidence': field_evidence_list,
        'condition_hints': [
            {**h, 'val': (str(h.get('val') or '')[:120] + '...') if len(str(h.get('val') or '')) > 120 else str(h.get('val') or '')}
            for h in (terms.get('condition_hints') or [])
        ],
        'retrieval_confidence': confidence,
        'retrieval_summary': retrieval_summary,
    }

    # Hard cap invariant: ensure prompt text never exceeds MAX_CONTEXT_CHARS
    while len(evidence_prompt_text(evidence)) > MAX_CONTEXT_CHARS and evidence_tables:
        if len(evidence_tables) > 1:
            evidence_tables.pop()
            continue
        single_tbl = evidence_tables[0]
        cols = single_tbl.get('columns') or []
        if len(cols) > 1:
            cols.pop()
            continue
        c = single_tbl.get('comment') or ''
        if len(c) > 20:
            single_tbl['comment'] = c[:20]
            continue
        break
    assert len(evidence_prompt_text(evidence)) <= MAX_CONTEXT_CHARS
    return evidence


def resolve_candidates(
    snapshot: dict | None,
    intent: dict,
    *,
    tokens=None,
    current_table=None,
    current_fields=None,
    confirmed=None,
    connection=None,
) -> dict:
    intent = intent if isinstance(intent, dict) else extract_intent_terms('')
    ranked = rank_schema_candidates(
        intent, snapshot,
        tokens=tokens, current_table=current_table,
        current_fields=current_fields, connection=connection,
    )
    ranked_tables = ranked['ranked_tables']
    ranked_fields = ranked['ranked_fields']

    if not ranked_tables:
        truncated = bool((snapshot or {}).get('truncated'))
        if truncated:
            return {
                'state': 'BLOCKED',
                'reason': '快照已截断，请扩大扫描范围后重试。',
                'objects': [],
                'fields': [],
                'needs_selection': False,
                'auto_confirmed': False,
            }
        table_terms = [str(item) for item in (intent.get('tables') or []) if str(item).strip()]
        missing_name = table_terms[0] if table_terms else '指定对象'
        return {
            'state': 'BLOCKED',
            'reason': f'当前快照未找到“{missing_name}”。可查看候选字段或修改描述。',
            'objects': [],
            'fields': [],
            'needs_selection': False,
            'auto_confirmed': False,
        }

    token_objs = _token_objects(snapshot, tokens)
    confirmed_keys = {normalize_identifier(item) for item in (confirmed or []) if str(item).strip()}
    table_terms = [str(item) for item in (intent.get('tables') or []) if str(item).strip()]

    explicit_tables = [
        t for t in ranked_tables
        if any(t['name'].upper() == term.upper() or t['qualified_name'].upper() == term.upper() for term in table_terms)
    ]
    if len(explicit_tables) > 1 and not token_objs and len(table_terms) > 1 and not intent.get('wants_join') and not confirmed_keys:
        return {
            'state': 'BLOCKED',
            'reason': '请补充关联条件',
            'objects': ranked_tables,
            'fields': ranked_fields,
            'needs_selection': False,
            'auto_confirmed': False,
            'join_blocked': True,
        }

    # Object priority resolution:
    # 1. Explicit tokens
    if token_objs:
        token_qns = {qualified_name(to) for to in token_objs}
        disambiguated_tables = [t for t in ranked_tables if t['qualified_name'] in token_qns]
        if not disambiguated_tables:
            disambiguated_tables = [
                {'object': to, 'score': 100, 'reasons': ['用户指定 Token'], 'qualified_name': qualified_name(to), 'name': str(to.get('name') or ''), 'owner': str(to.get('owner') or '')}
                for to in token_objs
            ]
    # 2. Explicit NL table mention
    elif explicit_tables:
        if not intent.get('wants_join') and len(explicit_tables) == 1:
            disambiguated_tables = explicit_tables[:1]
        else:
            disambiguated_tables = explicit_tables
    else:
        # current_table is purely an optional ranking hint (+10 in rank_schema_candidates), not a hard selector.
        if not intent.get('wants_join') and len(ranked_tables) > 1:
            top_score = ranked_tables[0]['score']
            second_score = ranked_tables[1]['score']
            if top_score > second_score and top_score >= 70:
                disambiguated_tables = [ranked_tables[0]]
            else:
                disambiguated_tables = ranked_tables
        else:
            disambiguated_tables = ranked_tables

    ambiguity = assess_schema_ambiguity(
        disambiguated_tables,
        ranked_fields,
        connection=connection,
        confirmed=confirmed,
        tokens=tokens,
    )
    if ambiguity.get('ambiguous'):
        return {
            'state': 'NEEDS_SELECTION',
            'reason': ambiguity.get('reason') or '找到多个候选字段，请选择要使用的字段。',
            'objects': ambiguity.get('tables') or disambiguated_tables,
            'fields': ambiguity.get('fields') or ranked_fields,
            'needs_selection': True,
            'auto_confirmed': False,
        }

    final_tables = ambiguity.get('disambiguated_tables') or disambiguated_tables
    return {
        'state': 'READY',
        'reason': '',
        'objects': final_tables,
        'fields': ranked_fields,
        'needs_selection': False,
        'auto_confirmed': True,
    }


def get_effective_sql_dialect(dialect: str, oceanbase_mode: str = '') -> str:
    d = str(dialect or '').strip().lower()
    if d == 'oceanbase':
        from tools.db_contracts import normalize_oceanbase_mode
        mode = normalize_oceanbase_mode(oceanbase_mode)
        return 'mysql' if mode == 'mysql' else 'oracle'
    return d or 'oracle'


def build_evidence_context(
    resolution: dict,
    snapshot: dict | None,
    *,
    dialect: str = '',
    oceanbase_mode: str = '',
) -> dict:
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
            names = [str(col) if isinstance(col, str) else str(col.get('name') or '') for col in (idx.get('columns') or [])]
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
    base_d = dialect or str(snap.get('dialect') or 'oracle')
    ob_m = str(oceanbase_mode or snap.get('oceanbase_mode') or '')
    return {
        'dialect': base_d,
        'oceanbase_mode': ob_m,
        'effective_dialect': get_effective_sql_dialect(base_d, ob_m),
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
        names = [str(item if isinstance(item, str) else item.get('name') or '').upper() for item in (idx.get('columns') or [])]
        if wanted in names:
            return str(idx.get('name') or '')
    return ''


def format_evidence_bar(evidence: dict | None) -> str:
    data = evidence if isinstance(evidence, dict) else {}
    rows = []
    for item in data.get('field_evidence') or []:
        parts = [
            str(item.get('qualified_name') or item.get('name') or ''),
            str(item.get('data_type') or ''),
            str(item.get('comment') or ''),
            str(item.get('index') or ''),
        ]
        rows.append(' · '.join(part for part in parts if part))
    detail = '；'.join(rows)
    prefix = str(data.get('retrieval_summary') or '').strip()
    if prefix and detail:
        return f"{prefix} ｜ {detail}"
    return prefix or detail


def format_condition_hint(hint: dict, tables: list | None = None) -> str:
    field = str(hint.get('field') or '').strip()
    op = str(hint.get('op') or '=').strip()
    val = str(hint.get('val') or '').strip()
    field_upper = field.upper()
    data_type = ''
    for t in tables or []:
        for col in (t.get('columns') or []):
            c_name = str(col.get('name') or '').upper()
            if c_name == field_upper or field_upper.endswith('.' + c_name):
                data_type = str(col.get('data_type') or '').upper()
                break
        if data_type:
            break

    is_numeric_type = any(t in data_type for t in ('INT', 'NUMBER', 'DECIMAL', 'FLOAT', 'DOUBLE', 'NUMERIC'))
    is_char_or_date = any(t in data_type for t in ('CHAR', 'VARCHAR', 'TEXT', 'DATE', 'TIME', 'CLOB'))

    val_clean = val.strip("'\"")

    # Datatype takes absolute priority:
    if is_numeric_type:
        if val_clean.isdigit():
            val_str = str(int(val_clean))
        elif re.match(r'^-?\d+(\.\d+)?$', val_clean):
            val_str = val_clean
        else:
            val_str = val_clean
    elif is_char_or_date:
        escaped = val_clean.replace("'", "''")
        val_str = f"'{escaped}'"
    else:
        # unknown datatype: conservative quoted
        escaped = val_clean.replace("'", "''")
        val_str = f"'{escaped}'"
    return f"{field} {op} {val_str}"


def _format_condition_hint(hint: dict, tables: list | None = None) -> str:
    return format_condition_hint(hint, tables)


def evidence_prompt_text(evidence: dict | None, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    data = evidence if isinstance(evidence, dict) else {}
    base_dialect = str(data.get('dialect') or 'oracle')
    ob_m = str(data.get('oceanbase_mode') or '')
    if base_dialect.lower() == 'oceanbase':
        from tools.db_contracts import normalize_oceanbase_mode
        eff_mode = normalize_oceanbase_mode(ob_m)
        mode_label = 'MySQL 兼容模式' if eff_mode == 'mysql' else 'Oracle 兼容模式'
        dialect_str = f"OceanBase ({mode_label})"
    else:
        dialect_str = base_dialect

    tables = list(data.get('tables') or [])
    hint_strs = []
    for h in (data.get('condition_hints') or []):
        hint_copy = dict(h)
        v = str(hint_copy.get('val') or '')
        if len(v) > 120:
            hint_copy['val'] = v[:120] + '...'
        hint_strs.append(format_condition_hint(hint_copy, tables))

    confirmed_fields = list(data.get('confirmed_fields') or [])

    def _render(
        tbls,
        hints=None,
        conf_fields=None,
        inc_idx=True,
        inc_col_cmt=True,
        inc_tbl_cmt=True,
        max_col_cmt=100,
        max_tbl_cmt=200,
    ) -> str:
        cur_conf = confirmed_fields if conf_fields is None else conf_fields
        lines = [
            f"方言：{dialect_str}",
            f"snapshot_id：{data.get('snapshot_id') or ''}",
            f"扫描时间：{data.get('scanned_at') or ''}",
            'Schema Evidence 字段：' + (', '.join(cur_conf) or '（无）'),
        ]
        cur_hints = hint_strs if hints is None else hints
        if cur_hints:
            lines.append('条件提示：' + ', '.join(cur_hints))
        for table in tbls:
            cols_parts = []
            for col in (table.get('columns') or []):
                name = str(col.get('name') or '')
                dtype = str(col.get('data_type') or '')
                cmt = str(col.get('comment') or '') if inc_col_cmt else ''
                if cmt and len(cmt) > max_col_cmt:
                    cmt = cmt[:max_col_cmt]
                cols_parts.append(f"{name} {dtype} {cmt}".strip())
            cols = ', '.join(cols_parts)

            extra = ''
            if inc_idx:
                idx = ', '.join(
                    f"{item.get('name')}({','.join(item.get('columns') or [])})"
                    for item in (table.get('indexes') or [])
                )
                if idx:
                    extra = f" 索引:{idx}"
            t_cmt = str(table.get('comment') or '') if inc_tbl_cmt else ''
            if t_cmt and len(t_cmt) > max_tbl_cmt:
                t_cmt = t_cmt[:max_tbl_cmt]
            lines.append(f"- {table.get('qualified_name')} [{table.get('object_type')}] {t_cmt} :: {cols}{extra}".strip())
        return '\n'.join(lines)

    text = _render(tables)
    if len(text) <= max_chars:
        return text

    # Step 1: drop indexes
    text = _render(tables, inc_idx=False)
    if len(text) <= max_chars:
        return text

    # Step 2: shorten comments
    text = _render(tables, inc_idx=False, max_col_cmt=30, max_tbl_cmt=50)
    if len(text) <= max_chars:
        return text

    # Step 3: drop comments completely
    text = _render(tables, inc_idx=False, inc_col_cmt=False, inc_tbl_cmt=False)
    if len(text) <= max_chars:
        return text

    # Step 4: trim secondary tables (keep top table)
    curr_tbls = [dict(t) for t in tables]
    while len(curr_tbls) > 1 and len(text) > max_chars:
        curr_tbls.pop()
        text = _render(curr_tbls, inc_idx=False, inc_col_cmt=False, inc_tbl_cmt=False)
    if len(text) <= max_chars:
        return text

    # Step 5: trim secondary columns in top table (keep at least 1 column, preserving confirmed)
    if curr_tbls:
        t0 = dict(curr_tbls[0])
        cols = list(t0.get('columns') or [])
        conf_names = {c.split('.')[-1].upper() for c in confirmed_fields}
        while len(cols) > 1 and len(text) > max_chars:
            drop_idx = -1
            for idx in range(len(cols) - 1, -1, -1):
                if str(cols[idx].get('name') or '').upper() not in conf_names:
                    drop_idx = idx
                    break
            if drop_idx >= 0:
                cols.pop(drop_idx)
            else:
                cols.pop()
            t0['columns'] = cols
            text = _render([t0], inc_idx=False, inc_col_cmt=False, inc_tbl_cmt=False)
        curr_tbls = [t0]
    if len(text) <= max_chars:
        return text

    # Step 6: trim secondary condition hints
    curr_hints = list(hint_strs)
    while curr_hints and len(text) > max_chars:
        curr_hints.pop()
        text = _render(curr_tbls, hints=curr_hints, inc_idx=False, inc_col_cmt=False, inc_tbl_cmt=False)
    if len(text) <= max_chars:
        return text

    # Step 7: trim secondary confirmed fields
    curr_conf = list(confirmed_fields)
    while len(curr_conf) > 1 and len(text) > max_chars:
        curr_conf.pop()
        text = _render(curr_tbls, hints=curr_hints, conf_fields=curr_conf, inc_idx=False, inc_col_cmt=False, inc_tbl_cmt=False)
    if len(text) <= max_chars:
        return text

    # Step 8: Absolute hard fallback
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def bounded_evidence_prompt_text(evidence: dict | None, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    return evidence_prompt_text(evidence, max_chars=max_chars)


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


def validate_generated_sql(sql: str, evidence: dict, dialect: str = '', oceanbase_mode: str = '') -> dict:
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
    base_dialect = str(dialect or data.get('dialect') or 'oracle').lower()
    ob_m = str(oceanbase_mode or data.get('oceanbase_mode') or '')
    kind = get_effective_sql_dialect(base_dialect, ob_m)

    info = classify_statement(parts[0], kind)
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
        if upper in _SQL_FUNCTIONS:
            continue
        unknown_fields.append(token)
    if unknown_fields:
        result['unknown_fields'] = unknown_fields
        result['reason'] = f'草案引用了当前快照不存在的字段或函数 {unknown_fields[0]}，已拦截且未写入 SQL 编辑器。'
        return result
    lowered = body.lower()
    if kind in ('oracle', 'dameng', 'dm') and re.search(r'\blimit\b', lowered) and 'fetch' not in lowered:
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


def retrieve_schema_context(
    question: str,
    snapshot: dict | None,
    connection: dict | None = None,
    *,
    tokens=None,
    current_table=None,
    current_fields=None,
    confirmed=None,
) -> dict:
    intent = extract_schema_terms(question)
    conn_dialect = str((connection or {}).get('dialect') or (snapshot or {}).get('dialect') or '').strip().lower()
    if conn_dialect in ('redis', 'mongo', 'mongodb'):
        return {
            'ok': False,
            'state': 'NOSQL_NOT_SUPPORTED',
            'reason': f'{conn_dialect.upper()} 非关系型数据库，不进入关系型 Schema 自动检索与 SQL 生成流程。',
            'next_action': '使用对应 NoSQL 语法或切换连接',
            'intent': intent,
            'call_model': False,
        }
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
        connection=connection,
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
            'state': resolution.get('state') or 'BLOCKED',
            'reason': resolution.get('reason') or '当前快照没有可确认的表或字段证据。',
            'next_action': '查看候选',
            'intent': intent,
            'resolution': resolution,
            'call_model': False,
        }

    ranked_tables = resolution.get('objects') or []
    ranked_fields = resolution.get('fields') or []
    evidence = build_retrieved_evidence(
        ranked_tables,
        ranked_fields,
        snapshot,
        intent,
        connection=connection,
        confirmed=confirmed,
    )
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
    return retrieve_schema_context(
        question,
        snapshot,
        connection,
        tokens=tokens,
        current_table=current_table,
        current_fields=current_fields,
        confirmed=confirmed,
    )
