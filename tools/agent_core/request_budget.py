"""Local byte measurement after all model request fields have been assembled.

This counts the JSON HTTP body, including any adapter-private continuation.
It is not a provider tokenizer or a measurement of the server chat template.
Only detached scalar measurements leave this module; request content is not
included in receipts, error messages or a module cache.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any


def validate_request_byte_limit(value: object) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError('max_request_bytes must be a positive integer or None')


class RequestEncodingError(RuntimeError):
    def __init__(self) -> None:
        super().__init__('model_request_encoding')


class RequestInputLimitExceeded(RuntimeError):
    def __init__(self, *, input_bytes: int, limit_bytes: int) -> None:
        if type(input_bytes) is not int or input_bytes < 0:
            raise ValueError('input_bytes must be a non-negative integer')
        validate_request_byte_limit(limit_bytes)
        if limit_bytes is None:
            raise ValueError('limit_bytes is required')
        self.input_bytes = input_bytes
        self.limit_bytes = limit_bytes
        super().__init__('final_request_input_bytes')


@dataclass(frozen=True, slots=True)
class RequestMeasurement:
    input_bytes: int
    limit_bytes: int | None

    def __post_init__(self) -> None:
        if type(self.input_bytes) is not int or self.input_bytes < 0:
            raise ValueError('input_bytes must be a non-negative integer')
        validate_request_byte_limit(self.limit_bytes)


def encode_model_request(body: Mapping[str, Any]) -> bytes:
    """Use the existing transport's JSON shape, including its spaces."""
    try:
        if not isinstance(body, Mapping):
            raise TypeError
        return json.dumps(dict(body), ensure_ascii=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, OverflowError):
        # Encoding errors can include private values in their original text.
        pass
    # Raise outside the handler so the encoding exception is not chained.
    raise RequestEncodingError()


def measure_model_request(
    body: Mapping[str, Any], *, max_request_bytes: int | None,
) -> RequestMeasurement:
    validate_request_byte_limit(max_request_bytes)
    measured = RequestMeasurement(len(encode_model_request(body)), max_request_bytes)
    if max_request_bytes is not None and measured.input_bytes > max_request_bytes:
        raise RequestInputLimitExceeded(input_bytes=measured.input_bytes,
                                        limit_bytes=max_request_bytes)
    return measured
