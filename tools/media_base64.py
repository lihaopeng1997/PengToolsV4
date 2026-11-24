# -*- coding: utf-8 -*-
"""纯离线图片 / PDF 与 Base64 双向转换逻辑（零 Qt 依赖）。"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import os
import re

# 单文件体积上限：避免误选超大视频/安装包撑爆内存与剪贴板
MAX_MEDIA_FILE_BYTES = 25 * 1024 * 1024  # 25 MiB

EXTENSION_TO_MIME: dict[str, str] = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.bmp': 'image/bmp',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.tif': 'image/tiff',
    '.tiff': 'image/tiff',
    '.pdf': 'application/pdf',
}

MIME_TO_EXTENSION: dict[str, str] = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/webp': '.webp',
    'image/gif': '.gif',
    'image/bmp': '.bmp',
    'image/svg+xml': '.svg',
    'image/x-icon': '.ico',
    'image/vnd.microsoft.icon': '.ico',
    'image/tiff': '.tiff',
    'application/pdf': '.pdf',
}

SUPPORTED_MEDIA_EXTENSIONS = tuple(EXTENSION_TO_MIME.keys())

_DATA_URI_RE = re.compile(
    r'data\s*:\s*([a-zA-Z0-9.+_-]+/[a-zA-Z0-9.+_-]+)\s*(?:;[^,;]*)*;base64\s*,\s*',
    re.IGNORECASE,
)
_PDF_PAGE_RE = re.compile(rb'/Type\s*/Page\b(?!s)')


@dataclass(frozen=True)
class MediaBase64Result:
    filename: str
    mime: str
    ext: str
    kind: str  # 'image' | 'pdf' | 'binary'
    raw_bytes: bytes
    raw_size: int
    b64_raw: str
    pdf_pages: int = 0

    @property
    def data_uri(self) -> str:
        return f'data:{self.mime};base64,{self.b64_raw}'


@dataclass(frozen=True)
class DecodedMediaResult:
    raw_bytes: bytes
    raw_size: int
    mime: str
    ext: str
    kind: str  # 'image' | 'pdf' | 'binary'
    pdf_pages: int = 0


def format_byte_size(num_bytes: int) -> str:
    size = max(0, int(num_bytes))
    if size < 1024:
        return f'{size} B'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    return f'{size / (1024 * 1024):.2f} MB'


def estimate_pdf_pages(raw: bytes) -> int:
    """轻量估算 PDF 页数（不引入重型 PDF 依赖，仅用于概览提示）。"""
    if not raw or not raw.lstrip()[:8].startswith(b'%PDF-'):
        return 0
    count = len(_PDF_PAGE_RE.findall(raw))
    return max(0, count)


def detect_media_mime(data: bytes, filename: str = '') -> tuple[str, str]:
    """优先通过文件魔数识别真实 MIME 和后缀，未命中再回退扩展名。"""
    head = bytes(data[:64]) if data else b''
    stripped = head.lstrip()

    if stripped.startswith(b'%PDF-'):
        return 'application/pdf', '.pdf'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', '.png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', '.jpg'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif', '.gif'
    if len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp', '.webp'
    if head.startswith(b'BM'):
        return 'image/bmp', '.bmp'
    if head.startswith((b'II*\x00', b'MM\x00*')):
        return 'image/tiff', '.tiff'
    if head.startswith(b'\x00\x00\x01\x00'):
        return 'image/x-icon', '.ico'
    if b'<svg' in stripped[:256].lower() or (
        stripped.startswith(b'<?xml') and b'<svg' in (data[:512].lower() if data else b'')
    ):
        return 'image/svg+xml', '.svg'

    ext = os.path.splitext(str(filename or '').strip())[1].lower()
    if ext in EXTENSION_TO_MIME:
        return EXTENSION_TO_MIME[ext], ext
    return 'application/octet-stream', (ext or '.bin')


def media_kind_for_mime(mime: str) -> str:
    low = str(mime or '').strip().lower()
    if low == 'application/pdf':
        return 'pdf'
    if low.startswith('image/'):
        return 'image'
    return 'binary'


def wrap_base64_lines(text: str, width: int = 76) -> str:
    if width <= 0 or not text:
        return text
    return '\n'.join(text[i:i + width] for i in range(0, len(text), width))


def format_media_base64_output(
    result: MediaBase64Result,
    *,
    mode: str = 'raw',
    line_wrap: int = 0,
) -> str:
    """按选定输出模式拼装结果：raw | data_uri | html | css。"""
    b64 = wrap_base64_lines(result.b64_raw, line_wrap) if line_wrap > 0 else result.b64_raw
    data_uri = f'data:{result.mime};base64,{b64}'
    mode = str(mode or 'raw').strip().lower()
    if mode == 'data_uri':
        return data_uri
    if mode == 'html':
        if result.kind == 'pdf':
            return f'<embed src="{data_uri}" type="application/pdf" width="100%" height="600px" />'
        alt = (result.filename or 'image').replace('"', '&quot;')
        return f'<img src="{data_uri}" alt="{alt}" />'
    if mode == 'css':
        return f'background-image: url("{data_uri}");'
    return b64


def encode_bytes_to_base64(
    raw: bytes,
    *,
    filename: str = '',
    mime: str = '',
) -> MediaBase64Result:
    if not raw:
        raise ValueError('文件内容为空，无法转换。')
    if len(raw) > MAX_MEDIA_FILE_BYTES:
        raise ValueError(
            f'文件体积超过 {format_byte_size(MAX_MEDIA_FILE_BYTES)} 上限'
            f'（当前 {format_byte_size(len(raw))}），请先压缩后再转换。'
        )
    detected_mime, ext = detect_media_mime(raw, filename)
    final_mime = str(mime or '').strip().lower() or detected_mime
    if final_mime in MIME_TO_EXTENSION and not ext:
        ext = MIME_TO_EXTENSION[final_mime]
    kind = media_kind_for_mime(final_mime)
    b64_raw = base64.b64encode(raw).decode('ascii')
    pages = estimate_pdf_pages(raw) if kind == 'pdf' else 0
    return MediaBase64Result(
        filename=os.path.basename(filename) if filename else f'clipboard{ext}',
        mime=final_mime,
        ext=ext,
        kind=kind,
        raw_bytes=raw,
        raw_size=len(raw),
        b64_raw=b64_raw,
        pdf_pages=pages,
    )


def encode_file_to_base64(path: str) -> MediaBase64Result:
    file_path = str(path or '').strip()
    if not file_path or not os.path.isfile(file_path):
        raise FileNotFoundError('未找到目标文件，请重新选择图片或 PDF。')
    file_size = os.path.getsize(file_path)
    if file_size <= 0:
        raise ValueError('所选文件为空（0 字节）。')
    if file_size > MAX_MEDIA_FILE_BYTES:
        raise ValueError(
            f'文件超过 {format_byte_size(MAX_MEDIA_FILE_BYTES)} 限制'
            f'（当前 {format_byte_size(file_size)}），为防止界面与剪贴板卡顿已拦截。'
        )
    with open(file_path, 'rb') as stream:
        raw = stream.read()
    return encode_bytes_to_base64(raw, filename=os.path.basename(file_path))


def _clean_base64_input(text: str) -> tuple[str, str]:
    """清洗常见包裹（HTML src、CSS url、JSON 引号、Data URI 头、空白与 URL-safe 字符）。"""
    s = str(text or '').strip()
    if not s:
        return '', ''
    # 提取 HTML src="..." 或 CSS url("...") 内部值
    src_match = re.search(r'(?:src\s*=\s*|url\(\s*)["\']?([^"\')\s]+)["\']?', s, re.IGNORECASE)
    if src_match and ('base64,' in src_match.group(1).lower() or len(src_match.group(1)) > 32):
        s = src_match.group(1).strip()

    s = s.strip('"\'` ')
    hint_mime = ''
    m = _DATA_URI_RE.search(s)
    if m:
        hint_mime = m.group(1).lower()
        s = s[m.end():]

    # 去掉换行、空格及常见包裹尾字符
    s = re.sub(r'\s+', '', s).rstrip(';,)"\'')
    # 兼容 URL-safe Base64 (- 和 _)
    if '-' in s or '_' in s:
        s = s.replace('-', '+').replace('_', '/')
    # 补齐 padding
    pad = (-len(s)) % 4
    if pad:
        s += '=' * pad
    return s, hint_mime


def decode_base64_to_media(text: str) -> DecodedMediaResult:
    cleaned, hint_mime = _clean_base64_input(text)
    if not cleaned:
        raise ValueError('请先粘贴需要还原的 Base64 内容（支持纯 Base64 或 data:...;base64,...）。')
    if len(cleaned) > int(MAX_MEDIA_FILE_BYTES * 1.45):
        raise ValueError('Base64 文本过大（超过 25 MB 原始文件上限），请分段或改用文件工具处理。')
    try:
        raw = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError):
        # 宽松重试一次（容忍偶发杂质字符外的合法内容，仍拒绝全非 Base64 文本）
        try:
            raw = base64.b64decode(cleaned, validate=False)
        except Exception as exc:
            raise ValueError('不是有效的 Base64 编码，请检查是否粘贴完整。') from exc
    if not raw:
        raise ValueError('解码结果为空，请检查 Base64 内容。')
    detected_mime, ext = detect_media_mime(raw)
    if detected_mime == 'application/octet-stream' and hint_mime:
        final_mime = hint_mime
        ext = MIME_TO_EXTENSION.get(hint_mime, '.bin')
    else:
        final_mime = detected_mime
    kind = media_kind_for_mime(final_mime)
    pages = estimate_pdf_pages(raw) if kind == 'pdf' else 0
    return DecodedMediaResult(
        raw_bytes=raw,
        raw_size=len(raw),
        mime=final_mime,
        ext=ext,
        kind=kind,
        pdf_pages=pages,
    )
