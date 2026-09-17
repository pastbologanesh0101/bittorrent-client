import hashlib
import os
import shutil
import socket
import struct
import tempfile
import threading
import unittest

from bittorrent import peer, torrent


def make_sample_torrent(workdir, content=b"hello bittorrent world! " * 50, piece_length=32):
    sample_path = os.path.join(workdir, "sample.bin")
    with open(sample_path, "wb") as f:
        f.write(content)
    torrent_dict = torrent.create_torrent(
        sample_path, "http://127.0.0.1:1/announce", piece_length=piece_length
    )
    torrent_path = os.path.join(workdir, "sample.torrent")
    torrent.save_torrent(torrent_dict, torrent_path)
    info = torrent.load_torrent(torrent_path)
    return sample_path, info


class TestHandshake(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="bt-peer-test-")
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.sample_path, self.info = make_sample_torrent(self.workdir)
        self.seeder_peer_id = peer.make_peer_id()
        self.seeder = peer.Seeder(self.sample_path, self.info, self.seeder_peer_id)
        self.seeder.start()
        self.addCleanup(self.seeder.stop)

    def test_handshake_succeeds_between_two_local_peers(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        self.addCleanup(sock.close)
        sock.connect(("127.0.0.1", self.seeder.port))

        leecher_peer_id = peer.make_peer_id()
        sock.sendall(peer.build_handshake(self.info.info_hash, leecher_peer_id))
        info_hash, remote_peer_id = peer.read_handshake(sock)

        self.assertEqual(info_hash, self.info.info_hash)
        self.assertEqual(remote_peer_id, self.seeder_peer_id)

    def test_seeder_rejects_wrong_info_hash_silently_closing(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        self.addCleanup(sock.close)
        sock.connect(("127.0.0.1", self.seeder.port))

        wrong_info_hash = os.urandom(20)
        sock.sendall(peer.build_handshake(wrong_info_hash, peer.make_peer_id()))
        # Seeder should close the connection rather than complete the handshake.
        data = sock.recv(1024)
        self.assertEqual(data, b"")


class TestSinglePieceTransfer(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="bt-piece-test-")
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.sample_path, self.info = make_sample_torrent(self.workdir)
        self.seeder_peer_id = peer.make_peer_id()
        self.seeder = peer.Seeder(self.sample_path, self.info, self.seeder_peer_id)
        self.seeder.start()
        self.addCleanup(self.seeder.stop)

    def test_single_piece_downloads_and_verifies(self):
        leecher_peer_id = peer.make_peer_id()
        leecher = peer.Leecher(self.info, leecher_peer_id)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        self.addCleanup(sock.close)
        sock.connect(("127.0.0.1", self.seeder.port))
        sock.sendall(peer.build_handshake(self.info.info_hash, leecher_peer_id))
        peer.read_handshake(sock)
        msg_id, _ = peer.read_message(sock)  # bitfield
        self.assertEqual(msg_id, peer.BITFIELD)
        peer.send_message(sock, peer.INTERESTED)
        msg_id, _ = peer.read_message(sock)  # unchoke
        self.assertEqual(msg_id, peer.UNCHOKE)

        block = leecher.download_piece(sock, 0)

        with open(self.sample_path, "rb") as f:
            expected = f.read(self.info.piece_length_for(0))
        self.assertEqual(block, expected)
        self.assertEqual(hashlib.sha1(block).digest(), self.info.pieces[0])


class _CorruptingSeederThread(threading.Thread):
    """A minimal peer that always answers REQUEST with corrupted piece data.

    Used only to prove the leecher's hash verification actually rejects
    bad data instead of silently accepting it.
    """

    def __init__(self, info_hash, piece_length):
        super().__init__(daemon=True)
        self.info_hash = info_hash
        self.piece_length = piece_length
        self.peer_id = peer.make_peer_id()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(5.0)
        self.port = self._sock.getsockname()[1]

    def run(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5.0)
            peer.read_handshake(conn)
            conn.sendall(peer.build_handshake(self.info_hash, self.peer_id))
            peer.send_message(conn, peer.BITFIELD, peer.make_bitfield(1))
            while True:
                try:
                    msg_id, payload = peer.read_message(conn)
                except (ConnectionError, OSError):
                    return
                if msg_id == peer.INTERESTED:
                    peer.send_message(conn, peer.UNCHOKE)
                elif msg_id == peer.REQUEST:
                    index, begin, length = struct.unpack(">III", payload)
                    garbage = bytes((b + 1) % 256 for b in b"\x00" * length)
                    peer.send_message(
                        conn, peer.PIECE, struct.pack(">II", index, begin) + garbage
                    )
                    return

    def stop(self):
        try:
            self._sock.close()
        except OSError:
            pass
        self.join(timeout=5)


class TestCorruptedPieceRejected(unittest.TestCase):
    def test_corrupted_piece_data_is_rejected_not_silently_accepted(self):
        expected_hash = hashlib.sha1(b"the real correct piece data......").digest()
        info = torrent.TorrentInfo(
            name="fake.bin",
            piece_length=32,
            pieces=[expected_hash],
            total_length=32,
            announce="http://127.0.0.1:1/announce",
            info_hash=os.urandom(20),
        )

        bad_seeder = _CorruptingSeederThread(info.info_hash, 32)
        bad_seeder.start()
        self.addCleanup(bad_seeder.stop)

        leecher_peer_id = peer.make_peer_id()
        leecher = peer.Leecher(info, leecher_peer_id, max_retries=1)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        self.addCleanup(sock.close)
        sock.connect(("127.0.0.1", bad_seeder.port))
        sock.sendall(peer.build_handshake(info.info_hash, leecher_peer_id))
        peer.read_handshake(sock)
        peer.read_message(sock)  # bitfield
        peer.send_message(sock, peer.INTERESTED)
        peer.read_message(sock)  # unchoke

        with self.assertRaises(peer.PieceHashMismatch):
            leecher.download_piece(sock, 0)


if __name__ == "__main__":
    unittest.main()
