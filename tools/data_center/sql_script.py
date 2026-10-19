"""Statement boundaries and code masks for the manual relational workbench.

Offsets are Python string indexes. This lexer does not parse procedural blocks
or assume MySQL NO_BACKSLASH_ESCAPES; ambiguous input is rejected explicitly.
"""

from dataclasses import dataclass
import re


class SqlScriptError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SqlSegment:
    sql: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class SqlScript:
    segments: tuple[SqlSegment, ...]
    code: str


def scan_sql(sql: str, dialect: str = "generic") -> SqlScript:
    text = str(sql or "")
    mysql = dialect in {"generic", "mysql", "oceanbase_mysql"}
    oracle = dialect in {"generic", "oracle", "oceanbase_oracle"}
    mask = list(text)
    segments = []
    start = 0
    index = 0
    count = len(text)

    def blank(begin, end):
        for position in range(begin, end):
            if text[position] not in "\r\n":
                mask[position] = " "

    def append(end):
        if "".join(mask[start:end]).strip():
            segments.append(SqlSegment(text[start:end].strip(), start, end))

    while index < count:
        char = text[index]
        if text.startswith("--", index) or (mysql and char == "#"):
            # MySQL -- comments require whitespace/control after the dashes.
            if char == "-" and dialect in {"mysql", "oceanbase_mysql"} and index + 2 < count and not text[index + 2].isspace():
                index += 2
                continue
            end = index + 1
            while end < count and text[end] not in "\r\n":
                end += 1
            blank(index, end)
            index = end
            continue
        if text.startswith("/*", index):
            if mysql and (text.startswith("/*!", index) or text[index:index + 4].upper() == "/*M!"):
                raise SqlScriptError("SQL_EXECUTABLE_COMMENT_UNSUPPORTED")
            end = text.find("*/", index + 2)
            if end < 0:
                raise SqlScriptError("SQL_INCOMPLETE_COMMENT")
            blank(index, end + 2)
            index = end + 2
            continue
        if oracle and char.lower() in {"q", "n"} and (index == 0 or not (text[index - 1].isalnum() or text[index - 1] in "_$#")):
            quote = index + (2 if text[index:index + 2].lower() == "nq" else 1)
            if (char.lower() == "q" or quote == index + 2) and quote + 1 < count and text[quote] == "'":
                opening = text[quote + 1]
                if opening.isspace() or opening == "'":
                    raise SqlScriptError("SQL_INVALID_QUOTE")
                closing = {"[": "]", "{": "}", "(": ")", "<": ">"}.get(opening, opening) + "'"
                end = text.find(closing, quote + 2)
                if end < 0:
                    raise SqlScriptError("SQL_INCOMPLETE_QUOTE")
                blank(index, end + 2)
                index = end + 2
                continue
        if char in "'\"" or mysql and char == "`":
            end = index + 1
            while end < count:
                if dialect in {"mysql", "oceanbase_mysql"} and text[end] == "\\" and end + 1 < count and text[end + 1] == char:
                    raise SqlScriptError("SQL_QUOTE_MODE_AMBIGUOUS")
                if text[end] == char:
                    if end + 1 < count and text[end + 1] == char:
                        end += 2
                        continue
                    break
                end += 1
            if end >= count:
                raise SqlScriptError("SQL_INCOMPLETE_QUOTE")
            blank(index, end + 1)
            index = end + 1
            continue
        if char == ";":
            append(index)
            start = index + 1
        index += 1
    append(count)
    return SqlScript(tuple(segments), "".join(mask))


def statement_at(sql: str, position: int, dialect: str) -> str:
    script = scan_sql(sql, dialect)
    if has_procedural_block(script.code):
        raise SqlScriptError("SQL_PROCEDURAL_SCRIPT_UNSUPPORTED")
    position = max(0, min(int(position), len(sql)))
    return next((segment.sql for segment in script.segments if segment.start <= position <= segment.end), "")


def has_procedural_block(code: str) -> bool:
    return re.search(
        r"(?is)(?:^|;)\s*(?:DELIMITER\b|DECLARE\b|BEGIN\b|WITH\s+(?:FUNCTION|PROCEDURE)\b|CREATE\s+(?:OR\s+REPLACE\s+)?"
        r"(?:(?:NON)?EDITIONABLE\s+)?(?:DEFINER\s*=\s*\S+\s+)?(?:PROCEDURE|FUNCTION|TRIGGER|EVENT|PACKAGE)\b)",
        code,
    ) is not None


def selected_procedural_sql(sql: str, provider: str) -> str | None:
    """Normalize only outer client terminators; never split a selected body.

    The vendor parses the complete statement. Ordinary SQL and WITH-local
    routine queries stay on their own paths, not this non-rowset block API.
    """
    if provider not in {"oracle", "oceanbase_oracle", "mysql", "oceanbase_mysql"}:
        raise SqlScriptError("BLOCK_PROVIDER_UNSUPPORTED")
    text = str(sql or "").strip()
    mysql = provider.endswith("mysql")
    delimiter = None
    lines = text.splitlines()
    if mysql and lines and re.fullmatch(r"(?i)DELIMITER\s+[^\s'\"`]{1,16}", lines[0].strip()):
        delimiter = lines.pop(0).strip().split(None, 1)[1]
        if lines and re.fullmatch(r"(?i)DELIMITER\s+;", lines[-1].strip()):
            lines.pop()
        text = "\n".join(lines).strip()
        if not text.endswith(delimiter):
            raise SqlScriptError("BLOCK_CLIENT_TERMINATOR_MISSING")
        text = text[:-len(delimiter)].rstrip()
    if not mysql and lines and lines[-1].strip() == "/":
        text = "\n".join(lines[:-1]).strip()
    code = scan_sql(text, provider).code.strip()
    if not has_procedural_block(code) or re.match(r"(?is)^WITH\b", code):
        if delimiter is not None:
            raise SqlScriptError("BLOCK_BODY_REQUIRED")
        return None
    if not re.match(r"(?is)^(?:CREATE\b|DECLARE\b|BEGIN\b)", code):
        raise SqlScriptError("BLOCK_BODY_REQUIRED")
    if re.match(r"(?is)^(?:DECLARE|BEGIN)\b", code) and not re.search(r"(?is)\bEND(?:\s+[\w$#]+)?\s*;?\s*$", code):
        raise SqlScriptError("BLOCK_INCOMPLETE")
    return text
