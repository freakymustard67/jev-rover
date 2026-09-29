"""W4C loopback mock vision server (stdlib http.server, 127.0.0.1 ephemeral port).

Modes (flip `srv.mode` at runtime):
  ok        -> 200, two valid detections
  slow      -> sleeps srv.sleep_s, then 200 (drives client timeout_s)
  http500   -> 500
  malformed -> 200 with invalid JSON
  schema    -> 200, {"detections": {...}} wrong top-level type
  badentry  -> 200, one entry with a degenerate bbox (x1<=x0) -> all-invalid

Every request is recorded in srv.requests (headers + body length) so the driver
can verify the Authorization header was actually sent without ever printing it.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OK_BODY = {
    "model": "mock",
    "ms": 1,
    "detections": [
        {"label": "mat", "bbox_px": [20, 30, 60, 70], "score": 0.91},
        {"label": "bottle", "bbox_px": [80, 10, 100, 40], "score": 0.77},
    ],
}
BAD_ENTRY_BODY = {
    "model": "mock",
    "detections": [{"label": "mat", "bbox_px": [5, 5, 5, 5], "score": 0.9}],
}


class MockVisionServer:
    def __init__(self):
        self.mode = "ok"
        self.sleep_s = 0.6
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):        # silence stderr noise
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(n)
                outer.requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization", ""),
                    "content_type": self.headers.get("Content-Type", ""),
                    "body_len": len(body),
                    "mode": outer.mode,
                })
                mode = outer.mode
                if mode == "slow":
                    time.sleep(outer.sleep_s)
                try:
                    if mode == "http500":
                        self._send(500, b"boom", "text/plain")
                    elif mode == "malformed":
                        self._send(200, b'{"detections": [oops', "application/json")
                    elif mode == "schema":
                        self._send(200, json.dumps({"detections": {"not": "a list"}}).encode(),
                                   "application/json")
                    elif mode == "badentry":
                        self._send(200, json.dumps(BAD_ENTRY_BODY).encode(), "application/json")
                    else:
                        self._send(200, json.dumps(OK_BODY).encode(), "application/json")
                except (BrokenPipeError, ConnectionResetError):
                    pass    # client timed out and closed; expected in 'slow' mode

            def _send(self, code: int, payload: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/detect"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> "MockVisionServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def dead_port() -> int:
    """Bind+close to get a 127.0.0.1 port that will refuse connections."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


if __name__ == "__main__":                        # manual smoke: python w4c_mockserver.py
    import urllib.request
    srv = MockVisionServer().start()
    print(f"mock listening on {srv.url}")
    req = urllib.request.Request(srv.url, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2.0) as r:
        print("status", r.status, r.read()[:80])
    srv.stop()
