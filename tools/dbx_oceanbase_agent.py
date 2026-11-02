# -*- coding: utf-8 -*-
"""OceanBase Oracle 模式的 DBX Java Agent 客户端。

进程用 stdin/stdout 逐行 JSON-RPC 2.0。密码只进 RPC 参数，不进命令行。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from pathlib import Path

MAIN_CLASS = 'com.dbx.agent.oceanbaseoracle.OceanBaseOracleAgent'
DESKTOP_JAR_NAME = 'dbx-agent-oceanbase-oracle-0.1.53.jar'


class AgentError(RuntimeError):
    pass


def configured_agent_paths() -> tuple[str, str]:
    """返回已保存的 (jar, java)。未保存时不猜测路径，避免测试机上的桌面文件改变连接方式。"""
    jar = ''
    java_bin = ''
    try:
        from config import load_settings
        settings = load_settings()
        jar = str(settings.get('oceanbase_agent_jar') or '').strip()
        java_bin = str(settings.get('oceanbase_java_bin') or '').strip()
    except Exception:
        pass
    return jar, java_bin


def suggested_agent_jar() -> str:
    jar, _java_bin = configured_agent_paths()
    if jar:
        return jar
    desktop = Path.home() / 'Desktop' / DESKTOP_JAR_NAME
    if desktop.is_file():
        return str(desktop)
    return ''


def agent_jar_ready(jar: str | None = None) -> bool:
    path = jar if jar is not None else configured_agent_paths()[0]
    return bool(path) and os.path.isfile(path) and path.lower().endswith('.jar')


def java_agent_usable() -> bool:
    """只有 JAR 和 Java 21 都在时才走 Java Agent。否则让连接回退到 ODBC。"""
    jar, java_bin = configured_agent_paths()
    if not agent_jar_ready(jar):
        return False
    major = java_major(java_bin)
    return major is not None and major >= 21


def java_major(java_bin: str) -> int | None:
    command = java_bin or 'java'
    try:
        completed = subprocess.run(
            [command, '-version'],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = f'{completed.stderr}\n{completed.stdout}'
    for token in text.replace('"', ' ').split():
        if token[:1].isdigit() and '.' in token:
            head = token.split('.', 1)[0]
            if head.isdigit():
                major = int(head)
                return major if major >= 9 else int(token.split('.')[1] or 0)
    return None


def describe_agent_status(jar: str = '', java_bin: str = '') -> str:
    saved_jar, saved_java = configured_agent_paths()
    jar = jar or saved_jar or suggested_agent_jar()
    java_bin = java_bin or saved_java
    if not jar:
        return '未导入 OceanBase Java Agent。可在设置中选择 dbx-agent-oceanbase-oracle 的 JAR。'
    if not os.path.isfile(jar):
        return f'找不到 JAR：{jar}'
    major = java_major(java_bin)
    if major is None:
        return '找不到 Java。请在设置中选择 Java 21 或更高版本的 java.exe。'
    if major < 21:
        return f'当前 Java 是 {major}，这个 JAR 需要 Java 21 或更高版本。'
    return f'将使用 Java Agent（Java {major}）：{os.path.basename(jar)}'


class OceanBaseAgentConnection:
    """把 DBX Agent 的 execute_query 收成连接和游标。"""

    def __init__(self, process: subprocess.Popen, jar_name: str):
        self._process = process
        self.driver_name = f'DBX Java Agent ({jar_name})'
        self._driver_name = self.driver_name
        self._queue: queue.Queue[str] = queue.Queue()
        self._next_id = 1
        self._lock = threading.Lock()
        self._stderr: list[str] = []
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._err_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._err_reader.start()
        ready = self._queue.get(timeout=20)
        if 'ready' not in ready:
            self.close()
            raise AgentError(f'Java Agent 没有就绪：{ready.strip()[:200]}')
        self._call('handshake', {}, timeout=20)

    def cursor(self):
        return OceanBaseAgentCursor(self)

    def commit(self):
        self._call('execute_query', {'sql': 'COMMIT', 'maxRows': 1, 'timeoutSecs': 30}, timeout=40)

    def rollback(self):
        self._call('execute_query', {'sql': 'ROLLBACK', 'maxRows': 1, 'timeoutSecs': 30}, timeout=40)

    def close(self):
        try:
            self._call('disconnect', {}, timeout=5)
        except Exception:
            pass
        try:
            self._call('shutdown', {}, timeout=5)
        except Exception:
            pass
        process = self._process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def connect(self, item: dict, password: str) -> None:
        port = int(item.get('port') or 2881)
        self._call('connect', {
            'host': str(item.get('host') or '').strip(),
            'port': port,
            'database': str(item.get('database') or '').strip(),
            'username': str(item.get('username') or '').strip(),
            'password': password,
            'port_explicit': True,
        }, timeout=40)

    def query(self, sql: str, *, schema: str = '', max_rows: int = 1000, timeout_secs: int = 60) -> dict:
        result = self._call('execute_query', {
            'sql': sql,
            'schema': schema,
            'maxRows': max_rows,
            'fetchSize': min(max_rows, 500),
            'timeoutSecs': timeout_secs,
        }, timeout=timeout_secs + 15)
        return result if isinstance(result, dict) else {'rows': result or []}

    def _call(self, method: str, params: dict, *, timeout: float) -> object:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            payload = {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}
            try:
                self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
                self._process.stdin.flush()
            except Exception as exc:
                raise AgentError(f'Java Agent 已退出（{type(exc).__name__}）') from exc
            try:
                line = self._queue.get(timeout=timeout)
            except queue.Empty as exc:
                raise AgentError(f'Java Agent 等待 {method} 超时') from exc
        try:
            message = json.loads(line)
        except ValueError as exc:
            raise AgentError('Java Agent 返回的不是 JSON') from exc
        if isinstance(message, dict) and message.get('error'):
            error = message['error']
            text = error.get('message') if isinstance(error, dict) else str(error)
            raise AgentError(str(text or 'Java Agent 调用失败')[:500])
        if isinstance(message, dict) and 'result' in message:
            return message['result']
        return message

    def _read_stdout(self) -> None:
        stream = self._process.stdout
        if stream is None:
            return
        for line in stream:
            self._queue.put(line)

    def _read_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        for line in stream:
            if len(self._stderr) < 20:
                self._stderr.append(line.rstrip())


class OceanBaseAgentCursor:
    def __init__(self, connection: OceanBaseAgentConnection):
        self._connection = connection
        self.description = None
        self.rowcount = -1
        self._rows: list = []
        self._index = 0

    def execute(self, sql, params=None):
        if params:
            raise AgentError('Java Agent 这条查询通道没有绑定参数，请把条件写进 SQL。')
        result = self._connection.query(str(sql or ''))
        columns = [str(name) for name in (result.get('columns') or [])]
        self.description = tuple((name, None, None, None, None, None, None) for name in columns) or None
        self._rows = list(result.get('rows') or [])
        self._index = 0
        affected = result.get('affected_rows')
        self.rowcount = int(affected) if isinstance(affected, int) else len(self._rows)
        return self

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return tuple(row) if isinstance(row, list) else row

    def fetchmany(self, size=None):
        count = int(size or 1)
        rows = []
        for _ in range(count):
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows

    def fetchall(self):
        rows = []
        while True:
            row = self.fetchone()
            if row is None:
                return rows
            rows.append(row)

    def close(self):
        self._rows = []
        self.description = None


def open_agent_connection(item: dict, password: str) -> OceanBaseAgentConnection:
    jar, java_bin = configured_agent_paths()
    if not agent_jar_ready(jar):
        raise AgentError(describe_agent_status(jar, java_bin))
    major = java_major(java_bin)
    if major is None or major < 21:
        raise AgentError(describe_agent_status(jar, java_bin))
    command = [java_bin or 'java', '-jar', jar]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )
    except OSError as exc:
        raise AgentError(f'无法启动 Java Agent：{exc}') from exc
    connection = OceanBaseAgentConnection(process, os.path.basename(jar))
    try:
        connection.connect(item, password)
    except Exception:
        connection.close()
        raise
    return connection
