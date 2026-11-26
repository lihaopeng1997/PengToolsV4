"""Local identifier completion from a scoped metadata DTO, without database I/O.

Alias resolution covers ordinary FROM/JOIN/UPDATE/INTO at the cursor's query
depth. CTEs and set operations deliberately keep direct object completion only;
this is not a semantic SQL parser or an execution/permission boundary.
"""

import re

from tools.data_center.sql_script import SqlScriptError, scan_sql


_BARE = re.compile(r"[^\W\d][\w$#]*", re.UNICODE)
_PART = r'(?:[\w$#]+|"(?:[^"\r\n]|"")*"|`(?:[^`\r\n]|``)*`)'
_TAIL = r'(?:[\w$#]*|"(?:[^"\r\n]|"")*|`(?:[^`\r\n]|``)*)'
_TOKEN = re.compile(rf'(?<![\w$#."`])(?:{_PART}\s*\.\s*)*{_TAIL}$')
_RESERVED = frozenset("SELECT FROM JOIN LEFT RIGHT INNER OUTER CROSS FULL ON WHERE GROUP ORDER BY HAVING UNION EXCEPT INTERSECT LIMIT OFFSET FETCH FOR SET VALUES RETURNING USING AS AND OR WITH CONNECT START MODEL PARTITION WINDOW QUALIFY NATURAL USE FORCE IGNORE".split())


def current_token(sql, position):
    position = max(0, min(position, len(sql)))
    start = max(sql.rfind("\n", 0, position) + 1, position - 4096)
    match = _TOKEN.search(sql[start:position])
    begin = start + match.start() if match else position
    return sql[begin:position], begin, position


def _identifier(text, start):
    if start >= len(text):
        return None
    quote = text[start]
    if quote in {'"', '`'}:
        end = start + 1
        value = []
        while end < len(text):
            if text[end] == quote:
                if end + 1 < len(text) and text[end + 1] == quote:
                    value.append(quote)
                    end += 2
                    continue
                return "".join(value), end + 1, True
            value.append(text[end])
            end += 1
        return None
    match = _BARE.match(text, start)
    return (match.group(), match.end(), False) if match else None


def _parts(text):
    values = []
    pos = 0
    while pos < len(text):
        pos = _space(text, pos)
        item = _identifier(text, pos)
        if item is None:
            return []
        value, pos, quoted = item
        values.append((value, quoted))
        pos = _space(text, pos)
        if pos == len(text):
            return values
        if text[pos] != ".":
            return []
        pos += 1
    return []


def _space(text, pos):
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _trivia(text, code, pos):
    while pos < len(text):
        after = _space(text, pos)
        if after != pos:
            pos = after
        elif text.startswith("/*", pos) and code[pos] == " ":
            pos = text.find("*/", pos + 2) + 2
        elif (text.startswith("--", pos) or text[pos] == "#") and code[pos] == " ":
            pos = text.find("\n", pos)
            if pos < 0:
                return len(text)
        else:
            break
    return pos


def _matches(value, quoted, actual, dialect):
    if quoted:
        return value == actual
    return value.upper() == actual if dialect.endswith("oracle") else value.casefold() == actual.casefold()


def _resolve(objects, parts, dialect):
    if not parts or len(parts) > 2:
        return None
    hits = []
    for obj in objects:
        fields = [str(obj.get("owner") or ""), obj["name"]] if len(parts) == 2 else [obj["name"]]
        if all(_matches(value, quoted, field, dialect)
               for (value, quoted), field in zip(parts, fields)):
            hits.append(obj)
    return hits[0] if len(hits) == 1 else None


def _alias_table(objects, sql, code, position, qualifier, dialect):
    begin = code.rfind(";", 0, position) + 1
    end = code.find(";", position)
    end = len(code) if end < 0 else end
    segment = code[begin:end]
    if re.search(r"(?i)\b(WITH|UNION|INTERSECT|EXCEPT)\b", segment):
        return False, None
    depths = []
    depth = 0
    for char in segment:
        depths.append(depth)
        depth += (char == "(") - (char == ")")
    cursor_depth = depths[position - begin] if position < end else depth
    hits = []
    for match in re.finditer(r"(?i)\b(?:FROM|JOIN|UPDATE|INTO)\b", segment):
        if depths[match.start()] != cursor_depth:
            continue
        pos = _trivia(sql, code, begin + match.end())
        parts = []
        while True:
            item = _identifier(sql, pos)
            if item is None:
                break
            value, pos, quoted = item
            parts.append((value, quoted))
            pos = _trivia(sql, code, pos)
            if pos < end and sql[pos] == ".":
                pos = _trivia(sql, code, pos + 1)
                continue
            break
        if not parts:
            continue
        alias = _identifier(sql, pos)
        if alias and alias[0].upper() == "AS" and not alias[2]:
            alias = _identifier(sql, _trivia(sql, code, alias[1]))
        if not alias or (not alias[2] and alias[0].upper() in _RESERVED):
            continue
        value, _, quoted = alias
        wanted, wanted_quoted = qualifier
        actual = value if quoted or not dialect.endswith("oracle") else value.upper()
        if _matches(wanted, wanted_quoted, actual, dialect):
            hits.append(_resolve(objects, parts, dialect))
    return bool(hits), hits[0] if len(hits) == 1 else None


def _quote(name, dialect):
    # Keep normal bare SQL identifiers clean (e.g. PRPCMAIN instead of "PRPCMAIN"),
    # and quote only when the identifier contains special characters, spaces,
    # reserved words, or case-sensitive lowercase letters in Oracle dialects.
    text = str(name or "")
    if not text:
        return text
    if _BARE.fullmatch(text) and text.upper() not in _RESERVED:
        if dialect in {"oracle", "oceanbase_oracle"}:
            if text == text.upper():
                return text
        else:
            return text
    if dialect not in {"mysql", "oracle", "oceanbase_mysql", "oceanbase_oracle"} and _BARE.fullmatch(text):
        return text
    quote = "`" if dialect.endswith("mysql") else '"'
    return quote + text.replace(quote, quote * 2) + quote


def _active_tables(objects, sql, code, position, dialect):
    """提取当前 SQL 语句 FROM/JOIN/UPDATE/INTO 引用的表，用于无前缀字段联想。"""
    begin = code.rfind(";", 0, position) + 1
    end = code.find(";", position)
    end = len(code) if end < 0 else end
    segment = code[begin:end]
    found = []
    seen = set()
    for match in re.finditer(r"(?i)\b(?:FROM|JOIN|UPDATE|INTO)\b", segment):
        pos = _trivia(sql, code, begin + match.end())
        parts = []
        while True:
            item = _identifier(sql, pos)
            if item is None:
                break
            value, pos, quoted = item
            parts.append((value, quoted))
            pos = _trivia(sql, code, pos)
            if pos < end and sql[pos] == ".":
                pos = _trivia(sql, code, pos + 1)
                continue
            break
        if not parts:
            continue
        table = _resolve(objects, parts, dialect)
        if table is not None:
            key = (str(table.get("owner") or ""), str(table.get("name") or ""))
            if key not in seen:
                seen.add(key)
                found.append(table)
    return found


_TABLE_CLAUSE_RE = re.compile(
    r"(?is)\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+(?:[^\s,;()]+\s*,\s*)*$"
)


def _prefers_tables(code: str, token_start: int) -> bool:
    before = code[max(0, token_start - 256):token_start]
    stmt_start = before.rfind(";")
    if stmt_start >= 0:
        before = before[stmt_start + 1:]
    return _TABLE_CLAUSE_RE.search(before) is not None


def completion_items(snapshot, sql, position, prefix, keywords):
    """Return structured completion items: list of (text, kind, detail)."""
    dialect = str((snapshot or {}).get("dialect") or "generic")
    if len(sql) > 256 * 1024:
        return [
            (kw, "func" if kw.endswith(")") else "keyword", "关键字")
            for kw in keywords if kw.casefold().startswith(prefix.casefold())
        ]
    objects = [
        obj for obj in (snapshot or {}).get("objects") or ()
        if isinstance(obj, dict) and isinstance(obj.get("name"), str)
    ]
    marker = "x" * len(prefix)
    lex_text = sql[:position - len(prefix)] + marker + sql[position:]
    try:
        code = scan_sql(lex_text, dialect).code
    except SqlScriptError:
        return []
    if prefix and code[position - len(prefix):position] != marker:
        return []
    token_code = re.sub(r'"(?:[^"]|"")*"|`(?:[^`]|``)*`', lambda match: " " * len(match.group()), prefix)
    split = token_code.rfind(".")
    if split >= 0:
        qualifier = prefix[:split].strip()
        partial = prefix[split + 1:].strip().lstrip('"`')
        parts = _parts(qualifier)
        aliased, table = _alias_table(objects, sql, code, position, parts[0], dialect) if len(parts) == 1 else (False, None)
        if not aliased:
            table = _resolve(objects, parts, dialect)
        if table is None:
            if aliased or len(parts) != 1:
                return []
            owner, quoted = parts[0]
            out = []
            for obj in objects:
                if _matches(owner, quoted, str(obj.get("owner", "")), dialect) and obj["name"].casefold().startswith(partial.casefold()):
                    detail = str(obj.get("comment") or obj.get("owner") or "")
                    kind = "view" if str(obj.get("kind") or "").lower() == "view" else "table"
                    out.append((qualifier + "." + _quote(obj["name"], dialect), kind, detail))
            return out[:2000]
        out = []
        for col in table.get("columns") or ():
            if isinstance(col, dict) and isinstance(col.get("name"), str) and col["name"].casefold().startswith(partial.casefold()):
                detail = str(col.get("type") or col.get("comment") or table.get("name") or "")
                out.append((f"{qualifier}.{_quote(col['name'], dialect)}", "column", detail))
        return out[:1024]

    low = prefix.lstrip('"`').casefold()
    prefer_tables = _prefers_tables(code, position - len(prefix))

    kw_items = [
        (kw, "func" if "(" in kw else "keyword", "函数" if "(" in kw else "关键字")
        for kw in keywords if kw.casefold().startswith(low)
    ]

    col_items = []
    for tbl in _active_tables(objects, sql, code, position, dialect):
        tname = str(tbl.get("name") or "")
        for col in tbl.get("columns") or ():
            if not isinstance(col, dict) or not isinstance(col.get("name"), str):
                continue
            cname = col["name"]
            if not low or cname.casefold().startswith(low):
                detail = " · ".join(p for p in (tname, str(col.get("type") or ""), str(col.get("comment") or "")) if p)
                col_items.append((_quote(cname, dialect), "column", detail))

    prefix_tables = []
    fuzzy_tables = []
    for obj in objects:
        name = obj["name"]
        owner = str(obj.get("owner") or "")
        comment = str(obj.get("comment") or "")
        kind = "view" if str(obj.get("kind") or "").lower() == "view" else "table"
        detail = " · ".join(p for p in (owner, comment) if p)
        compact, needle = name.casefold().replace("_", ""), low.replace("_", "")
        if not needle or compact.startswith(needle):
            prefix_tables.append((_quote(name, dialect), kind, detail))
            if owner:
                prefix_tables.append((_quote(owner, dialect) + "." + _quote(name, dialect), kind, comment or owner))
        elif len(needle) >= 2 and (needle in compact or _subsequence(needle, compact)):
            fuzzy_tables.append((_quote(name, dialect), kind, detail))
            if owner:
                fuzzy_tables.append((_quote(owner, dialect) + "." + _quote(name, dialect), kind, comment or owner))

    table_items = prefix_tables + fuzzy_tables
    if prefer_tables:
        ordered = table_items + kw_items + col_items
    else:
        ordered = col_items + kw_items + table_items

    seen = set()
    unique = []
    for text, kind, detail in ordered:
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append((text, kind, detail))
        if len(unique) >= 2000:
            break
    return unique


def completion_candidates(snapshot, sql, position, prefix, keywords):
    return [item[0] for item in completion_items(snapshot, sql, position, prefix, keywords)]


def _subsequence(needle, value):
    chars = iter(value)
    return all(char in chars for char in needle)
