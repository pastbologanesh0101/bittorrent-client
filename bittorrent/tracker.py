"""A tiny mock BitTorrent tracker.

Real BitTorrent trackers speak HTTP: a client issues a GET request to an
"announce" URL with query parameters (info_hash, peer_id, port, ...) and
the tracker replies with a *bencoded* dictionary containing an interval
and a list of peers currently swarming that torrent.

This is a deliberately minimal implementation for local testing only:
peers self-report their listening port, the tracker records them in
memory keyed by info_hash, and announce requests return the current
peer list for that torrent (compact peer format is not implemented;
peers are returned as a list of bencoded dicts instead).
"""

from __future__ import annotations

import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote_from_bytes, unquote_to_bytes, urlsplit

from . import bencode


def _parse_query(query: str) -> dict:
    """Parse a query string into {key: raw-bytes-value}.

    We avoid urllib.parse.parse_qs because it assumes UTF-8 text, while
    info_hash / peer_id are arbitrary binary data that has been
    percent-encoded byte-for-byte by the client.
    """
    params = {}
    if not query:
        return params
    for pair in query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        params[key] = unquote_to_bytes(value)
    return params


class _AnnounceHandler(BaseHTTPRequestHandler):
    server: "MockTracker"

    def log_message(self, fmt, *args):  # silence default stderr logging
        pass

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path != "/announce":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return

        params = _parse_query(parts.query)
        try:
            info_hash = params["info_hash"]
            peer_id = params["peer_id"]
            port = int(params["port"].decode("ascii"))
        except (KeyError, ValueError):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(bencode.encode({b"failure reason": b"missing/invalid params"}))
            return

        client_ip = self.client_address[0]
        event = params.get("event", b"").decode("ascii", "ignore")

        response_body = self.server.announce(info_hash, peer_id, client_ip, port, event)

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    # Quiet down connection errors from short-lived test clients.
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            pass


class MockTracker:
    """An in-memory, HTTP announce-only tracker for localhost testing."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, interval: int = 1800):
        self._lock = threading.Lock()
        self._swarms: dict[bytes, dict[bytes, tuple]] = {}
        self.interval = interval
        self._httpd = ThreadingHTTPServer((host, port), _AnnounceHandler)
        self._httpd.timeout = 1
        # Give the handler access back to this MockTracker instance.
        self._httpd.announce = self.announce  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return self._httpd.server_address[0]

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/announce"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "MockTracker":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def announce(self, info_hash: bytes, peer_id: bytes, ip: str, port: int, event: str) -> bytes:
        with self._lock:
            swarm = self._swarms.setdefault(info_hash, {})
            if event == "stopped":
                swarm.pop(peer_id, None)
            else:
                swarm[peer_id] = (ip, port)
            peers = [
                {b"peer id": pid, b"ip": ip.encode("ascii"), b"port": p}
                for pid, (ip, p) in swarm.items()
            ]
        return bencode.encode({b"interval": self.interval, b"peers": peers})

    def peers_for(self, info_hash: bytes):
        with self._lock:
            swarm = self._swarms.get(info_hash, {})
            return [
                {"peer_id": pid, "ip": ip, "port": p} for pid, (ip, p) in swarm.items()
            ]


def build_announce_url(
    tracker_url: str,
    info_hash: bytes,
    peer_id: bytes,
    port: int,
    uploaded: int = 0,
    downloaded: int = 0,
    left: int = 0,
    event: str = "",
) -> str:
    """Build a GET announce URL, percent-encoding the raw binary fields."""
    query = (
        f"info_hash={quote_from_bytes(info_hash)}"
        f"&peer_id={quote_from_bytes(peer_id)}"
        f"&port={port}"
        f"&uploaded={uploaded}"
        f"&downloaded={downloaded}"
        f"&left={left}"
    )
    if event:
        query += f"&event={event}"
    return f"{tracker_url}?{query}"


def announce(
    tracker_url: str,
    info_hash: bytes,
    peer_id: bytes,
    port: int,
    uploaded: int = 0,
    downloaded: int = 0,
    left: int = 0,
    event: str = "",
    timeout: float = 5.0,
) -> dict:
    """Perform an HTTP GET announce request and return the decoded response."""
    url = build_announce_url(
        tracker_url, info_hash, peer_id, port, uploaded, downloaded, left, event
    )
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = resp.read()
    return bencode.decode(body)
