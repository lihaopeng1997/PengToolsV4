# -*- coding: utf-8 -*-
"""本机敏感凭据保护（安测基线）。

优先级：
1. Windows DPAPI（当前登录用户可解密，文件拷到别机不可用）
2. Fernet + 本机盐（cryptography，密钥存 data 目录）
3. 禁止对新数据使用纯 base64（仅兼容解密历史 `b64:`）

Token 前缀：
- `dpapi:`  Windows CryptProtectData
- `enc:`    Fernet
- `b64:`    历史弱编码（只读兼容，保存时会升级）
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
from typing import Optional

from config import ensure_config_dir, local_data_dir


class SecureStoreError(Exception):
    """凭据加解密失败。"""


def _data_dir() -> str:
    return local_data_dir()


def _key_path() -> str:
    return os.path.join(_data_dir(), '.ops_ssh_key')


# ── Windows DPAPI ──────────────────────────────────────────

def _dpapi_available() -> bool:
    return sys.platform.startswith('win')


def _dpapi_protect(plain: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(plain, len(plain))
    blob_in = DATA_BLOB(len(plain), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise SecureStoreError('CryptProtectData failed')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(len(blob), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise SecureStoreError('CryptUnprotectData failed')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def encrypt_dpapi(plain: str) -> str:
    raw = _dpapi_protect(str(plain or '').encode('utf-8'))
    return 'dpapi:' + base64.urlsafe_b64encode(raw).decode('ascii')


def decrypt_dpapi(token: str) -> str:
    raw = str(token or '')
    if raw.startswith('dpapi:'):
        raw = raw[6:]
    try:
        blob = base64.urlsafe_b64decode(raw.encode('ascii'))
        return _dpapi_unprotect(blob).decode('utf-8')
    except Exception as exc:
        raise SecureStoreError(f'DPAPI decrypt failed: {exc}') from exc


# ── Fernet ─────────────────────────────────────────────────

def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # pragma: no cover
        return None
    ensure_config_dir()
    path = _key_path()
    if os.path.isfile(path):
        try:
            with open(path, 'rb') as stream:
                raw = stream.read().strip()
            if raw:
                return Fernet(raw)
        except OSError:
            pass
    salt = os.urandom(16)
    material = hashlib.sha256(
        b'pengtools-ops-ssh-v1|'
        + salt
        + os.environ.get('COMPUTERNAME', '').encode('utf-8', 'ignore')
        + os.environ.get('USERNAME', '').encode('utf-8', 'ignore')
    ).digest()
    key = base64.urlsafe_b64encode(material)
    try:
        with open(path, 'wb') as stream:
            stream.write(key)
    except OSError:
        return Fernet(key)
    return Fernet(key)


def encrypt_fernet(plain: str) -> str:
    f = _fernet()
    if f is None:
        raise SecureStoreError('cryptography.Fernet unavailable')
    return 'enc:' + f.encrypt(str(plain or '').encode('utf-8')).decode('ascii')


def decrypt_fernet(token: str) -> str:
    from cryptography.fernet import InvalidToken

    raw = str(token or '')
    if raw.startswith('enc:'):
        raw = raw[4:]
    f = _fernet()
    if f is None:
        raise SecureStoreError('cryptography.Fernet unavailable')
    try:
        return f.decrypt(raw.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, TypeError) as exc:
        raise SecureStoreError(f'Fernet decrypt failed: {exc}') from exc


# ── 统一 API ───────────────────────────────────────────────

def backend_name() -> str:
    """当前优先后端：dpapi | fernet | none。"""
    if _dpapi_available():
        return 'dpapi'
    try:
        from cryptography.fernet import Fernet  # noqa: F401
        return 'fernet'
    except ImportError:
        return 'none'


def encrypt_secret(plain: str) -> str:
    """加密敏感串。绝不写出新的 `b64:`。"""
    text = str(plain or '')
    if not text:
        return ''
    if _dpapi_available():
        try:
            return encrypt_dpapi(text)
        except Exception:
            pass
    try:
        return encrypt_fernet(text)
    except Exception as exc:
        raise SecureStoreError(
            '无法安全加密凭据：Windows DPAPI 与 Fernet 均不可用'
        ) from exc


def decrypt_secret(token: str) -> str:
    """解密；兼容 dpapi / enc / 历史 b64 / 误存明文。"""
    raw = str(token or '')
    if not raw:
        return ''
    if raw.startswith('dpapi:'):
        try:
            return decrypt_dpapi(raw)
        except SecureStoreError:
            return ''
    if raw.startswith('enc:'):
        try:
            return decrypt_fernet(raw)
        except SecureStoreError:
            return ''
    if raw.startswith('b64:'):
        try:
            return base64.urlsafe_b64decode(raw[4:].encode('ascii')).decode('utf-8')
        except (ValueError, UnicodeError):
            return ''
    # 兼容旧数据：当作用户误存明文
    return raw


def is_weak_token(token: str) -> bool:
    """历史弱编码或明文（应在下次保存时升级）。"""
    raw = str(token or '')
    if not raw:
        return False
    if raw.startswith(('dpapi:', 'enc:')):
        return False
    if raw.startswith('b64:'):
        return True
    return True


def reencrypt_if_weak(token: str) -> Optional[str]:
    """若 token 为弱编码/明文，解密后用当前强算法重加密；否则返回 None。"""
    if not is_weak_token(token):
        return None
    plain = decrypt_secret(token)
    if not plain:
        return None
    return encrypt_secret(plain)
