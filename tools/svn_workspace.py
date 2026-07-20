# -*- coding: utf-8 -*-
import calendar
import datetime
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import urlparse


class SvnError(RuntimeError):
    pass


def svn_binary():
    path = shutil.which('svn')
    if not path:
        raise SvnError('未找到 SVN 命令行。请先安装 TortoiseSVN 并勾选 command line client tools。')
    return path


def _decode_output(data):
    for encoding in ('utf-8', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def run_svn(arguments, cwd=None, check=True, timeout=300):
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = subprocess.run(
        [svn_binary(), '--non-interactive', *arguments],
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=flags, timeout=timeout,
    )
    output = _decode_output(result.stdout).strip()
    if check and result.returncode:
        raise SvnError(output or f'SVN 命令失败，退出码 {result.returncode}')
    return {'returncode': result.returncode, 'output': output}


def validate_svn_url(url):
    value = str(url or '').strip()
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https', 'svn', 'svn+ssh', 'file'):
        raise ValueError('请输入 http、https、svn、svn+ssh 或 file 开头的 SVN 地址。')
    return value.rstrip('/')


def working_copy_info(path):
    target = os.path.abspath(path)
    result = run_svn(['info', '--xml', target])
    try:
        entry = ET.fromstring(result['output']).find('entry')
        repository = entry.find('repository')
        commit = entry.find('commit')
        return {
            'local_path': target,
            'svn_url': entry.findtext('url', ''),
            'svn_root': repository.findtext('root', '') if repository is not None else '',
            'svn_uuid': repository.findtext('uuid', '') if repository is not None else '',
            'svn_revision': entry.get('revision', ''),
            'last_changed_revision': commit.get('revision', '') if commit is not None else '',
        }
    except (ET.ParseError, AttributeError) as exc:
        raise SvnError(f'无法解析 SVN 工作副本信息：{target}') from exc


def svn_status(path):
    result = run_svn(['status', path], check=False)
    lines = [line.rstrip() for line in result['output'].splitlines() if line.strip()]
    return {
        'clean': result['returncode'] == 0 and not lines,
        'changes': lines,
        'text': '\n'.join(lines) if lines else '工作副本干净，无本地改动',
        'returncode': result['returncode'],
    }


MONTH_PATTERNS = (
    re.compile(r'(?:(20\d{2})[年._/-]?)?(0?[1-9]|1[0-2])月', re.I),
    re.compile(r'(?<!\d)(20\d{2})[._/-](0?[1-9]|1[0-2])(?!\d)'),
)

CHINESE_MONTHS = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6,
    '七': 7, '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12,
}


def infer_online_month(path, default_year=None):
    default_year = default_year or datetime.date.today().year
    parts = list(reversed(os.path.normpath(path).split(os.sep)))
    for part in parts:
        chinese = re.search(r'(?:(20\d{2})年?)?(十一|十二|十|[一二三四五六七八九])月', part)
        if chinese:
            year = int(chinese.group(1) or default_year)
            return f'{year:04d}-{CHINESE_MONTHS[chinese.group(2)]:02d}'
        for pattern in MONTH_PATTERNS:
            match = pattern.search(part)
            if match:
                year = int(match.group(1) or default_year)
                month = int(match.group(2))
                return f'{year:04d}-{month:02d}'
    return ''


def month_end_date(month_text):
    if not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])', str(month_text or '')):
        return ''
    year, month = map(int, month_text.split('-'))
    return f'{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}'


def infer_record_kind(text):
    return 'BUG' if re.search(r'(?i)(?:\bBUG\b|\bDEF[-_]|缺陷|问题单)', str(text or '')) else '需求'


def folder_file_snapshot(path, ignored=None):
    ignored = ignored or set()
    file_count = 0
    latest_mtime = 0.0
    for current, directories, files in os.walk(path):
        directories[:] = [name for name in directories if name not in ignored and name != '.svn']
        for name in files:
            if name.startswith('~$'):
                continue
            try:
                latest_mtime = max(latest_mtime, os.path.getmtime(os.path.join(current, name)))
                file_count += 1
            except OSError:
                pass
    modified_at = datetime.datetime.fromtimestamp(latest_mtime).isoformat(timespec='seconds') if latest_mtime else ''
    return file_count, modified_at


def scan_working_copies(root):
    base = os.path.abspath(root)
    if not os.path.isdir(base):
        raise ValueError('选择的需求根目录不存在。')
    root_year_match = re.search(r'(20\d{2})', os.path.basename(base))
    default_year = int(root_year_match.group(1)) if root_year_match else datetime.date.today().year
    copies = []
    svn_paths = set()
    ignored = {'.git', '__pycache__', 'node_modules', 'build', 'dist'}
    for current, directories, _files in os.walk(base):
        if '.svn' in directories:
            svn_paths.add(os.path.normcase(os.path.abspath(current)))
            file_count, modified_at = folder_file_snapshot(current, ignored)
            try:
                info = working_copy_info(current)
                status = svn_status(current)
                relative = os.path.relpath(current, base)
                info.update({
                    'relative_path': relative,
                    'online_month': infer_online_month(relative, default_year),
                    'record_kind': infer_record_kind(relative),
                    'svn_status': status['text'],
                    'changed_count': len(status['changes']),
                    'workspace_kind': 'svn',
                    'file_count': file_count, 'source_modified_at': modified_at,
                })
                copies.append(info)
            except SvnError as exc:
                copies.append({
                    'local_path': current, 'relative_path': os.path.relpath(current, base),
                    'svn_url': '', 'svn_revision': '', 'online_month': infer_online_month(current, default_year),
                    'record_kind': infer_record_kind(current), 'svn_status': str(exc),
                    'changed_count': 0, 'error': str(exc), 'workspace_kind': 'svn',
                    'file_count': file_count, 'source_modified_at': modified_at,
                })
        directories[:] = [name for name in directories if name not in ignored and name != '.svn']

    for month_entry in os.scandir(base):
        if not month_entry.is_dir() or month_entry.name in ignored:
            continue
        month = infer_online_month(month_entry.name, default_year)
        if not month:
            continue
        candidates = [entry for entry in os.scandir(month_entry.path) if entry.is_dir() and entry.name not in ignored]
        if not candidates:
            candidates = [month_entry]
        for candidate in candidates:
            candidate_path = os.path.abspath(candidate.path)
            normalized = os.path.normcase(candidate_path)
            if any(
                normalized == svn_path or normalized.startswith(svn_path + os.sep) or svn_path.startswith(normalized + os.sep)
                for svn_path in svn_paths
            ):
                continue
            file_count, modified_at = folder_file_snapshot(candidate_path, ignored)
            if not file_count:
                continue
            relative = os.path.relpath(candidate_path, base)
            copies.append({
                'local_path': candidate_path, 'relative_path': relative,
                'svn_url': '', 'svn_revision': '', 'online_month': month,
                'record_kind': infer_record_kind(relative),
                'svn_status': '本地需求文件夹（未关联 SVN）',
                'changed_count': 0, 'workspace_kind': 'folder', 'file_count': file_count,
                'source_modified_at': modified_at,
            })
    return sorted(
        copies,
        key=lambda item: (item.get('online_month', ''), item.get('source_modified_at', ''), item.get('relative_path', '')),
        reverse=True,
    )


def checkout(url, target_path):
    clean_url = validate_svn_url(url)
    target = os.path.abspath(target_path)
    if os.path.exists(target) and os.listdir(target):
        raise ValueError('目标目录已存在且不为空，请更换需求名称或目录。')
    os.makedirs(os.path.dirname(target), exist_ok=True)
    result = run_svn(['checkout', clean_url, target], timeout=900)
    info = working_copy_info(target)
    info['output'] = result['output']
    return info


def update_working_copy(path):
    result = run_svn(['update', os.path.abspath(path)], timeout=900)
    info = working_copy_info(path)
    info.update({'output': result['output'], 'svn_status': svn_status(path)['text']})
    return info


def update_many(paths):
    results = []
    for path in paths:
        try:
            results.append({'path': path, 'ok': True, **update_working_copy(path)})
        except (OSError, ValueError, SvnError, subprocess.TimeoutExpired) as exc:
            results.append({'path': path, 'ok': False, 'error': str(exc)})
    return results


def safe_relative_target(root, relative_path):
    base = os.path.abspath(root)
    target = os.path.abspath(os.path.join(base, str(relative_path or '').strip()))
    if os.path.commonpath((base, target)) != base or target == base:
        raise ValueError('文件必须位于当前 SVN 工作副本内。')
    return target


def add_text_file(root, relative_path, content):
    target = safe_relative_target(root, relative_path)
    if os.path.exists(target):
        raise ValueError('文件已存在，请换一个文件名。')
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'w', encoding='utf-8') as stream:
        stream.write(str(content or ''))
    try:
        run_svn(['add', '--parents', target])
    except Exception:
        try:
            os.remove(target)
        except OSError:
            pass
        raise
    return target


def add_existing_files(root, source_paths, relative_folder=''):
    destination = os.path.abspath(root) if not relative_folder else safe_relative_target(root, relative_folder)
    os.makedirs(destination, exist_ok=True)
    copied = []
    for source in source_paths:
        if not os.path.isfile(source):
            continue
        target = safe_relative_target(root, os.path.relpath(os.path.join(destination, os.path.basename(source)), root))
        if os.path.exists(target):
            raise ValueError(f'目标中已存在同名文件：{os.path.basename(source)}')
        shutil.copy2(source, target)
        copied.append(target)
    if not copied:
        raise ValueError('没有可添加的文件。')
    try:
        run_svn(['add', '--parents', '--force', *copied])
    except Exception:
        for target in copied:
            try:
                os.remove(target)
            except OSError:
                pass
        raise
    return copied


def commit_working_copy(path, message):
    text = str(message or '').strip()
    if not text:
        raise ValueError('提交说明不能为空。')
    status = svn_status(path)
    if status['clean']:
        raise ValueError('工作副本没有可提交的改动。')
    result = run_svn(['commit', os.path.abspath(path), '-m', text], timeout=900)
    info = working_copy_info(path)
    info.update({'output': result['output'], 'svn_status': svn_status(path)['text']})
    return info


def lock_file(path, message='PengTools 开发锁定'):
    target = os.path.abspath(path)
    if not os.path.isfile(target):
        raise ValueError('SVN 只能锁定文件，请在文件树中选择一个文件。')
    result = run_svn(['lock', target, '-m', str(message or 'PengTools 开发锁定').strip()])
    return {'path': target, 'output': result['output']}


def unlock_file(path):
    target = os.path.abspath(path)
    if not os.path.isfile(target):
        raise ValueError('SVN 只能解锁文件，请在文件树中选择一个文件。')
    result = run_svn(['unlock', target])
    return {'path': target, 'output': result['output']}


def _file_size_text(size):
    value = float(size or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{int(value)} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


def _workspace_entry(path, base, is_dir):
    try:
        stat = os.stat(path)
        modified_at = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
        size = '--' if is_dir else _file_size_text(stat.st_size)
    except OSError:
        modified_at, size = '', '--' if is_dir else ''
    extension = os.path.splitext(path)[1].lower()
    file_type = '文件夹' if is_dir else {
        '.sql': 'SQL 脚本', '.xlsx': 'Excel 工作簿', '.xls': 'Excel 工作簿',
        '.docx': 'Word 文档', '.doc': 'Word 文档', '.txt': '文本文件',
        '.md': 'Markdown', '.pdf': 'PDF 文档', '.json': 'JSON 文件',
        '.xml': 'XML 文件', '.csv': 'CSV 文件', '.zip': '压缩文件',
    }.get(extension, f'{extension[1:].upper()} 文件' if extension else '文件')
    return {
        'path': path, 'relative_path': os.path.relpath(path, base), 'is_dir': is_dir,
        'modified_at': modified_at, 'file_type': file_type, 'size': size,
    }


def workspace_files(root, limit=None):
    base = os.path.abspath(root)
    if not os.path.isdir(base):
        return []
    result = []
    for current, directories, files in os.walk(base):
        directories[:] = [name for name in directories if name != '.svn']
        for name in sorted(directories):
            path = os.path.join(current, name)
            result.append(_workspace_entry(path, base, True))
        for name in sorted(files):
            path = os.path.join(current, name)
            result.append(_workspace_entry(path, base, False))
        if limit and len(result) >= limit:
            break
    return result[:limit] if limit else result


def safe_folder_name(value):
    name = re.sub(r'[\\/:*?"<>|]+', '_', str(value or '').strip()).strip(' .')
    return name[:80] or '未命名需求'
