"""Tiny fixture web server for crawler/audit tests: static files + a few redirect rules."""
import functools
import http.server
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).parent / "site"
REDIRECTS = {"/old-page": (302, "/old-page-2"), "/old-page-2": (301, "/contact.html")}
STATE = {"dyn_noindex": True}  # tests flip this to simulate a developer fixing a page


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in REDIRECTS:
            code, loc = REDIRECTS[self.path]
            self.send_response(code)
            self.send_header("Location", loc)
            self.end_headers()
            return
        path = self.path.split("?")[0]
        if path == "/dyn/landing.html":
            robots = '<meta name="robots" content="noindex">' if STATE["dyn_noindex"] else ""
            body = f"<!doctype html><html><head><title>Landing</title>{robots}</head><body><h1>Landing</h1></body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        f = ROOT / path.lstrip("/")
        if f.is_dir():
            f = f / "index.html"
        if f.is_file() and f.suffix in (".html", ".txt", ".xml"):
            host = self.headers.get("Host", "127.0.0.1:8765")
            body = f.read_text().replace("127.0.0.1:8765", host).encode()
            self.send_response(200)
            self.send_header("Content-Type", {".html": "text/html; charset=utf-8", ".txt": "text/plain", ".xml": "application/xml"}[f.suffix])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def end_headers(self):
        if self.path.endswith((".html", "/")):
            self.send_header("Cache-Control", "max-age=300")
        super().end_headers()


def start(port=8765):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(H, directory=str(ROOT)))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


if __name__ == "__main__":
    start(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
    threading.Event().wait()
