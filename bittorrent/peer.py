"""Peer wire protocol: handshake + a subset of the message stream.

Handshake (49 + len(pstr) bytes, pstr is always "BitTorrent protocol"):

    <pstrlen:1><pstr:19><reserved:8><info_hash:20><peer_id:20>

After the handshake, peers exchange length-prefixed messages:

    <length:4 (big-endian)><message id:1><payload:length-1>

A zero-length message is a keep-alive. This implementation supports the
messages needed for a single seeder to serve a complete file to a single
leecher over a localhost TCP socket:

    id 0  choke
    id 1  unchoke
    id 2  interested
    id 3  not interested
    id 4  have         payload: piece index (4 bytes)
    id 5  bitfield      payload: bitfield
    id 6  request       payload: index, begin, length (4 bytes each)
    id 7  piece         payload: index, begin, block
    id 8  cancel        payload: index, begin, length

This is intentionally a small subset of BEP 3 - no choking algorithm,
no endgame mode, no extension protocol, no DHT/PEX.
"""

from __future__ import annotations

import hashlib
import socket
import struct
import threading
from dataclasses import dataclass, field
from typing import List, Optional

from .torrent import TorrentInfo

PSTR = b"BitTorrent protocol"
HANDSHAKE_LEN = 49 + len(PSTR)

CHOKE = 0
UNCHOKE = 1
INTERESTED = 2
NOT_INTERESTED = 3
HAVE = 4
BITFIELD = 5
REQUEST = 6
PIECE = 7
CANCEL = 8


class ProtocolError(Exception):
    pass


class PieceHashMismatch(Exception):
    """Raised when downloaded piece data does not match the expected hash."""


def make_peer_id(prefix: bytes = b"-PY0001-") -> bytes:
    """A 20-byte peer id, loosely following the Azureus-style convention."""
    import os

    tail = os.urandom(20 - len(prefix))
    return prefix + tail


def recvall(sock: socket.socket, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("socket closed before expected bytes were received")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def build_handshake(info_hash: bytes, peer_id: bytes) -> bytes:
    if len(info_hash) != 20:
        raise ValueError("info_hash must be 20 bytes")
    if len(peer_id) != 20:
        raise ValueError("peer_id must be 20 bytes")
    return bytes([len(PSTR)]) + PSTR + (b"\x00" * 8) + info_hash + peer_id


def read_handshake(sock: socket.socket) -> tuple:
    data = recvall(sock, HANDSHAKE_LEN)
    pstrlen = data[0]
    if pstrlen != len(PSTR):
        raise ProtocolError("unexpected pstrlen in handshake")
    pstr = data[1 : 1 + pstrlen]
    if pstr != PSTR:
        raise ProtocolError("unexpected protocol string in handshake")
    offset = 1 + pstrlen + 8
    info_hash = data[offset : offset + 20]
    peer_id = data[offset + 20 : offset + 40]
    return info_hash, peer_id


def send_message(sock: socket.socket, msg_id: Optional[int], payload: bytes = b"") -> None:
    if msg_id is None:
        sock.sendall(struct.pack(">I", 0))  # keep-alive
        return
    length = 1 + len(payload)
    sock.sendall(struct.pack(">I", length) + bytes([msg_id]) + payload)


def read_message(sock: socket.socket):
    """Returns (msg_id, payload). msg_id is None for a keep-alive."""
    length = struct.unpack(">I", recvall(sock, 4))[0]
    if length == 0:
        return None, b""
    body = recvall(sock, length)
    return body[0], body[1:]


def make_bitfield(num_pieces: int) -> bytes:
    """A bitfield with all ``num_pieces`` bits set (i.e. "I have everything")."""
    num_bytes = (num_pieces + 7) // 8
    bits = bytearray(num_bytes)
    for i in range(num_pieces):
        bits[i // 8] |= 0x80 >> (i % 8)
    return bytes(bits)


def bitfield_has_piece(bitfield: bytes, index: int) -> bool:
    byte_index = index // 8
    if byte_index >= len(bitfield):
        return False
    return bool(bitfield[byte_index] & (0x80 >> (index % 8)))


@dataclass
class Seeder:
    """Serves a complete file to any peer that connects and requests pieces."""

    file_path: str
    torrent: TorrentInfo
    peer_id: bytes
    host: str = "127.0.0.1"
    port: int = 0
    _sock: socket.socket = field(init=False, repr=False, default=None)
    _thread: threading.Thread = field(init=False, repr=False, default=None)
    _running: bool = field(init=False, default=False)
    _conn_threads: List[threading.Thread] = field(init=False, default_factory=list)

    def start(self) -> None:
        with open(self.file_path, "rb") as f:
            self._data = f.read()
        if len(self._data) != self.torrent.total_length:
            raise ValueError("seeder file does not match torrent length")

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(5)
        self._sock.settimeout(1.0)
        self.port = self._sock.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        for t in self._conn_threads:
            t.join(timeout=5)

    def __enter__(self) -> "Seeder":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle_conn, args=(conn,), daemon=True)
            t.start()
            self._conn_threads.append(t)

    def _handle_conn(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(5.0)
            info_hash, _peer_id = read_handshake(conn)
            if info_hash != self.torrent.info_hash:
                conn.close()
                return
            conn.sendall(build_handshake(self.torrent.info_hash, self.peer_id))
            send_message(conn, BITFIELD, make_bitfield(self.torrent.num_pieces))

            peer_interested = False
            while self._running:
                try:
                    msg_id, payload = read_message(conn)
                except (ConnectionError, socket.timeout, struct.error):
                    break
                if msg_id is None:
                    continue
                if msg_id == INTERESTED:
                    peer_interested = True
                    send_message(conn, UNCHOKE)
                elif msg_id == NOT_INTERESTED:
                    peer_interested = False
                elif msg_id == REQUEST:
                    index, begin, length = struct.unpack(">III", payload)
                    block = self._data[
                        self._piece_offset(index) + begin : self._piece_offset(index)
                        + begin
                        + length
                    ]
                    send_message(conn, PIECE, struct.pack(">II", index, begin) + block)
                # unknown/unsupported message ids are ignored.
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _piece_offset(self, index: int) -> int:
        return index * self.torrent.piece_length


class Leecher:
    """Downloads a complete file from a single seeder peer."""

    def __init__(self, torrent: TorrentInfo, peer_id: bytes, max_retries: int = 3):
        self.torrent = torrent
        self.peer_id = peer_id
        self.max_retries = max_retries

    def download_piece(self, sock: socket.socket, index: int) -> bytes:
        """Request and receive one full piece, verifying its SHA-1 hash.

        Retries up to ``max_retries`` times if the received data does not
        match the expected hash, then raises PieceHashMismatch.
        """
        piece_len = self.torrent.piece_length_for(index)
        expected_hash = self.torrent.pieces[index]

        last_error = None
        for _attempt in range(self.max_retries):
            send_message(sock, REQUEST, struct.pack(">III", index, 0, piece_len))
            msg_id, payload = read_message(sock)
            if msg_id != PIECE:
                last_error = ProtocolError(f"expected PIECE message, got id={msg_id}")
                continue
            recv_index, recv_begin = struct.unpack(">II", payload[:8])
            block = payload[8:]
            if recv_index != index or recv_begin != 0 or len(block) != piece_len:
                last_error = ProtocolError("piece message did not match request")
                continue
            if hashlib.sha1(block).digest() != expected_hash:
                last_error = PieceHashMismatch(f"piece {index} failed hash verification")
                continue
            return block

        raise last_error or PieceHashMismatch(f"piece {index} failed hash verification")

    def download(self, peer_host: str, peer_port: int, output_path: str) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((peer_host, peer_port))
        try:
            sock.sendall(build_handshake(self.torrent.info_hash, self.peer_id))
            info_hash, _remote_peer_id = read_handshake(sock)
            if info_hash != self.torrent.info_hash:
                raise ProtocolError("peer returned mismatched info_hash in handshake")

            msg_id, _payload = read_message(sock)
            if msg_id != BITFIELD:
                raise ProtocolError("expected BITFIELD message after handshake")

            send_message(sock, INTERESTED)
            msg_id, _payload = read_message(sock)
            if msg_id != UNCHOKE:
                raise ProtocolError("expected UNCHOKE after INTERESTED")

            pieces = [b""] * self.torrent.num_pieces
            for index in range(self.torrent.num_pieces):
                pieces[index] = self.download_piece(sock, index)

            with open(output_path, "wb") as f:
                for block in pieces:
                    f.write(block)
        finally:
            sock.close()
