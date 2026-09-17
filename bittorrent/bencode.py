"""Bencode encoder/decoder.

Bencode is the serialization format used by the BitTorrent protocol for
.torrent files and tracker responses. It supports four data types:

    integers  -> i<base-ten digits>e            e.g. i42e
    strings   -> <length>:<raw bytes>            e.g. 4:spam
    lists     -> l<bencoded values>e             e.g. l4:spami42ee
    dicts     -> d<bencoded key><bencoded value>...e
                 keys must be strings and must appear in sorted order.
                 e.g. d3:bar4:spam3:fooi42ee

Decoded strings are returned as ``bytes`` (not ``str``) because torrent
data is frequently binary (e.g. piece hashes, info_hash, peer ids).
Dict keys are likewise ``bytes``.
"""

from __future__ import annotations

from typing import Any


class BencodeError(ValueError):
    """Raised when bencoded data is malformed."""


def encode(data: Any) -> bytes:
    """Encode a Python object (int, bytes, str, list, dict) to bencode bytes."""
    if isinstance(data, bool):
        # bool is a subclass of int; BitTorrent has no boolean type, so
        # reject it explicitly rather than silently encoding True as i1e.
        raise TypeError("bencode does not support booleans")
    if isinstance(data, int):
        return b"i" + str(data).encode("ascii") + b"e"
    if isinstance(data, bytes):
        return str(len(data)).encode("ascii") + b":" + data
    if isinstance(data, str):
        raw = data.encode("utf-8")
        return str(len(raw)).encode("ascii") + b":" + raw
    if isinstance(data, list):
        return b"l" + b"".join(encode(item) for item in data) + b"e"
    if isinstance(data, dict):
        out = bytearray(b"d")
        # Dict keys must be sorted as raw byte strings.
        def _key_bytes(k: Any) -> bytes:
            if isinstance(k, bytes):
                return k
            if isinstance(k, str):
                return k.encode("utf-8")
            raise TypeError("bencode dict keys must be bytes or str")

        for key in sorted(data.keys(), key=_key_bytes):
            out += encode(_key_bytes(key))
            out += encode(data[key])
        out += b"e"
        return bytes(out)
    raise TypeError(f"cannot bencode object of type {type(data)!r}")


def decode(data: bytes) -> Any:
    """Decode a complete bencoded byte string into a Python object."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("decode() requires bytes")
    value, index = _decode(data, 0)
    if index != len(data):
        raise BencodeError("trailing data after top-level bencoded value")
    return value


def decode_prefix(data: bytes, index: int = 0):
    """Decode a single bencoded value starting at ``index``.

    Returns ``(value, next_index)``. Useful for streaming/partial buffers.
    """
    return _decode(data, index)


def _decode(data: bytes, index: int):
    if index >= len(data):
        raise BencodeError("unexpected end of data")
    marker = data[index : index + 1]

    if marker == b"i":
        end = data.index(b"e", index)
        num_str = data[index + 1 : end]
        if num_str == b"" or num_str == b"-":
            raise BencodeError("invalid integer")
        value = int(num_str)
        return value, end + 1

    if marker == b"l":
        index += 1
        result = []
        while data[index : index + 1] != b"e":
            item, index = _decode(data, index)
            result.append(item)
        return result, index + 1

    if marker == b"d":
        index += 1
        result = {}
        while data[index : index + 1] != b"e":
            key, index = _decode(data, index)
            if not isinstance(key, bytes):
                raise BencodeError("dict keys must be bencoded strings")
            value, index = _decode(data, index)
            result[key] = value
        return result, index + 1

    if marker.isdigit():
        colon = data.index(b":", index)
        length = int(data[index:colon])
        if length < 0:
            raise BencodeError("negative string length")
        start = colon + 1
        end = start + length
        if end > len(data):
            raise BencodeError("string length exceeds available data")
        return bytes(data[start:end]), end

    raise BencodeError(f"invalid bencode marker {marker!r} at index {index}")
