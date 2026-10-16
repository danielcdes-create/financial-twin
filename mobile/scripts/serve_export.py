"""Serve the local Expo export with compression and cache-safe headers."""
from __future__ import annotations

import argparse
import gzip
import io
import mimetypes
import re
from email.utils import formatdate
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HASHED_ASSET = re.compile(r"[-.][0-9a-f]{16,}[.]", re.IGNORECASE)
COMPRESSIBLE = {"text/html", "text/css", "text/javascript", "application/javascript", "application/json", "image/svg+xml"}


class ExportHandler(SimpleHTTPRequestHandler):
    root: Path

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            path = path / "index.html"
        if self.root not in path.resolve().parents:
            self.send_error(404, "File not found")
            return None
        # Expo Router's client routes must remain reachable after refreshing /account.
        if not path.is_file() and not path.suffix:
            path = self.root / "index.html"
        if not path.is_file():
            self.send_error(404, "File not found")
            return None
        stat = path.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return None
        content = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = "gzip" in self.headers.get("Accept-Encoding", "").casefold() and content_type in COMPRESSIBLE
        if encoded:
            content = gzip.compress(content, compresslevel=6)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Last-Modified", formatdate(stat.st_mtime, usegmt=True))
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if HASHED_ASSET.search(path.name) else "no-cache")
        if encoded:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        return io.BytesIO(content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--port", type=int, default=19006)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    root = args.directory.resolve()
    handler = lambda *values, **kwargs: ExportHandler(*values, directory=str(root), **kwargs)
    ExportHandler.root = root
    ThreadingHTTPServer((args.bind, args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
