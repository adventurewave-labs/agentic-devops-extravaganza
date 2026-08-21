"""
Static file server for the showcase page.

`run.sh demo` referenced this file for months while it did not exist in the
repo; the shell script silently fell back to `python -m http.server`. It
exists now, and it does two things the fallback did not: it serves from the
repo root regardless of the caller's working directory, and it sets the
correct Content-Type for the assets the page loads.

Usage:
    python scripts/serve_site.py [PORT]
"""
import functools
import http.server
import socketserver
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".gif": "image/gif",
        ".json": "application/json",
        ".cast": "application/x-asciicast",
        ".md": "text/markdown; charset=utf-8",
        ".svg": "image/svg+xml",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        print(f"[site] {fmt % args}", file=sys.stderr, flush=True)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else paths.SITE_PORT
    handler = functools.partial(Handler, directory=str(paths.ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"[site] serving {paths.ROOT} on http://127.0.0.1:{port}",
              file=sys.stderr, flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
