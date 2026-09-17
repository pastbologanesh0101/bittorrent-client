import hashlib
import os
import shutil
import tempfile
import unittest

from bittorrent import bencode, torrent


class TestTorrentCreationAndParsing(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="bt-torrent-test-")
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)

        self.sample_path = os.path.join(self.workdir, "sample.bin")
        # 3 full pieces + 1 partial piece at piece_length=16
        self.piece_length = 16
        self.content = bytes(range(256)) * 2  # 512 bytes -> not a multiple check below
        self.content = self.content[:40]  # 40 bytes: 2 full pieces + 1 partial (8 bytes)
        with open(self.sample_path, "wb") as f:
            f.write(self.content)

        self.tracker_url = "http://127.0.0.1:9999/announce"
        self.torrent_dict = torrent.create_torrent(
            self.sample_path, self.tracker_url, piece_length=self.piece_length
        )
        self.torrent_path = os.path.join(self.workdir, "sample.torrent")
        torrent.save_torrent(self.torrent_dict, self.torrent_path)

    def test_piece_hashes_match_expected_sha1_chunks(self):
        info = torrent.load_torrent(self.torrent_path)
        expected_hashes = [
            hashlib.sha1(self.content[i : i + self.piece_length]).digest()
            for i in range(0, len(self.content), self.piece_length)
        ]
        self.assertEqual(info.pieces, expected_hashes)
        self.assertEqual(info.num_pieces, 3)

    def test_total_length_and_name(self):
        info = torrent.load_torrent(self.torrent_path)
        self.assertEqual(info.total_length, len(self.content))
        self.assertEqual(info.name, "sample.bin")
        self.assertEqual(info.announce, self.tracker_url)

    def test_last_piece_length_is_shorter(self):
        info = torrent.load_torrent(self.torrent_path)
        self.assertEqual(info.piece_length_for(0), self.piece_length)
        self.assertEqual(info.piece_length_for(1), self.piece_length)
        self.assertEqual(info.piece_length_for(2), len(self.content) - 2 * self.piece_length)

    def test_info_hash_matches_manual_computation(self):
        with open(self.torrent_path, "rb") as f:
            raw = bencode.decode(f.read())
        expected = hashlib.sha1(bencode.encode(raw[b"info"])).digest()
        info = torrent.load_torrent(self.torrent_path)
        self.assertEqual(info.info_hash, expected)
        self.assertEqual(len(info.info_hash), 20)

    def test_info_hash_changes_if_content_changes(self):
        info_a = torrent.load_torrent(self.torrent_path)

        other_path = os.path.join(self.workdir, "other.bin")
        with open(other_path, "wb") as f:
            f.write(b"completely different content, not matching original at all!")
        other_torrent = torrent.create_torrent(other_path, self.tracker_url, piece_length=16)
        other_torrent_path = os.path.join(self.workdir, "other.torrent")
        torrent.save_torrent(other_torrent, other_torrent_path)
        info_b = torrent.load_torrent(other_torrent_path)

        self.assertNotEqual(info_a.info_hash, info_b.info_hash)


if __name__ == "__main__":
    unittest.main()
