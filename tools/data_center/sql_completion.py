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
    # Metadata names are always quoted, including vendor-reserved words.
    # Legacy snapshots can omit the provider/mode; preserve their bare names.
    if dialect not in {"mysql", "oracle", "oceanbase_mysql", "oceanbase_oracle"} and _BARE.fullmatch(name):
        return name
    quote = "`" if dialect.endswith("mysql") else '"'
    return quote + name.replace(quote, quote * 2) + quote


def completion_candidates(snapshot, sql, position, prefix, keywords):
    dialect = str(snapshot.get("dialect") or "generic")
    if len(sql) > 256 * 1024:
        return [kw for kw in keywords if kw.casefold().startswith(prefix.casefold())]
    objects = [obj for obj in snapshot.get("objects") or () if isinstance(obj, dict) and isinstance(obj.get("name"), str)]
    # An unfinished quoted completion token is valid while editing. Mask only
    # that token before lexing; all comments/literals elsewhere stay masked.
    marker = "x" * len(prefix)
    lex_text = sql[:position - len(prefix)] + marker + sql[position:]
    try:
        code = scan_sql(lex_text, dialect).code
    except SqlScriptError:
        return []
    if prefix and code[position - len(prefix):position] != marker:
        return []  # The caret is inside a comment or string literal.
    # Split at dots outside quoted identifiers (names can contain a dot).
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
            return [qualifier + "." + _quote(obj['name'], dialect) for obj in objects
                    if _matches(owner, quoted, str(obj.get('owner', '')), dialect)
                    and obj['name'].casefold().startswith(partial.casefold())][:2000]
        return [f"{qualifier}.{_quote(col['name'], dialect)}" for col in table.get("columns") or ()
                if isinstance(col, dict) and isinstance(col.get("name"), str) and col['name'].casefold().startswith(partial.casefold())][:1024]
    low = prefix.lstrip('"`').casefold()
    hits = [kw for kw in keywords if kw.casefold().startswith(low)]
    for obj in objects:
        name = obj["name"]
        compact, needle = name.casefold().replace("_", ""), low.replace("_", "")
        if compact.startswith(needle) or len(needle) >= 3 and _subsequence(needle, compact):
            hits.append(_quote(name, dialect))
            if obj.get("owner"):
                hits.append(_quote(obj["owner"], dialect) + "." + _quote(name, dialect))
    return list(dict.fromkeys(hits))[:2000]


def _subsequence(needle, value):
    chars = iter(value)
    return all(char in chars for char in needle)
