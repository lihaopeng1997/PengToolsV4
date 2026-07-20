# -*- coding: utf-8 -*-
"""Gateway payload decryption compatible with gatewaySmDeco.js."""

import base64
import re

from gmssl import sm2, sm4


KEYS = {
    1: {
        "request": {
            "private": "4209ec702176f6423b72ad069f2d98f68a96be39f815408307c7c9f854f28834",
            "public": "931e52a96020be463de2fcdb946fe7ad5f7654be6514e65eca417f8e8fe7aefd5476eff553c815abbed551e41ce7cde7c28f48c062543fe59cedb85d57b986df",
        },
        "response": {
            "private": "6f559eb1cc36638f282fa4c47b0d14afdf27ffc6ca836f7f309ec5951ce97de9",
            "public": "80e7cf17047e057ce96ba5cb275f848a1c4a8e926256a4c43422353675e82b917dc81cccc7db6bf572d688b3793cb4c683895cfc3ef8b43ff2372f387d7f11e5",
        },
    },
    2: {},
    3: {
        "request": {
            "private": "a9f06dae10d0233c762f49b66ab598380936d760b1cc308d2d5a69048b921367",
            "public": "31662ae24ce40b1dcfa0b4532f08342bdb97ca8c6a86e6cb66cb3cf02c668636deb7a59f20d4de8ff5608f73cd1b906fb5ee8672d690af6d1d45145426d225b1",
        },
        "response": {
            "private": "7d0eabc780b514670441a023d8fbfbfc2a25e8164f12a7a348349b78668a0db8",
            "public": "b0fa10c5a04c1b9a2bba38dc994922527bdff7bb32c0e04c94499c703de7edb29be3e83717ea7c6903b50ed041d65af4005e11df96eae98d915faa6414db6f91",
        },
    },
}
KEYS[2] = KEYS[1]


def _clean_hex(value):
    return re.sub(r"\s+", "", value or "")


def decrypt_gateway_payload(direction, environment, encrypted_sm4_key, encrypted_payload):
    """Decrypt a gateway request or response payload.

    ``environment`` follows the legacy page: 1 integration, 2 user/UAT,
    3 production. Response key ciphertext may include the leading ``04``
    point marker used by the original page.
    """
    direction = str(direction).strip().lower()
    if direction in {"1", "request", "req"}:
        direction = "request"
    elif direction in {"2", "response", "resp"}:
        direction = "response"
    else:
        raise ValueError("解密方向必须是 request 或 response")

    try:
        environment = int(environment)
        key_pair = KEYS[environment][direction]
    except (KeyError, TypeError, ValueError):
        raise ValueError("环境必须是 1（集成）、2（用户）或 3（生产）") from None

    key_cipher = _clean_hex(encrypted_sm4_key)
    if direction == "response" and key_cipher.lower().startswith("04"):
        key_cipher = key_cipher[2:]
    if not key_cipher or len(key_cipher) % 2:
        raise ValueError("SM4 Key 密文不是有效的十六进制内容")
    try:
        key_cipher_bytes = bytes.fromhex(key_cipher)
    except ValueError:
        raise ValueError("SM4 Key 密文只能包含十六进制字符") from None

    crypt_sm2 = sm2.CryptSM2(
        private_key=key_pair["private"],
        public_key=key_pair["public"],
        mode=1,
    )
    key_bytes = crypt_sm2.decrypt(key_cipher_bytes)
    if not key_bytes:
        raise ValueError("SM2 解密失败，请检查环境、方向和 Key 密文")
    try:
        key_text = key_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("SM2 已解密，但结果不是 UTF-8 格式的 SM4 Key") from None
    if len(key_text.encode("utf-8")) != 16:
        raise ValueError("解出的 SM4 Key 不是 16 字节，请检查环境和方向")

    payload = re.sub(r"\s+", "", encrypted_payload or "")
    try:
        payload_bytes = base64.b64decode(payload, validate=True)
    except Exception:
        raise ValueError("正文密文不是有效的 Base64 内容") from None
    if not payload_bytes or len(payload_bytes) % 16:
        raise ValueError("正文密文长度不符合 SM4-CBC 分组规则")

    crypt_sm4 = sm4.CryptSM4()
    crypt_sm4.set_key(key_text.encode("utf-8"), sm4.SM4_DECRYPT)
    try:
        plain_bytes = crypt_sm4.crypt_cbc(key_text.encode("utf-8"), payload_bytes)
        return plain_bytes.decode("utf-8")
    except (UnicodeDecodeError, IndexError, ValueError):
        raise ValueError("SM4 解密失败，请检查 Key 密文和正文密文是否配套") from None
