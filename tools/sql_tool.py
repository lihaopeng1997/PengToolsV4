# -*- coding: utf-8 -*-
import re
import os

DDL_KEYWORDS = ('CREATE', 'ALTER', 'DROP', 'TRUNCATE', 'RENAME', 'COMMENT')
DML_KEYWORDS = ('INSERT', 'UPDATE', 'DELETE', 'MERGE', 'SELECT')


def strip_comments(sql):
    result = []
    index = 0
    in_string = False
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ''
        if in_string:
            result.append(char)
            if char == "'" and next_char == "'":
                result.append(next_char)
                index += 2
                continue
            if char == "'":
                in_string = False
            index += 1
            continue
        if char == "'":
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == '-' and next_char == '-':
            index += 2
            while index < len(sql) and sql[index] not in '\r\n':
                index += 1
            continue
        if char == '/' and next_char == '*':
            index += 2
            while index + 1 < len(sql) and sql[index:index + 2] != '*/':
                index += 1
            index = min(index + 2, len(sql))
            continue
        result.append(char)
        index += 1
    return ''.join(result)


def strip_connection_header(sql):
    """移除已交付脚本中的连接头，避免合并多个文件后重复写入。"""
    lines = sql.lstrip('\ufeff').splitlines()
    result = []
    index = 0
    while index < len(lines):
        header_group = index + 2 < len(lines) and (
            lines[index].lstrip().startswith('---- 地址')
            and lines[index + 1].lstrip().lower().startswith('---- sid')
            and lines[index + 2].lstrip().startswith('---- 用户名')
        )
        if header_group:
            index += 3
            continue
        result.append(lines[index])
        index += 1
    return '\n'.join(result).strip()


def split_statements(sql):
    stmts = []
    depth = 0
    in_string = False
    last_cut = 0
    for i, c in enumerate(sql):
        if c == "'" and in_string and (
            (i + 1 < len(sql) and sql[i + 1] == "'")
            or (i > 0 and sql[i - 1] == "'")
        ):
            continue
        if c == "'":
            in_string = not in_string
        elif not in_string:
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            elif c == ';' and depth == 0:
                stmt = sql[last_cut:i].strip()
                if stmt:
                    stmts.append(stmt)
                last_cut = i + 1
    last_stmt = sql[last_cut:].strip()
    if last_stmt:
        stmts.append(last_stmt)
    return stmts


def _canonical_sql_statement(statement):
    """用于去重的规范形式：关键字忽略大小写，字符串值保留大小写。"""
    clean = strip_comments(statement).strip().rstrip(';')
    result = []
    in_string = False
    pending_space = False
    index = 0
    while index < len(clean):
        char = clean[index]
        next_char = clean[index + 1] if index + 1 < len(clean) else ''
        if in_string:
            result.append(char)
            if char == "'" and next_char == "'":
                result.append(next_char)
                index += 2
                continue
            if char == "'":
                in_string = False
            index += 1
            continue
        if char == "'":
            if pending_space and result:
                result.append(' ')
            pending_space = False
            in_string = True
            result.append(char)
        elif char.isspace():
            pending_space = True
        else:
            if pending_space and result:
                result.append(' ')
            pending_space = False
            result.append(char.upper())
        index += 1
    return ''.join(result).strip()


def deduplicate_sql_statements(sql):
    """按首次出现顺序合并 SQL，返回 (去重后的 SQL, 重复语句列表)。"""
    unique = []
    duplicates = []
    seen = set()
    for statement in split_statements(sql):
        key = _canonical_sql_statement(statement)
        if not key:
            continue
        if key in seen:
            duplicates.append(statement.rstrip(';') + ';')
            continue
        seen.add(key)
        unique.append(statement.rstrip(';') + ';')
    return '\n'.join(unique), duplicates


def is_ddl(upper_stmt):
    for kw in DDL_KEYWORDS:
        if re.match(r'^\s*' + kw + r'\b', upper_stmt):
            return True
    return False


def is_dml(upper_stmt):
    for kw in DML_KEYWORDS:
        if re.match(r'^\s*' + kw + r'\b', upper_stmt):
            return True
    return False


def classify_sql_type(sql):
    clean = strip_comments(sql)
    stmts = split_statements(clean)
    has_ddl = False
    has_dml = False
    for s in stmts:
        u = s.upper()
        if is_ddl(u):
            has_ddl = True
        if is_dml(u):
            has_dml = True
    if has_ddl and has_dml:
        return 'MIXED'
    if has_ddl:
        return 'DDL'
    return 'DML'


def split_mixed_sql(sql):
    sql, _ = deduplicate_sql_statements(sql)
    ddl_parts = []
    dml_parts = []
    for statement in split_statements(sql):
        u = strip_comments(statement).upper()
        if is_ddl(u) and not is_dml(u):
            ddl_parts.append(statement.rstrip(';') + ';')
        else:
            dml_parts.append(statement.rstrip(';') + ';')
    return {'ddl': '\n'.join(ddl_parts), 'dml': '\n'.join(dml_parts)}


def generate_rollback(orig_stmt, upper_stmt):
    m = re.match(r'^\s*CREATE\s+TABLE\s+(\S+)', upper_stmt)
    if m:
        return f'DROP TABLE {m.group(1)};\n-- CASCADE CONSTRAINTS may be needed'

    m = re.match(r'^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+(\S+)', upper_stmt)
    if m:
        return f'DROP INDEX {m.group(2)};'

    m = re.match(r'^\s*CREATE\s+SEQUENCE\s+(\S+)', upper_stmt)
    if m:
        return f'DROP SEQUENCE {m.group(1)};'

    m = re.match(r'^\s*ALTER\s+TABLE\s+(\S+)\s+ADD(?:\s+COLUMN)?\s+(.+?)\s*$', orig_stmt, re.I | re.S)
    if m:
        table_name = m.group(1)
        body = m.group(2).strip().rstrip(';').strip()
        if body.startswith('(') and body.endswith(')'):
            body = body[1:-1]
        columns = []
        for definition in _split_csv(body):
            name = definition.strip().split()[0].strip('"') if definition.strip() else ''
            if name.upper() == 'CONSTRAINT':
                parts = definition.strip().split()
                if len(parts) > 1:
                    columns.append(f'ALTER TABLE {table_name} DROP CONSTRAINT {parts[1]};')
            elif name:
                columns.append(f'ALTER TABLE {table_name} DROP COLUMN {name};')
        return '\n'.join(columns) or f'-- [必须人工补充] 无法解析 ALTER TABLE {table_name} ADD 的回滚列'

    if re.match(r'^\s*ALTER\s+TABLE\s+\S+\s+MODIFY', upper_stmt):
        return ('-- [必须人工补充] MODIFY 无法从升级 SQL 推断修改前的数据类型\n'
                f'-- 原升级语句：{orig_stmt.rstrip(";")}\n'
                '-- ALTER TABLE <表名> MODIFY (<字段> <升级前类型>);')

    if re.match(r'^\s*DROP\s+TABLE', upper_stmt):
        return f'-- [必须人工补充] DROP TABLE 无法自动恢复，请填写原 CREATE TABLE\n-- {orig_stmt.rstrip(";")}'

    match = re.match(
        r'^\s*INSERT\s+INTO\s+([^\s(]+)\s*\((.*?)\)\s*VALUES\s*\((.*)\)\s*$',
        orig_stmt, re.IGNORECASE | re.DOTALL
    )
    if match:
        table_name = match.group(1)
        columns = [item.strip() for item in _split_csv(match.group(2))]
        values = [item.strip() for item in _split_csv(match.group(3))]
        conditions = []
        for column, value in zip(columns, values):
            upper_value = value.upper()
            if upper_value == 'NULL':
                conditions.append(f'{column} IS NULL')
            elif re.match(r'^[A-Z_][A-Z0-9_$#]*\s*\(', upper_value):
                continue
            else:
                conditions.append(f'{column}={value}')
        if conditions:
            return f'DELETE FROM {table_name} WHERE ' + ' AND '.join(conditions) + ';\n-- 请确认回滚条件'
        return f'-- INSERT 无法自动确定回滚条件：DELETE FROM {table_name} WHERE <条件>;'

    match = re.match(r'^\s*INSERT\s+INTO\s+([^\s(]+)', orig_stmt, re.IGNORECASE)
    if match:
        return f'-- 无法确定列名：DELETE FROM {match.group(1)} WHERE <条件>;'

    update = _parse_update(orig_stmt)
    if update:
        table_ref, assignments, where_clause = update
        placeholders = []
        for assignment in _split_csv(assignments):
            if '=' in assignment:
                column = assignment.split('=', 1)[0].strip()
                placeholders.append(f'    {column} = <升级前原值>')
        where_sql = f'\nWHERE {where_clause}' if where_clause else ''
        warning = (
            '-- [必须人工补充] UPDATE 回滚无法从升级 SQL 推断原始值\n'
            '-- 请在生产升级前执行验证 SQL 中的“升级前原值留存”，并把结果填入下方占位符\n'
        )
        if not where_clause:
            warning += '-- [高风险] 原 UPDATE 没有 WHERE 条件，回滚可能影响全表\n'
        return warning + f'UPDATE {table_ref}\nSET\n' + ',\n'.join(placeholders) + where_sql + ';'

    delete = _parse_delete(orig_stmt)
    if delete:
        table_name, _, parsed_condition = delete
        condition = parsed_condition or '<原删除条件>'
        return (
            '-- [必须人工补充] DELETE 回滚需要升级前的完整原始记录\n'
            f'-- 原删除条件：{condition}\n'
            f'INSERT INTO {table_name} (<字段列表>) VALUES (<删除前原始值>);'
        )

    if re.match(r'^\s*COMMENT\s+ON\b', upper_stmt):
        return '-- COMMENT 语句通常无需回滚；如覆盖了旧注释，请在此补充原注释'

    if re.match(r'^\s*(COMMIT|SELECT)\b', upper_stmt):
        return '-- 此语句无需生成回滚'

    return f'-- [必须人工补充] 无法自动生成回滚\n-- 原语句：{orig_stmt.rstrip(";")}'


def _parse_update(statement):
    match = re.match(
        r'^\s*UPDATE\s+(.+?)\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?$',
        statement.strip().rstrip(';'), re.I | re.S,
    )
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip(), (match.group(3) or '').strip()


def _parse_delete(statement):
    match = re.match(
        r'^\s*DELETE\s+FROM\s+(\S+)(?:\s+((?!WHERE\b)\w+))?(?:\s+WHERE\s+(.+))?$',
        statement.strip().rstrip(';'), re.I | re.S,
    )
    if not match:
        return None
    table = match.group(1)
    table_ref = table + (f' {match.group(2)}' if match.group(2) else '')
    return table, table_ref, (match.group(3) or '').strip()


def _split_csv(text):
    parts = []
    start = 0
    depth = 0
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'" and in_string and index + 1 < len(text) and text[index + 1] == "'":
            index += 2
            continue
        if char == "'":
            in_string = not in_string
        elif not in_string:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            elif char == ',' and depth == 0:
                parts.append(text[start:index])
                start = index + 1
        index += 1
    parts.append(text[start:])
    return parts


def generate_reverse_sql(sql, direction='upgrade'):
    clean = strip_comments(sql)
    clean_stmts = split_statements(clean)
    results = []
    for statement in clean_stmts:
        if direction == 'upgrade':
            results.append(generate_rollback(statement, statement.upper()))
        else:
            results.append(f'-- [必须人工补充] 请根据回滚语句还原升级 SQL\n-- {statement}')
    return '\n\n'.join(results)


def _insert_condition(statement):
    match = re.match(
        r'^\s*INSERT\s+INTO\s+([^\s(]+)\s*\((.*?)\)\s*VALUES\s*\((.*)\)\s*$',
        statement, re.I | re.S,
    )
    if not match:
        return None
    table_name = match.group(1)
    columns = [item.strip() for item in _split_csv(match.group(2))]
    values = [item.strip() for item in _split_csv(match.group(3))]
    conditions = []
    for column, value in zip(columns, values):
        upper_value = value.upper()
        if upper_value == 'NULL':
            conditions.append(f'{column} IS NULL')
        elif re.match(r'^[A-Z_][A-Z0-9_$#]*\s*\(', upper_value) or upper_value in ('SYSDATE', 'SYSTIMESTAMP'):
            continue
        else:
            conditions.append(f'{column}={value}')
    return table_name, ' AND '.join(conditions)


def generate_verification_sql(sql):
    """生成独立验证脚本，并为 UPDATE/DELETE 提供升级前原值留存查询。"""
    statements = split_statements(strip_comments(sql))
    before, after = [], []
    for index, statement in enumerate(statements, 1):
        upper = statement.upper().strip()
        insert = _insert_condition(statement)
        if insert:
            table, condition = insert
            where = condition or '<请填写能唯一定位新增记录的条件>'
            after.append(f'-- [{index}] INSERT 执行后验证\nSELECT * FROM {table} WHERE {where};')
            continue
        update = _parse_update(statement)
        if update:
            table_ref, assignments, where = update
            columns = ', '.join(
                part.split('=', 1)[0].strip() for part in _split_csv(assignments) if '=' in part
            ) or '*'
            condition = where or '<原 UPDATE 无 WHERE，请人工限定范围>'
            before.append(
                f'-- [{index}] UPDATE 升级前原值留存（回滚必需，请保存查询结果）\n'
                f'SELECT {columns} FROM {table_ref} WHERE {condition};'
            )
            after.append(f'-- [{index}] UPDATE 执行后验证\nSELECT * FROM {table_ref} WHERE {condition};')
            continue
        delete = _parse_delete(statement)
        if delete:
            table, table_ref, parsed_condition = delete
            condition = parsed_condition or '<请填写删除条件>'
            before.append(f'-- [{index}] DELETE 升级前原记录留存（回滚必需，请保存查询结果）\nSELECT * FROM {table_ref} WHERE {condition};')
            after.append(f'-- [{index}] DELETE 执行后验证，预期返回 0 行\nSELECT * FROM {table_ref} WHERE {condition};')
            continue
        create_table = re.match(r'^\s*CREATE\s+TABLE\s+(\S+)', upper)
        if create_table:
            table = create_table.group(1).split('.')[-1].strip('"(')
            after.append(f"-- [{index}] CREATE TABLE 执行后验证\nSELECT table_name FROM user_tables WHERE table_name='{table}';")
            continue
        alter = re.match(r'^\s*ALTER\s+TABLE\s+(\S+)', upper)
        if alter:
            table = alter.group(1).split('.')[-1].strip('"')
            after.append(f"-- [{index}] ALTER TABLE 执行后验证\nSELECT column_name, data_type, data_length FROM user_tab_columns WHERE table_name='{table}' ORDER BY column_id;")
            continue
        if upper.startswith(('CREATE INDEX', 'CREATE UNIQUE INDEX')):
            match = re.match(r'^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(\S+)', upper)
            if match:
                index_name = match.group(1).split('.')[-1].strip('"')
                after.append(f"-- [{index}] CREATE INDEX 执行后验证\nSELECT index_name, status FROM user_indexes WHERE index_name='{index_name}';")
    sections = [
        '-- 重要：本文件用于生产执行前留存与执行后验证，不提交 SVN。',
        '-- UPDATE/DELETE 的原值无法由升级 SQL 推断，必须在升级前保存对应查询结果。',
    ]
    if before:
        sections.append('\n-- ==================== 升级前原值留存 ====================\n' + '\n\n'.join(before))
    sections.append('\n-- ==================== 生产执行后验证 ====================\n' + ('\n\n'.join(after) if after else '-- 请根据业务补充验证 SQL'))
    return '\n'.join(sections).rstrip() + '\n'


def validate_oracle_sql_detailed(sql):
    """轻量 Oracle DDL/DML 预检；只报告明确结构问题，不替代数据库编译。"""
    issues = []

    def add(stmt_num, severity, code, zh, en):
        issues.append({
            'statement': stmt_num, 'severity': severity, 'code': code,
            'message_zh': zh, 'message_en': en,
        })

    clean = strip_comments(sql)
    stmts = split_statements(clean)
    for i, stmt in enumerate(stmts):
        upper = stmt.upper()
        stmt_num = i + 1

        first = re.match(r'^\s*([A-Z]+)', upper)
        keyword = first.group(1) if first else ''
        supported = set(DDL_KEYWORDS + DML_KEYWORDS + ('COMMIT', 'ROLLBACK', 'GRANT', 'REVOKE', 'BEGIN', 'DECLARE'))
        if not keyword:
            add(stmt_num, 'error', 'missing_keyword', '未识别到 SQL 起始关键字', 'Missing SQL statement keyword')
        elif keyword not in supported:
            add(stmt_num, 'error', 'unknown_keyword', f'无法识别起始关键字 {keyword}', f'Unrecognized statement keyword {keyword}')
        elif keyword in ('BEGIN', 'DECLARE'):
            add(stmt_num, 'warning', 'plsql_limited', '检测到 PL/SQL 块，轻量检查无法完整验证过程语法', 'PL/SQL block detected; lightweight validation is limited')

        if re.match(r'^\s*INSERT\s', upper) and not re.search(r'\bINTO\b', upper):
            add(stmt_num, 'error', 'insert_into', 'INSERT 缺少 INTO 关键字', 'INSERT missing INTO keyword')
        elif re.match(r'^\s*INSERT\s', upper):
            if not re.search(r'\bINTO\s+[A-Z0-9_$#.]+', upper):
                add(stmt_num, 'error', 'insert_table', 'INSERT INTO 后缺少目标表名', 'INSERT INTO missing target table')
            if not re.search(r'\b(VALUES|SELECT|WITH)\b', upper):
                add(stmt_num, 'error', 'insert_source', 'INSERT 缺少 VALUES 或 SELECT 数据来源', 'INSERT missing VALUES or SELECT source')

        depth = 0
        in_str = False
        index = 0
        min_depth = 0
        while index < len(stmt):
            ch = stmt[index]
            if ch == "'":
                if in_str and index + 1 < len(stmt) and stmt[index + 1] == "'":
                    index += 2
                    continue
                in_str = not in_str
            elif not in_str:
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    min_depth = min(min_depth, depth)
            index += 1
        if depth != 0:
            add(stmt_num, 'error', 'parentheses', f'括号不匹配，差值 {depth}', f'Unmatched parentheses (diff:{depth})')
        elif min_depth < 0:
            add(stmt_num, 'error', 'parentheses_order', '右括号出现在对应左括号之前', 'Closing parenthesis appears before opening parenthesis')

        if in_str:
            add(stmt_num, 'error', 'quotes', '单引号字符串未闭合', 'Unmatched single quotes')

        if re.match(r'^\s*TRUNCATE\s', upper) and not re.match(r'^\s*TRUNCATE\s+TABLE', upper):
            add(stmt_num, 'error', 'truncate_table', 'TRUNCATE 后应包含 TABLE 关键字', 'TRUNCATE should include TABLE keyword')

        if re.match(r'^\s*(UPDATE|DELETE)\b', upper) and not re.search(r'\bWHERE\b', upper):
            add(stmt_num, 'warning', 'missing_where', 'UPDATE/DELETE 没有 WHERE 条件，请确认是否会影响全表', 'Dangerous operation without WHERE clause')

        if re.match(r'^\s*UPDATE\b', upper):
            if not re.search(r'\bUPDATE\s+[A-Z0-9_$#.]+', upper):
                add(stmt_num, 'error', 'update_table', 'UPDATE 后缺少目标表名', 'UPDATE missing target table')
            if not re.search(r'\bSET\b', upper):
                add(stmt_num, 'error', 'update_set', 'UPDATE 缺少 SET 子句', 'UPDATE missing SET clause')
        if re.match(r'^\s*DELETE\b', upper) and not re.match(r'^\s*DELETE\s+FROM\s+[A-Z0-9_$#.]+', upper):
            add(stmt_num, 'error', 'delete_from', 'DELETE 应写为 DELETE FROM 表名', 'DELETE missing FROM or target table')
        if re.match(r'^\s*CREATE\s+TABLE\b', upper):
            if not re.match(r'^\s*CREATE\s+TABLE\s+[A-Z0-9_$#.]+', upper):
                add(stmt_num, 'error', 'create_table_name', 'CREATE TABLE 后缺少表名', 'CREATE TABLE missing table name')
            elif '(' not in stmt and not re.search(r'\bAS\s+SELECT\b', upper):
                add(stmt_num, 'error', 'create_table_body', 'CREATE TABLE 缺少字段定义括号或 AS SELECT', 'CREATE TABLE missing column list or AS SELECT')
        if re.match(r'^\s*ALTER\s+TABLE\b', upper):
            if not re.match(r'^\s*ALTER\s+TABLE\s+[A-Z0-9_$#.]+', upper):
                add(stmt_num, 'error', 'alter_table_name', 'ALTER TABLE 后缺少表名', 'ALTER TABLE missing table name')
            elif not re.search(r'\b(ADD|MODIFY|DROP|RENAME|ENABLE|DISABLE)\b', upper):
                add(stmt_num, 'error', 'alter_action', 'ALTER TABLE 缺少 ADD、MODIFY 等操作关键字', 'ALTER TABLE missing action keyword')
        if re.match(r'^\s*MERGE\b', upper):
            for token in ('INTO', 'USING', 'ON', 'WHEN'):
                if not re.search(rf'\b{token}\b', upper):
                    add(stmt_num, 'error', 'merge_' + token.lower(), f'MERGE 缺少 {token} 子句', f'MERGE missing {token} clause')
        if re.match(r'^\s*COMMENT\s+ON\b', upper) and not re.search(r'\bIS\s+\'', upper):
            add(stmt_num, 'error', 'comment_is', 'COMMENT ON 缺少 IS 和说明字符串', 'COMMENT ON missing IS and comment string')
        if re.search(r',\s*\)', stmt):
            add(stmt_num, 'error', 'trailing_comma', '右括号前存在多余逗号', 'Trailing comma before closing parenthesis')
        if re.search(r',\s*,', stmt):
            add(stmt_num, 'error', 'double_comma', '检测到连续逗号，可能缺少字段或值', 'Consecutive commas may indicate a missing column or value')
        dialect_hits = []
        if '`' in stmt:
            dialect_hits.append('反引号')
        if re.search(r'\bLIMIT\s+\d+', upper):
            dialect_hits.append('LIMIT')
        if re.search(r'\bAUTO_INCREMENT\b|\bENGINE\s*=', upper):
            dialect_hits.append('MySQL 表选项')
        if dialect_hits:
            add(stmt_num, 'warning', 'dialect', '发现可能不是 Oracle 的语法：' + '、'.join(dialect_hits), 'Possible non-Oracle syntax: ' + ', '.join(dialect_hits))

    return issues


def validate_oracle_sql(sql):
    warnings = []
    for issue in validate_oracle_sql_detailed(sql):
        warnings.append(f"Stmt {issue['statement']}: {issue['message_en']}")

    return warnings


def generate_file_header(system, env):
    if env == system.get('sim_env_name', '模拟环境'):
        addr = system['sim_addr']
        sid = system['sim_sid']
        user = system['sim_user']
    else:
        addr = system['prod_addr']
        sid = system['prod_sid']
        user = system['prod_user']
    lines = [f'---- 地址：{addr}', f'---- sid： {sid}', f'---- 用户名：{user}', '']
    return '\n'.join(lines) + '\n'


def get_output_path(system, env, sql_type, category, date_str):
    template = system.get('delivery_template', '{日期}/{环境}/{分类}/{系统目录}/{SQL类型}')
    return _format_path(template, system, env, sql_type, category, date_str)


def _format_path(template, system, env, sql_type, category, date_str):
    values = {
        '{日期}': date_str, '{date}': date_str,
        '{环境}': env, '{env}': env,
        '{分类}': category.upper(), '{category}': category.upper(),
        '{系统目录}': system.get('system_folder', system['name']),
        '{system_folder}': system.get('system_folder', system['name']),
        '{SQL类型}': sql_type, '{type}': sql_type,
    }
    path = template
    for key, value in values.items():
        path = path.replace(key, value)
    return path.replace('/', os.sep).replace('\\\\', os.sep).strip('/\\\\')


def build_sql_package(sql, system, env, date_str):
    """按 20260629 SVN 样本构建升级、回滚及独立验证文件计划。"""
    sql = strip_connection_header(sql)
    sql, _ = deduplicate_sql_statements(sql)
    split = split_mixed_sql(sql)
    title = system.get('sql_title', system['name'])
    author = system.get('script_author', '李浩鹏')
    artifacts = []
    for category, category_sql in (('DDL', split['ddl']), ('DML', split['dml'])):
        if not category_sql.strip():
            continue
        upgrade_name = f'{author}-【{title}】升级SQL.sql'
        rollback_name = f'{author}-【{title}】回滚SQL.sql'
        upgrade_dir = get_output_path(system, env, '升级SQL', category, date_str)
        rollback_dir = get_output_path(system, env, '回滚SQL', category, date_str)
        artifacts.extend([
            {
                'kind': 'upgrade', 'category': category,
                'relative_path': os.path.join(upgrade_dir, upgrade_name),
                'content': generate_file_header(system, env) + category_sql.rstrip() + '\n',
            },
            {
                'kind': 'rollback', 'category': category,
                'relative_path': os.path.join(rollback_dir, rollback_name),
                'content': generate_file_header(system, env) + generate_reverse_sql(category_sql).rstrip() + '\n',
            },
        ])
    validation_template = system.get('validation_template', '{日期}/验证SQL/{系统目录}')
    validation_dir = _format_path(validation_template, system, '生产环境', '验证SQL', '验证', date_str)
    artifacts.append({
        'kind': 'validation', 'category': 'VALIDATION',
        'relative_path': os.path.join(validation_dir, f'{author}-【{title}】验证SQL.sql'),
        'content': generate_file_header(system, system.get('prod_env_name', '生产环境')) + generate_verification_sql(sql),
    })
    return artifacts


def export_sql_package(output_root, sql, system, env, date_str):
    written = []
    for artifact in build_sql_package(sql, system, env, date_str):
        path = os.path.join(output_root, artifact['relative_path'])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8-sig', newline='\n') as stream:
            stream.write(artifact['content'])
        written.append(path)
    return written


def read_file_auto_encoding(file_path):
    encodings = ['utf-8', 'gbk', 'utf-16']
    for enc in encodings:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                return f.read()
        except Exception:
            continue
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()
