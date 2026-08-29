# -*- coding: utf-8 -*-
"""PTools Harness：Linux 只读门禁。查询/查看允许，删除/改系统拒绝。"""

from __future__ import annotations

import re
import shlex

ALLOWED_BINARIES = frozenset({
    'grep', 'egrep', 'fgrep', 'rg',
    'tail', 'head', 'less', 'more',
    'cat', 'zcat', 'zless', 'bzcat',
    'ls', 'stat', 'wc', 'file',
    'date', 'hostname', 'uname', 'uptime',
    'df', 'free', 'ps', 'id', 'who', 'whoami', 'pwd',
    'echo', 'printf',
})

_DENIED_BINARIES = frozenset({
    'rm', 'rmdir', 'mv', 'cp', 'dd', 'mkfs', 'fdisk',
    'reboot', 'shutdown', 'halt', 'poweroff', 'init',
    'kill', 'killall', 'pkill', 'xkill',
    'chmod', 'chown', 'chgrp', 'chattr',
    'sudo', 'su', 'doas',
    'tee', 'dd',
    'systemctl', 'service', 'mount', 'umount',
    'iptables', 'firewall-cmd',
    'useradd', 'userdel', 'passwd',
    'crontab', 'at',
    'python', 'python3', 'perl', 'ruby', 'node', 'bash', 'sh', 'zsh', 'ksh',
    'find', 'xargs', 'awk', 'sed',
})

_DENIED_PATTERN = re.compile(
    r'(?:^|[;&|]\s*)(?:rm|reboot|shutdown|kill|dd|chmod|chown|mkfs|sudo)\b'
    r'|(?:>>|>\s*[^&\s])'
    r'|\|\s*(?:sh|bash|zsh|ksh|python|perl)\b',
    re.IGNORECASE,
)
# $()、反引号、<()、>() 只在远端 shell 解析时才展开，静态白名单检查不到内部命令，必须整体拒绝
_SUBSTITUTION_PATTERN = re.compile(r'\$\(|`|[<>]\(')


class LinuxGuardError(Exception):
    """命令未通过只读门禁。"""


def _first_binary(segment: str) -> str:
    text = str(segment or '').strip()
    if not text:
        return ''
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:
        parts = text.split()
    if not parts:
        return ''
    name = parts[0]
    if '/' in name:
        name = name.rsplit('/', 1)[-1]
    return name.lower()


def split_pipeline(command: str) -> list[str]:
    raw = str(command or '').strip()
    if not raw:
        return []
    chunks = re.split(r'\s*(?:&&|\|\||;|\n)+\s*', raw)
    segments = []
    for chunk in chunks:
        if not chunk.strip():
            continue
        for piece in chunk.split('|'):
            piece = piece.strip()
            if piece:
                segments.append(piece)
    return segments


def inspect_command(command: str) -> tuple[bool, str]:
    """返回 (允许?, 原因)。"""
    text = str(command or '').strip()
    if not text:
        return False, '空命令'
    if text.startswith('#'):
        return False, '注释不是可执行查询'
    if _SUBSTITUTION_PATTERN.search(text):
        return False, '含命令替换/进程替换，无法静态审查'
    if _DENIED_PATTERN.search(text):
        return False, '含重定向、管道到解释器或危险命令'
    segments = split_pipeline(text)
    if not segments:
        return False, '无法解析命令'
    for segment in segments:
        binary = _first_binary(segment)
        if not binary:
            return False, f'无法识别命令：{segment[:80]}'
        if binary in _DENIED_BINARIES:
            return False, f'禁止：{binary}'
        if binary not in ALLOWED_BINARIES:
            return False, f'不在只读白名单：{binary}'
    return True, ''


def inspect_commands(commands) -> tuple[list[str], list[tuple[str, str]]]:
    allowed: list[str] = []
    rejected: list[tuple[str, str]] = []
    for item in commands or []:
        cmd = str(item or '').strip()
        if not cmd:
            continue
        ok, reason = inspect_command(cmd)
        if ok:
            allowed.append(cmd)
        else:
            rejected.append((cmd, reason))
    return allowed, rejected
