# -*- coding: utf-8 -*-
"""发布前敏感信息扫描：阻止把账密/Token/VPN 等打进安装包。

用法（在 PengToolsV4 根目录）:
  python scripts/scan_release_secrets.py
  python scripts/scan_release_secrets.py --strict

退出码: 0 通过；1 发现高危；2 参数/IO 错误
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# 扫描范围：会进入安装包或常被误提交的路径
DEFAULT_SCAN_ROOTS = (
    'resources',
    'Installer',
    'PrivateInstaller',
    'packaging',
)

# 明确允许的假数据/说明（命中后仍可通过）
ALLOWLIST_SNIPPETS = (
    'RFC5737',
    '192.0.2.',
    'demo_user',
    'example.com',
    'localhost',
    '127.0.0.1',
    '安全空模板',
    '假数据',
    '故意不含任何真实',
)

# 高危模式：宁严勿松（发布阻断）
HIGH_RISK_PATTERNS = [
    (r'(?i)password\s*[:=]\s*\S{4,}', '疑似 password= 赋值'),
    (r'(?i)passwd\s*[:=]\s*\S{4,}', '疑似 passwd= 赋值'),
    (r'密码\s*[：:=]\s*\S{4,}', '疑似「密码:」明文'),
    (r'VPN密码\s*[：:=]', '疑似 VPN 密码字段'),
    (r'堡垒机密码', '疑似堡垒机密码字段'),
    (r'(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.', '疑似 JWT Bearer'),
    (r'eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.', '疑似 JWT 三段式'),
    (r'jdbc:oracle:thin:@', '疑似 Oracle JDBC 连接串'),
    (r'(?i)(BEGIN (RSA |OPENSSH )?PRIVATE KEY)', '疑似私钥 PEM'),
    # 常见内网密码风格（字母数字符号混排 10+）紧跟在「密码」后已覆盖；再拦高熵口令样例
    (r'环境清单密码', '疑似环境清单密码'),
    (r'服务器密码', '疑似服务器密码字段'),
    (r'内网生产服务器密码', '疑似生产服务器密码'),
]

# 仅警告、默认不阻断（可用 --strict 升级为失败）
WARN_PATTERNS = [
    (r'10\.(?:\d{1,3}\.){2}\d{1,3}', '内网 10.x 地址（确认非真实机密上下文）'),
    (r'20\.(?:\d{1,3}\.){2}\d{1,3}', '内网 20.x 地址（确认非真实机密上下文）'),
    (r'(?i)vpn', '出现 VPN 字样'),
    (r'堡垒机', '出现堡垒机字样'),
]

SKIP_DIR_NAMES = {
    '.git', '__pycache__', 'node_modules', '.codex_work', 'build', 'dist',
    '.venv', 'venv',
}
SKIP_FILE_SUFFIX = {
    '.pyc', '.pyo', '.exe', '.dll', '.png', '.ico', '.jpg', '.jpeg', '.gif',
    '.zip', '.7z', '.pdf', '.docx', '.xlsx', '.woff', '.woff2', '.ttf',
}
TEXT_SUFFIX = {
    '.txt', '.md', '.json', '.jsonl', '.csv', '.xml', '.yaml', '.yml',
    '.py', '.ps1', '.cmd', '.bat', '.qss', '.html', '.htm', '.ini', '.cfg',
    '.env', '.spec', '.toml',
}


def _is_allowlisted(text: str) -> bool:
    return any(s in text for s in ALLOWLIST_SNIPPETS)


def _iter_files(roots: list[str], project_dir: str):
    for root_rel in roots:
        root = os.path.join(project_dir, root_rel)
        if not os.path.isdir(root):
            # 也允许单文件
            if os.path.isfile(root):
                yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith('.')]
            for name in filenames:
                path = os.path.join(dirpath, name)
                ext = os.path.splitext(name)[1].lower()
                if ext in SKIP_FILE_SUFFIX:
                    continue
                # 无后缀小文件也扫；大二进制跳过
                if ext and ext not in TEXT_SUFFIX:
                    # 仍扫 .txt 类外的可疑 seed 命名
                    if 'seed' not in name.lower() and 'secret' not in name.lower() and 'password' not in name.lower():
                        continue
                yield path


def _read_text(path: str, limit: int = 2_000_000) -> str | None:
    try:
        size = os.path.getsize(path)
        if size > limit:
            return None
        with open(path, 'rb') as stream:
            raw = stream.read()
        if b'\x00' in raw[:4096]:
            return None
        for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', errors='replace')
    except OSError:
        return None


def scan(project_dir: str, roots: list[str], strict: bool = False) -> int:
    high = []
    warns = []
    for path in _iter_files(roots, project_dir):
        text = _read_text(path)
        if not text:
            continue
        rel = os.path.relpath(path, project_dir)
        # 安全空模板自身允许说明性文字
        if _is_allowlisted(text) and os.path.basename(path).startswith('private_knowledge_seed'):
            # 仍检查是否又混入 JWT 等
            for pattern, label in HIGH_RISK_PATTERNS:
                if re.search(pattern, text):
                    # 模板里不应出现 JWT/私钥
                    if 'JWT' in label or '私钥' in label or 'Bearer' in label or 'JDBC' in label:
                        high.append((rel, label, _sample(text, pattern)))
            continue
        for pattern, label in HIGH_RISK_PATTERNS:
            if re.search(pattern, text):
                high.append((rel, label, _sample(text, pattern)))
        for pattern, label in WARN_PATTERNS:
            if re.search(pattern, text):
                warns.append((rel, label, _sample(text, pattern)))

    print(f'[scan_release_secrets] project={project_dir}')
    print(f'[scan_release_secrets] roots={roots}')
    if high:
        print(f'[scan_release_secrets] HIGH RISK: {len(high)}')
        for rel, label, sample in high[:40]:
            print(f'  ! {rel}: {label} :: {sample}')
        if len(high) > 40:
            print(f'  ... and {len(high) - 40} more')
    else:
        print('[scan_release_secrets] HIGH RISK: 0')
    if warns:
        print(f'[scan_release_secrets] WARN: {len(warns)} (sample up to 20)')
        for rel, label, sample in warns[:20]:
            print(f'  ? {rel}: {label} :: {sample}')
    else:
        print('[scan_release_secrets] WARN: 0')

    if high:
        print('[scan_release_secrets] FAIL — 请移除敏感内容后再打包')
        return 1
    if strict and warns:
        print('[scan_release_secrets] FAIL (strict) — 存在警告项')
        return 1
    print('[scan_release_secrets] PASS')
    return 0


def _sample(text: str, pattern: str, width: int = 80) -> str:
    m = re.search(pattern, text)
    if not m:
        return ''
    start = max(0, m.start() - 10)
    end = min(len(text), m.end() + 30)
    snippet = text[start:end].replace('\n', ' ').replace('\r', ' ')
    # 打码：连续高熵串
    snippet = re.sub(r'([A-Za-z0-9_\-@#$%^&*]{8,})', '***', snippet)
    if len(snippet) > width:
        snippet = snippet[:width] + '…'
    return snippet


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='PengTools release secret scanner')
    parser.add_argument('--project', default='', help='project root (default: parent of scripts/)')
    parser.add_argument('--strict', action='store_true', help='treat warnings as failures')
    parser.add_argument(
        '--root', action='append', dest='roots', default=None,
        help='extra/override scan root relative to project (repeatable)',
    )
    args = parser.parse_args(argv)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = args.project or os.path.dirname(script_dir)
    roots = args.roots if args.roots else list(DEFAULT_SCAN_ROOTS)
    if not os.path.isdir(project_dir):
        print(f'project not found: {project_dir}', file=sys.stderr)
        return 2
    return scan(project_dir, roots, strict=bool(args.strict))


if __name__ == '__main__':
    raise SystemExit(main())
