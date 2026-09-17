#!/usr/bin/env python3
"""End-to-end demo of the local BitTorrent client stack.

This script, entirely on localhost:

  1. Creates a small sample file and a .torrent file describing it.
  2. Starts a mock tracker (HTTP server) and a seeder peer holding the
     complete file.
  3. Has the seeder announce itself to the tracker.
  4. Starts a leecher that announces to the tracker to discover the
     seeder, connects to it over a TCP socket, performs the peer wire
     handshake, and downloads the file piece by piece, verifying each
     piece's SHA-1 hash against the torrent metadata.
  5. Verifies the downloaded file's SHA-256 digest matches the original.

Run it with: python3 demo.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile

from bittorrent import peer, torrent, tracker


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="bittorrent-demo-")
    print(f"[demo] working directory: {workdir}")

    original_path = os.path.join(workdir, "sample.txt")
    with open(original_path, "wb") as f:
        # A few pieces' worth of semi-repetitive data.
        f.write((b"BitTorrent from scratch! " * 2000))

    torrent_tracker = tracker.MockTracker(host="127.0.0.1", port=0)
    torrent_tracker.start()
    print(f"[demo] mock tracker listening at {torrent_tracker.url}")

    try:
        torrent_dict = torrent.create_torrent(
            original_path, torrent_tracker.url, piece_length=4096
        )
        torrent_path = os.path.join(workdir, "sample.torrent")
        torrent.save_torrent(torrent_dict, torrent_path)
        info = torrent.load_torrent(torrent_path)
        print(f"[demo] created {torrent_path}")
        print(f"[demo] info_hash = {info.info_hash.hex()}")
        print(f"[demo] {info.num_pieces} pieces of up to {info.piece_length} bytes")

        seeder_peer_id = peer.make_peer_id(b"-PY0001-")
        seeder = peer.Seeder(original_path, info, seeder_peer_id)
        seeder.start()
        print(f"[demo] seeder listening on 127.0.0.1:{seeder.port}")

        try:
            tracker.announce(
                torrent_tracker.url,
                info.info_hash,
                seeder_peer_id,
                seeder.port,
                left=0,
                event="started",
            )

            leecher_peer_id = peer.make_peer_id(b"-PY0001-")
            response = tracker.announce(
                torrent_tracker.url,
                info.info_hash,
                leecher_peer_id,
                port=0,
                left=info.total_length,
                event="started",
            )
            peers = [p for p in response[b"peers"] if p[b"peer id"] != leecher_peer_id]
            if not peers:
                print("[demo] no peers returned by tracker!", file=sys.stderr)
                return 1
            target = peers[0]
            print(
                f"[demo] leecher discovered peer "
                f"{target[b'ip'].decode()}:{target[b'port']} via tracker"
            )

            downloaded_path = os.path.join(workdir, "downloaded_sample.txt")
            leecher = peer.Leecher(info, leecher_peer_id)
            leecher.download(target[b"ip"].decode(), target[b"port"], downloaded_path)

            original_digest = sha256_of(original_path)
            downloaded_digest = sha256_of(downloaded_path)
            print(f"[demo] original  SHA-256: {original_digest}")
            print(f"[demo] downloaded SHA-256: {downloaded_digest}")

            if original_digest == downloaded_digest:
                print("[demo] SUCCESS: downloaded file matches original byte-for-byte.")
                return 0
            print("[demo] FAILURE: downloaded file does not match original!", file=sys.stderr)
            return 1
        finally:
            seeder.stop()
    finally:
        torrent_tracker.stop()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
