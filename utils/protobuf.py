"""
Minimal protobuf wire-format encoder/decoder.

Google Travel encodes a search in the URL as a base64url protobuf
(`tfs=` for flights). Only varints and length-delimited fields are needed;
this avoids a protobuf dependency and generated classes.
"""
from __future__ import annotations

import base64
from typing import Dict, List, Tuple, Union

Value = Union[int, bytes]

_VARINT, _LEN = 0, 2


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def field_varint(number: int, value: int) -> bytes:
    return varint((number << 3) | _VARINT) + varint(value)


def field_bytes(number: int, payload: bytes) -> bytes:
    return varint((number << 3) | _LEN) + varint(len(payload)) + payload


def field_str(number: int, text: str) -> bytes:
    return field_bytes(number, text.encode())


def to_url_param(message: bytes) -> str:
    """base64url without padding, as used in Google Travel URLs."""
    return base64.urlsafe_b64encode(message).decode().rstrip("=")


def from_url_param(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def decode(data: bytes) -> Dict[int, List[Value]]:
    """Decode one message level: field number → values (ints for varints, bytes otherwise)."""
    fields: Dict[int, List[Value]] = {}
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        number, wire_type = key >> 3, key & 0x7
        if wire_type == _VARINT:
            value, pos = _read_varint(data, pos)
        elif wire_type == _LEN:
            length, pos = _read_varint(data, pos)
            value, pos = data[pos:pos + length], pos + length
        else:
            raise ValueError(f"unsupported wire type {wire_type}")
        fields.setdefault(number, []).append(value)
    return fields
