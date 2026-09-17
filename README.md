# BitTorrent Client

A small, educational implementation of the core BitTorrent protocol,
written from scratch in pure Python standard library (`socket`, `hashlib`,
`http.server`, `struct`, `threading`, ...). No third-party BitTorrent
libraries are used anywhere.

It implements enough of the real protocol for two local peer processes —
a **seeder** (has the complete file) and a **leecher** (downloads it) — to
find each other through a **mock tracker** and transfer a file over real
localhost TCP sockets, with piece-level SHA-1 verification.

> **Scope note:** this is a local-peer educational subset of BitTorrent.
> It does **not** implement DHT, magnet links, PEX, the extension
> protocol, choking algorithms, or real internet swarms/trackers. It is
> meant to demonstrate how the protocol's pieces (pun intended) fit
> together, not to be a production torrent client.

## Quick start

```bash
python3 demo.py
```

This creates a temporary sample file, builds a `.torrent` for it, starts
a mock tracker and a seeder peer, then starts a leecher that discovers
the seeder via the tracker, downloads the file over a socket, and
verifies the downloaded file's SHA-256 matches the original byte-for-byte.

Run the test suite:

```bash
python3 -m unittest discover -s tests -v
```

## Protocol basics

### Bencode (`bittorrent/bencode.py`)

Bencode is the serialization format BitTorrent uses for `.torrent` files
and tracker responses. It has four types:

| Type    | Encoding                          | Example              |
|---------|------------------------------------|-----------------------|
| Integer | `i<digits>e`                       | `i42e` → `42`         |
| String  | `<length>:<raw bytes>`             | `4:spam` → `b"spam"`  |
| List    | `l<bencoded items>e`               | `l4:spami42ee`        |
| Dict    | `d<bencoded key><value>...e`       | keys sorted lexically |

Decoded strings and dict keys come back as `bytes`, since torrent data is
frequently binary (info hashes, piece hashes, peer ids). `encode()` /
`decode()` are fully round-trip safe for nested structures of all four
types.

### `.torrent` files (`bittorrent/torrent.py`)

A `.torrent` file is just a bencoded dictionary:

```
{
  "announce": "<tracker announce URL>",
  "info": {
    "name": "<file name>",
    "piece length": <bytes per piece>,
    "pieces": <concatenated 20-byte SHA-1 hashes, one per piece>,
    "length": <total file size in bytes>
  }
}
```

`create_torrent()` splits a file into fixed-size pieces, SHA-1 hashes
each piece, and assembles this dictionary. The **info_hash** — the value
that uniquely identifies a torrent to trackers and peers — is the SHA-1
hash of the *bencoded `info` dictionary itself* (not the whole file, and
not the raw bytes — the canonical bencoded form of that sub-dictionary).
`parse_torrent()` / `load_torrent()` reverse the process: they decode the
file and expose the piece hash list, piece length, total length, and
computed info_hash via a `TorrentInfo` dataclass.

### Tracker announce (`bittorrent/tracker.py`)

Real BitTorrent trackers are simple HTTP servers: a client does an HTTP
`GET` to an announce URL with query parameters (`info_hash`, `peer_id`,
`port`, `uploaded`, `downloaded`, `left`, `event`) and gets back a
bencoded dictionary with an `interval` and a `peers` list.

`MockTracker` implements exactly this `/announce` endpoint using
`http.server.ThreadingHTTPServer`, in memory, for localhost testing:

- `info_hash` and `peer_id` are raw 20-byte binary values, percent-encoded
  byte-for-byte in the query string (`urllib.parse.quote_from_bytes` /
  `unquote_to_bytes`) rather than treated as UTF-8 text.
- Peers announcing with `event=started` are recorded per swarm
  (keyed by `info_hash`); `event=stopped` removes them.
- The response is a bencoded dict: `{"interval": ..., "peers": [{"peer id": ..., "ip": ..., "port": ...}, ...]}`.

This is a toy tracker for local tests only — it is never pointed at any
real internet tracker or swarm.

### Peer wire protocol (`bittorrent/peer.py`)

After discovering a peer's address via the tracker, two peers speak a
binary TCP protocol directly to each other:

**Handshake** (49 + 19 bytes, sent by both sides on connect):

```
<pstrlen=19><"BitTorrent protocol"><8 reserved bytes><info_hash><peer_id>
```

**Messages** after the handshake, each length-prefixed
(`<4-byte big-endian length><message id><payload>`, zero length = keep-alive).
This implementation supports the subset needed for a full single-peer
file transfer:

| id | message        | payload                          |
|----|----------------|-----------------------------------|
| 0  | choke          | —                                  |
| 1  | unchoke        | —                                  |
| 2  | interested     | —                                  |
| 3  | not interested | —                                  |
| 4  | have           | piece index                        |
| 5  | bitfield       | bitmap of held pieces              |
| 6  | request        | piece index, begin offset, length  |
| 7  | piece          | piece index, begin offset, block   |
| 8  | cancel         | piece index, begin offset, length  |

Flow implemented by `Seeder` / `Leecher`:

1. TCP connect, exchange handshakes, verify `info_hash` matches.
2. Seeder sends a `bitfield` claiming every piece.
3. Leecher sends `interested`; seeder replies `unchoke` (no real choking
   algorithm — the seeder always unchokes).
4. Leecher sends `request` for each piece in turn and receives `piece`
   messages.
5. Leecher SHA-1-hashes each received piece and compares it against the
   hash from the torrent's `info` dict. A mismatch raises
   `PieceHashMismatch` (after retrying up to `max_retries` times) instead
   of silently accepting corrupted data.
6. Once every piece is verified, the leecher writes them to the output
   file in order.

## Layout

```
bittorrent/
  bencode.py    # encode/decode
  torrent.py    # .torrent creation/parsing, info_hash
  tracker.py    # mock HTTP tracker + announce client helper
  peer.py       # handshake, wire messages, Seeder, Leecher
demo.py         # end-to-end CLI demo (tracker + seeder + leecher)
tests/          # unit + integration tests
```

## Running tests in CI

`.github/workflows/tests.yml` runs the full test suite (and the demo) on
Python 3.11 and 3.12 on every push and pull request. No dependencies are
installed — everything is standard library.
