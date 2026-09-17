import os
import unittest

from bittorrent import tracker


class TestMockTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = tracker.MockTracker(host="127.0.0.1", port=0)
        self.tracker.start()
        self.addCleanup(self.tracker.stop)

        self.info_hash = os.urandom(20)

    def test_announce_returns_bencoded_peer_list(self):
        peer_id_a = b"A" * 20
        response = tracker.announce(
            self.tracker.url, self.info_hash, peer_id_a, port=6881, left=0, event="started"
        )
        self.assertIn(b"interval", response)
        self.assertIn(b"peers", response)
        self.assertEqual(len(response[b"peers"]), 1)
        peer_entry = response[b"peers"][0]
        self.assertEqual(peer_entry[b"peer id"], peer_id_a)
        self.assertEqual(peer_entry[b"ip"], b"127.0.0.1")
        self.assertEqual(peer_entry[b"port"], 6881)

    def test_announce_accumulates_multiple_peers(self):
        peer_id_a = b"A" * 20
        peer_id_b = b"B" * 20
        tracker.announce(self.tracker.url, self.info_hash, peer_id_a, port=6881, event="started")
        response = tracker.announce(
            self.tracker.url, self.info_hash, peer_id_b, port=6882, event="started"
        )
        returned_ids = {p[b"peer id"] for p in response[b"peers"]}
        self.assertEqual(returned_ids, {peer_id_a, peer_id_b})

    def test_different_info_hashes_have_separate_swarms(self):
        other_info_hash = os.urandom(20)
        peer_id = b"C" * 20
        tracker.announce(self.tracker.url, self.info_hash, peer_id, port=1000, event="started")
        response = tracker.announce(
            self.tracker.url, other_info_hash, b"D" * 20, port=2000, event="started"
        )
        self.assertEqual(len(response[b"peers"]), 1)
        self.assertEqual(response[b"peers"][0][b"port"], 2000)

    def test_stopped_event_removes_peer(self):
        peer_id = b"E" * 20
        tracker.announce(self.tracker.url, self.info_hash, peer_id, port=3000, event="started")
        response = tracker.announce(
            self.tracker.url, self.info_hash, peer_id, port=3000, event="stopped"
        )
        self.assertEqual(response[b"peers"], [])


if __name__ == "__main__":
    unittest.main()
