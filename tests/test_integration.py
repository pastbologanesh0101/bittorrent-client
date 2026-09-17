import hashlib
import os
import shutil
import tempfile
import unittest

from bittorrent import peer, torrent, tracker


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class TestEndToEndDownload(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="bt-integration-test-")
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)

        self.mock_tracker = tracker.MockTracker(host="127.0.0.1", port=0)
        self.mock_tracker.start()
        self.addCleanup(self.mock_tracker.stop)

    def test_full_multi_piece_file_downloads_correctly_end_to_end(self):
        original_path = os.path.join(self.workdir, "original.bin")
        # Several pieces' worth of data, last piece intentionally partial.
        content = os.urandom(4096 * 5 + 777)
        with open(original_path, "wb") as f:
            f.write(content)

        torrent_dict = torrent.create_torrent(original_path, self.mock_tracker.url, piece_length=4096)
        torrent_path = os.path.join(self.workdir, "original.torrent")
        torrent.save_torrent(torrent_dict, torrent_path)
        info = torrent.load_torrent(torrent_path)
        self.assertEqual(info.num_pieces, 6)

        seeder_peer_id = peer.make_peer_id()
        seeder = peer.Seeder(original_path, info, seeder_peer_id)
        seeder.start()
        self.addCleanup(seeder.stop)

        tracker.announce(
            self.mock_tracker.url, info.info_hash, seeder_peer_id, seeder.port,
            left=0, event="started",
        )

        leecher_peer_id = peer.make_peer_id()
        response = tracker.announce(
            self.mock_tracker.url, info.info_hash, leecher_peer_id, port=0,
            left=info.total_length, event="started",
        )
        peers = [p for p in response[b"peers"] if p[b"peer id"] != leecher_peer_id]
        self.assertEqual(len(peers), 1)
        target = peers[0]
        self.assertEqual(target[b"port"], seeder.port)

        downloaded_path = os.path.join(self.workdir, "downloaded.bin")
        leecher = peer.Leecher(info, leecher_peer_id)
        leecher.download(target[b"ip"].decode(), target[b"port"], downloaded_path)

        self.assertEqual(sha256_of(downloaded_path), sha256_of(original_path))
        with open(downloaded_path, "rb") as f:
            self.assertEqual(f.read(), content)


if __name__ == "__main__":
    unittest.main()
