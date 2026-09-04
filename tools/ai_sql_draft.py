# -*- coding: utf-8 -*-
"""内网模型 SQL 草案：结构化说明 + SQL，绝不执行数据库。"""

from __future__ import annotations

import json
import re

from tools.ai_harness import strip_markdown_fence
from tools.intranet_llm import IntranetLlmError, chat_completions, is_enabled, load_ai_local
from tools.schema_snapshot import clip_snapshot_for_prompt
from tools.sql_guard import ai_draft_safety, classify_statement
from tools.tameng_agent import evidence_prompt_text

_SQL_KEYWORDS = frozenset({
    'select', 'from', 'where', 'and', 'or', 'not', 'in', 'is', 'null', 'as',
    'join', 'left', 'right', 'inner', 'outer', 'on', 'group', 'by', 'order',
    'asc', 'desc', 'having', 'union', 'all', 'distinct', 'count', 'sum',
    'avg', 'min', 'max', 'case', 'when', 'then', 'else', 'end', 'insert',
    'into', 'values', 'update', 'set', 'delete', 'create', 'alter', 'drop',
    'table', 'view', 'index', 'with', 'exists', 'like', 'between', 'limit',
    'offset', 'dual', 'rownum', 'fetch', 'first', 'rows', 'only', 'scan',
    'get', 'match', 'hgetall', 'collection', 'filter', 'true', 'false',
})

ACTIONS = {
    'generate': '根据问题生成',
    'explain': '解释当前 SQL',
    'optimize': '优化当前 SQL',
    'fix': '修复报错',
}

_SYSTEM = (
    '你是内网数据库 SQL 助手。只返回一个 JSON 对象，不要 Markdown 围栏，不要隐藏思维链，'
    '不要输出 host、端口、用户名、密码、Token 或任何行数据。'
    '字段：summary, intent, objects_used, selected_fields, evidence, condition_interpretation, '
    'join_assumptions, risk_level, warnings, sql。'
    'risk_level 只能是 read、write、ddl、unknown。sql 必须是单条草案，禁止用分号拼多条。'
    '只能使用用户消息中已确认的真实表和字段，禁止猜测不存在的字段名。'
    '多表且关联不明确时，join_assumptions 必须给出假设列表，risk_level 不得视为 read，'
    'warnings 必须包含「需人工补充 Join 条件」。不要隐藏思维链。'
)


def _extract_json(text: str) -> dict:
    raw = strip_markdown_fence(str(text or ''))
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    match = re.search(r'\{[\s\S]*\}', raw)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {'summary': raw[:800], 'sql': raw, 'warnings': ['模型未返回标准 JSON，已尽量提取 SQL']}


def empty_draft(**overrides) -> dict:
    data = {
        'summary': '',
        'intent': '',
        'objects_used': [],
        'selected_fields': [],
        'condition_interpretation': '',
        'join_assumptions': [],
        'risk_level': 'unknown',
        'warnings': [],
        'sql': '',
        'evidence': [],
        'fail_closed': False,
    }
    data.update(overrides)
    return data


def get_effective_sql_dialect(dialect: str, oceanbase_mode: str = '') -> str:
    d = str(dialect or '').strip().lower()
    if d == 'oceanbase':
        from tools.db_contracts import normalize_oceanbase_mode
        mode = normalize_oceanbase_mode(oceanbase_mode)
        return 'mysql' if mode == 'mysql' else 'oracle'
    return d or 'oracle'


def build_safe_context(
    *,
    dialect: str,
    alias: str,
    question: str,
    action: str,
    snapshot=None,
    selected_tables=None,
    selected_fields=None,
    current_sql: str = '',
    error_text: str = '',
    stale: bool = False,
    evidence=None,
    database: str = '',
    schema_name: str = '',
    oceanbase_mode: str = '',
) -> str:
    tables = [str(item).strip() for item in (selected_tables or []) if str(item).strip()]
    fields = [str(item).strip() for item in (selected_fields or []) if str(item).strip()]
    d_lower = str(dialect or '').strip().lower()
    parts = []
    if d_lower == 'oceanbase':
        from tools.db_contracts import normalize_oceanbase_mode
        eff_mode = normalize_oceanbase_mode(oceanbase_mode)
        mode_label = 'MySQL 兼容模式' if eff_mode == 'mysql' else 'Oracle 兼容模式'
        parts.append('数据库类型：OceanBase')
        parts.append(f'兼容模式：{mode_label}')
        parts.append(f'SQL 语法必须遵循 {mode_label} 规范。')
    else:
        parts.append(f'方言：{dialect}')
    parts.append(f'连接别名：{alias or "未命名"}')
    parts.append(f'动作：{ACTIONS.get(action, action)}')
    if database:
        parts.append(f'数据库：{database}')
    if schema_name:
        parts.append(f'Schema/Owner：{schema_name}')
    if stale and action == 'generate':
        parts.append('结构快照无效，禁止生成 SQL。')
        return '\n'.join(parts)
    if isinstance(evidence, dict) and evidence.get('tables'):
        parts.append('已确认结构证据（仅元数据，禁止猜测未列出的字段）：\n' + evidence_prompt_text(evidence))
        confirmed = [str(item) for item in (evidence.get('confirmed_fields') or []) if str(item)]
        if confirmed:
            parts.append('SQL 只允许引用已确认字段：' + ', '.join(confirmed))
        if evidence.get('condition_hints'):
            hint_strs = [
                f"{h.get('field')} {h.get('op', '=')} '{h.get('val')}'"
                if not str(h.get('val')).isdigit()
                else f"{h.get('field')} {h.get('op', '=')} {h.get('val')}"
                for h in evidence['condition_hints']
            ]
            parts.append('从问题解析出的过滤条件提示：' + ', '.join(hint_strs))
    else:
        if tables:
            parts.append('用户选中对象：' + ', '.join(tables))
        if fields:
            parts.append('用户选中字段：' + ', '.join(fields))
            parts.append('SQL 只允许引用选中字段、选中对象和 COUNT(*)。')
        clipped = clip_snapshot_for_prompt(snapshot, selected_tables=tables, selected_fields=fields)
        if clipped:
            parts.append('结构快照（已裁剪，仅元数据）：\n' + clipped)
    if current_sql and action in ('explain', 'optimize', 'fix', 'generate'):
        parts.append('当前编辑器 SQL：\n' + str(current_sql)[:4000])
    if error_text and action == 'fix':
        parts.append('用户粘贴的错误：\n' + str(error_text)[:1500])
    parts.append('用户问题：\n' + str(question or ''))
    return '\n'.join(parts)


def _idents(sql: str) -> set[str]:
    return {item.upper() for item in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', str(sql or ''))}


_SQL_FUNCTIONS = frozenset({
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'NVL', 'NVL2', 'COALESCE', 'DECODE',
    'TO_CHAR', 'TO_DATE', 'TO_NUMBER', 'TRUNC', 'SUBSTR', 'INSTR', 'LENGTH',
    'CONCAT', 'LOWER', 'UPPER', 'TRIM', 'LTRIM', 'RTRIM', 'REPLACE', 'ROUND',
    'CEIL', 'FLOOR', 'ABS', 'MOD', 'SYSDATE', 'SYSTIMESTAMP', 'NOW', 'CURDATE',
    'CURTIME', 'DATE_FORMAT', 'STR_TO_DATE', 'DATEDIFF', 'DATE_ADD', 'DATE_SUB',
    'IFNULL', 'NULLIF', 'ROW_NUMBER', 'RANK', 'DENSE_RANK', 'OVER', 'PARTITION',
})


def validate_draft(draft: dict, selected_tables=None, selected_fields=None, dialect: str = 'oracle') -> dict:
    data = empty_draft(**{k: v for k, v in (draft or {}).items() if k in empty_draft()})
    warnings = [str(item) for item in (data.get('warnings') or []) if str(item).strip()]
    tables = [str(item).strip() for item in (selected_tables or data.get('objects_used') or []) if str(item).strip()]
    fields = [str(item).strip() for item in (selected_fields or []) if str(item).strip()]
    sql = str(data.get('sql') or '').strip()
    data['sql'] = sql
    if fields and sql:
        allowed = {item.upper() for item in fields} | {item.upper() for item in tables} | _SQL_FUNCTIONS
        func_calls = {m.group(1).upper() for m in re.finditer(r'\b([A-Za-z_][A-Za-z0-9_$#]*)\s*\(', sql)}
        leftover = []
        for token in _idents(sql):
            if token.lower() in _SQL_KEYWORDS:
                continue
            if token in allowed or token in func_calls:
                continue
            leftover.append(token)
        if leftover:
            warnings.append('选中字段约束：SQL 出现了未勾选标识符 ' + ', '.join(sorted(set(leftover))[:12]))
    joins = data.get('join_assumptions')
    if isinstance(joins, str):
        joins = [joins] if joins.strip() else []
    if not isinstance(joins, list):
        joins = []
    if len(tables) > 1:
        lowered = sql.lower()
        if 'join' not in lowered and '=' not in lowered:
            if '需人工补充 Join 条件' not in ''.join(warnings):
                warnings.append('需人工补充 Join 条件')
            data['risk_level'] = 'unknown'
            if not joins:
                joins = ['多表无明确关联，不能视为可安全执行']
    if not data.get('objects_used'):
        data['objects_used'] = tables
    if not data.get('selected_fields'):
        data['selected_fields'] = fields
    info = classify_statement(sql, dialect) if sql else {}
    if info.get('needs_confirm') and '写入/结构变更草案，必须人工确认后才会执行' not in warnings:
        warnings.append('写入/结构变更草案，必须人工确认后才会执行')
    safety = ai_draft_safety(sql, dialect)
    data['fail_closed'] = bool(safety.get('fail_closed'))
    if safety.get('fail_closed') and safety.get('reason') not in warnings:
        warnings.append(str(safety.get('reason')))
    data['join_assumptions'] = [str(item) for item in joins if str(item).strip()]
    data['warnings'] = warnings
    level = str(data.get('risk_level') or 'unknown').lower()
    if level in ('low', 'medium'):
        level = 'read'
    if level == 'high':
        level = 'write' if info.get('category') in ('dml', 'redis_write', 'mongo_write') else 'unknown'
    join_blocked = '需人工补充 Join 条件' in ''.join(warnings)
    if info.get('category') == 'ddl':
        level = 'ddl'
    elif join_blocked:
        level = 'unknown'
    elif info.get('is_read'):
        level = 'read' if level not in ('write', 'ddl') else level
    elif info.get('category') in ('dml', 'redis_write', 'mongo_write'):
        level = 'write'
    if level not in ('read', 'write', 'ddl', 'unknown'):
        level = 'unknown'
    data['risk_level'] = level
    return data


def format_explanation(draft: dict) -> str:
    data = draft if isinstance(draft, dict) else empty_draft()
    lines = [
        f"摘要：{data.get('summary') or '（无）'}",
        f"意图：{data.get('intent') or '（无）'}",
        '对象：' + (', '.join(data.get('objects_used') or []) or '（未标明）'),
        '字段：' + (', '.join(data.get('selected_fields') or []) or '（未限定）'),
        '证据：' + (', '.join(str(item) for item in (data.get('evidence') or []) if str(item)) or '（无）'),
        f"条件：{data.get('condition_interpretation') or '（无）'}",
        'Join 假设：' + ('；'.join(data.get('join_assumptions') or []) or '（无）'),
        f"风险：{data.get('risk_level') or 'unknown'}",
    ]
    warnings = [str(item) for item in (data.get('warnings') or []) if str(item).strip()]
    if warnings:
        lines.append('警告：')
        lines.extend(f'- {item}' for item in warnings)
    return '\n'.join(lines)


def generate_sql_draft(
    question: str,
    *,
    action: str = 'generate',
    dialect: str = 'oracle',
    alias: str = '',
    snapshot=None,
    selected_tables=None,
    selected_fields=None,
    current_sql: str = '',
    error_text: str = '',
    stale: bool = False,
    evidence=None,
    database: str = '',
    schema_name: str = '',
    oceanbase_mode: str = '',
    cfg=None,
) -> dict:
    settings = cfg if isinstance(cfg, dict) else load_ai_local()
    if not is_enabled(settings):
        raise IntranetLlmError('未启用内网模型，请先在设置中配置并探测')
    wanted = action or 'generate'
    if wanted == 'generate':
        if stale or not isinstance(evidence, dict) or not evidence.get('tables'):
            return empty_draft(
                summary='无有效字段证据，已拒绝调用模型',
                warnings=['无有效快照或字段证据，不会猜测表或字段。'],
                fail_closed=True,
            )
    context = build_safe_context(
        dialect=dialect,
        alias=alias,
        question=question,
        action=wanted,
        snapshot=snapshot if wanted != 'generate' else None,
        selected_tables=selected_tables,
        selected_fields=selected_fields,
        current_sql=current_sql,
        error_text=error_text,
        stale=False if wanted == 'generate' else stale,
        evidence=evidence if wanted == 'generate' else None,
        database=database,
        schema_name=schema_name,
        oceanbase_mode=oceanbase_mode,
    )
    content = chat_completions(
        [
            {'role': 'system', 'content': _SYSTEM},
            {'role': 'user', 'content': context},
        ],
        cfg=settings,
    )
    parsed = _extract_json(content)
    if not parsed.get('sql'):
        parsed['sql'] = strip_markdown_fence(content)
    effective_dialect = get_effective_sql_dialect(dialect, oceanbase_mode)
    return validate_draft(parsed, selected_tables, selected_fields, effective_dialect)
