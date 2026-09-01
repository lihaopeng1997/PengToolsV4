# -*- coding: utf-8 -*-
# Database unified object search index and query engine.

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


def build_schema_search_index(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    snap = snapshot if isinstance(snapshot, dict) else {}
    objects = list(snap.get('objects') or [])

    tables: List[Dict[str, Any]] = []
    fields: List[Dict[str, Any]] = []
    schemas_seen: Set[str] = set()
    schemas: List[Dict[str, Any]] = []

    for obj in objects:
        if not isinstance(obj, dict):
            continue
        owner = str(obj.get('owner') or '').strip()
        name = str(obj.get('name') or '').strip()
        if not name and not owner:
            continue
        comment = str(obj.get('comment') or '').strip()
        obj_type = str(obj.get('object_type') or 'TABLE').upper()

        if owner and owner not in schemas_seen:
            schemas_seen.add(owner)
            schemas.append({
                'kind': 'schema',
                'name': owner,
                'name_lower': owner.lower(),
            })

        qn = f'{owner}.{name}' if owner and name else (name or owner)
        table_entry = {
            'kind': 'table',
            'owner': owner,
            'owner_lower': owner.lower(),
            'name': name,
            'name_lower': name.lower(),
            'qualified': qn,
            'qualified_lower': qn.lower(),
            'comment': comment,
            'comment_lower': comment.lower(),
            'object_type': obj_type,
            'object': obj,
        }
        tables.append(table_entry)

        for col in obj.get('columns') or []:
            if not isinstance(col, dict):
                continue
            c_name = str(col.get('name') or '').strip()
            if not c_name:
                continue
            c_comment = str(col.get('comment') or '').strip()
            c_dtype = str(col.get('data_type') or '').strip()
            c_qn = f'{qn}.{c_name}' if qn else c_name

            field_entry = {
                'kind': 'field',
                'owner': owner,
                'owner_lower': owner.lower(),
                'table_name': name,
                'table_name_lower': name.lower(),
                'name': c_name,
                'name_lower': c_name.lower(),
                'qualified': c_qn,
                'qualified_lower': c_qn.lower(),
                'comment': c_comment,
                'comment_lower': c_comment.lower(),
                'data_type': c_dtype,
                'object': obj,
                'column': col,
            }
            fields.append(field_entry)

    return {
        'snapshot_id': str(snap.get('snapshot_id') or ''),
        'dialect': str(snap.get('dialect') or '').lower(),
        'tables': tables,
        'fields': fields,
        'schemas': schemas,
    }


def _match_tier(
    q: str,
    name_l: str,
    qual_l: str,
    comment_l: str,
    owner_l: str = '',
) -> int:
    # Tier 1: Identifier exact match
    if name_l == q or qual_l == q:
        return 1

    # Tier 2: Identifier prefix match
    if name_l.startswith(q) or qual_l.startswith(q):
        return 2

    # Tier 3: Identifier substring match
    if q in name_l or q in qual_l:
        return 3

    # Tier 4: Comment exact or prefix match
    if comment_l and (comment_l == q or comment_l.startswith(q)):
        return 4

    # Tier 5: Comment substring match
    if comment_l and q in comment_l:
        return 5

    # Tier 6: Schema/Owner match
    if owner_l and (owner_l == q or q in owner_l):
        return 6

    return 999


def search_schema_index(
    index: Optional[Dict[str, Any]],
    query: str = '',
    limit: int = 50,
) -> List[Dict[str, Any]]:
    if not isinstance(index, dict):
        return []
    q = str(query or '').strip().lower()
    if not q:
        return []

    limit = max(1, int(limit))
    candidates: List[Tuple[Tuple, Dict[str, Any]]] = []

    # 1. Tables
    for entry in index.get('tables') or []:
        tier = _match_tier(
            q,
            entry['name_lower'],
            entry['qualified_lower'],
            entry['comment_lower'],
            entry['owner_lower'],
        )
        if tier < 999:
            len_diff = abs(len(entry['name_lower']) - len(q))
            sort_key = (tier, 0, len_diff, entry['owner_lower'], entry['name_lower'], '')
            item = {
                'kind': 'table',
                'owner': entry['owner'],
                'table_name': entry['name'],
                'field_name': '',
                'table_comment': entry['comment'],
                'field_comment': '',
                'data_type': '',
                'object_type': entry['object_type'],
                'object': entry['object'],
                'column': None,
                'title': entry['qualified'],
                'subtitle': entry['comment'] or entry['object_type'],
            }
            candidates.append((sort_key, item))

    # 2. Fields
    for entry in index.get('fields') or []:
        tier = _match_tier(
            q,
            entry['name_lower'],
            entry['qualified_lower'],
            entry['comment_lower'],
            entry['owner_lower'],
        )
        if tier < 999:
            len_diff = abs(len(entry['name_lower']) - len(q))
            sort_key = (tier, 1, len_diff, entry['owner_lower'], entry['table_name_lower'], entry['name_lower'])
            sub_parts = []
            if entry['data_type']:
                sub_parts.append(entry['data_type'])
            if entry['comment']:
                sub_parts.append(entry['comment'])
            subtitle = ' · '.join(sub_parts) if sub_parts else str((entry.get('object') or {}).get('comment') or '')

            item = {
                'kind': 'field',
                'owner': entry['owner'],
                'table_name': entry['table_name'],
                'field_name': entry['name'],
                'table_comment': str((entry.get('object') or {}).get('comment') or ''),
                'field_comment': entry['comment'],
                'data_type': entry['data_type'],
                'object_type': 'FIELD',
                'object': entry['object'],
                'column': entry['column'],
                'title': entry['qualified'],
                'subtitle': subtitle,
            }
            candidates.append((sort_key, item))

    # 3. Schemas
    for entry in index.get('schemas') or []:
        tier = 999
        if entry['name_lower'] == q:
            tier = 1
        elif entry['name_lower'].startswith(q):
            tier = 2
        elif q in entry['name_lower']:
            tier = 3

        if tier < 999:
            len_diff = abs(len(entry['name_lower']) - len(q))
            sort_key = (tier, 2, len_diff, entry['name_lower'], '', '')
            item = {
                'kind': 'schema',
                'owner': entry['name'],
                'table_name': '',
                'field_name': '',
                'table_comment': '',
                'field_comment': '',
                'data_type': '',
                'object_type': 'SCHEMA',
                'object': None,
                'column': None,
                'title': entry['name'],
                'subtitle': 'Schema / Owner',
            }
            candidates.append((sort_key, item))

    candidates.sort(key=lambda pair: pair[0])
    return [item for _sort_key, item in candidates[:limit]]


def get_matched_table_identities(
    index: Optional[Dict[str, Any]],
    query: str = '',
) -> Set[Tuple[str, str]]:
    if not isinstance(index, dict) or not query.strip():
        return set()
    results = search_schema_index(index, query, limit=5000)
    matched: Set[Tuple[str, str]] = set()
    for item in results:
        kind = item.get('kind')
        owner = str(item.get('owner') or '')
        table_name = str(item.get('table_name') or '')
        if kind in ('table', 'field') and table_name:
            matched.add((owner.lower(), table_name.lower()))
    return matched
