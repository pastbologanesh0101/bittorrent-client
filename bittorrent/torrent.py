""".torrent file creation and parsing.

A .torrent file is a bencoded dictionary with (at minimum):

    {
      "announce": "<tracker url>",
      "info": {
        "name": "<file name>",
        "piece length": <bytes per piece>,
        "pieces": <concatenated 20-byte SHA-1 hashes, one per piece>,
        "length": <total file size in bytes>   (single-file mode)
      }
    }

The ``info_hash`` that identifies a torrent to trackers and peers is the
SHA-1 hash of the *bencoded* "info" dictionary (not the whole file).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import List

from . import bencode

SHA1_LEN = 20


@dataclass
class TorrentInfo:
    """Convenience view over a parsed .torrent dict."""

    name: str
    piece_length: int
    pieces: List[bytes]  # list of 20-byte SHA-1 hashes, one per piece
    total_length: int
    announce: str
    info_hash: bytes

    @property
    def num_pieces(self) -> int:
        return len(self.pieces)

    def piece_length_for(self, index: int) -> int:
        """Actual byte length of piece ``index`` (last piece may be shorter)."""
        if index < 0 or index >= self.num_pieces:
            raise IndexError("piece index out of range")
        if index == self.num_pieces - 1:
            remainder = self.total_length - self.piece_length * (self.num_pieces - 1)
            return remainder
        return self.piece_length


def compute_info_hash(info_dict: dict) -> bytes:
    """SHA-1 of the bencoded info dictionary."""
    return hashlib.sha1(bencode.encode(info_dict)).digest()


def create_torrent(file_path: str, tracker_url: str, piece_length: int = 16384) -> dict:
    """Build a single-file torrent dict for ``file_path``.

    Returns the full bencode-able torrent dictionary (with bytes keys),
    ready to be passed to :func:`save_torrent`.
    """
    with open(file_path, "rb") as f:
        data = f.read()

    pieces = bytearray()
    for offset in range(0, len(data), piece_length):
        chunk = data[offset : offset + piece_length]
        pieces += hashlib.sha1(chunk).digest()

    info = {
        b"name": os.path.basename(file_path).encode("utf-8"),
        b"piece length": piece_length,
        b"pieces": bytes(pieces),
        b"length": len(data),
    }

    torrent = {
        b"announce": tracker_url.encode("utf-8"),
        b"info": info,
    }
    return torrent


def save_torrent(torrent_dict: dict, out_path: str) -> None:
    with open(out_path, "wb") as f:
        f.write(bencode.encode(torrent_dict))


def load_torrent(path: str) -> TorrentInfo:
    with open(path, "rb") as f:
        raw = f.read()
    torrent_dict = bencode.decode(raw)
    return parse_torrent(torrent_dict)


def parse_torrent(torrent_dict: dict) -> TorrentInfo:
    info = torrent_dict[b"info"]
    pieces_blob = info[b"pieces"]
    if len(pieces_blob) % SHA1_LEN != 0:
        raise ValueError("malformed 'pieces' field: not a multiple of 20 bytes")
    pieces = [
        pieces_blob[i : i + SHA1_LEN] for i in range(0, len(pieces_blob), SHA1_LEN)
    ]
    return TorrentInfo(
        name=info[b"name"].decode("utf-8"),
        piece_length=info[b"piece length"],
        pieces=pieces,
        total_length=info[b"length"],
        announce=torrent_dict[b"announce"].decode("utf-8"),
        info_hash=compute_info_hash(info),
    )
