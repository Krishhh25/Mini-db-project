"""A small TCP server exposing MiniDB over the network, using the same
line-based command syntax as the REPL (see commands.py).

Multiple clients can connect concurrently; each connection gets its own
CommandProcessor (so BEGIN/COMMIT/ROLLBACK state is per-connection), but
all connections share one MiniDB instance, which is internally
thread-safe.

Wire protocol: client sends one command per line. Server replies with the
response text followed by a line containing exactly "END", so
multi-line responses (e.g. FIND with several matches) are unambiguous.

Optional auth: if the MINIDB_AUTH_TOKEN environment variable is set, a
freshly connected client must send "AUTH <token>" before anything else
is accepted. This is a bare-minimum shared-secret check, not real user
management -- see DEPLOY.md before exposing this to the public internet.

Usage:
    python -m minidb.server [--host 127.0.0.1] [--port 9999] [--file data.db]
"""
import argparse
import os
import socketserver

from .db import MiniDB
from .commands import CommandProcessor

AUTH_TOKEN = os.environ.get("MINIDB_AUTH_TOKEN")


class MiniDBHandler(socketserver.StreamRequestHandler):
    def handle(self):
        processor = CommandProcessor(self.server.db)
        authenticated = AUTH_TOKEN is None
        greeting = b"MiniDB server ready. Type HELP for commands."
        if not authenticated:
            greeting += b" Send: AUTH <token>"
        self._send(greeting)

        while True:
            line = self.rfile.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if text.upper() == "EXIT":
                self._send(b"Bye!")
                break

            if not authenticated:
                if text[:5].upper() == "AUTH ":
                    token = text.split(" ", 1)[1].strip()
                    if token == AUTH_TOKEN:
                        authenticated = True
                        self._send(b"OK: authenticated")
                    else:
                        self._send(b"Error: invalid token")
                else:
                    self._send(b"Error: authentication required. Send: AUTH <token>")
                continue

            response = processor.process(text)
            self._send(response.encode("utf-8"))

    def _send(self, payload: bytes):
        self.wfile.write(payload + b"\n")
        self.wfile.write(b"END\n")


class MiniDBServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, db):
        super().__init__(addr, MiniDBHandler)
        self.db = db


def main():
    parser = argparse.ArgumentParser(description="Run a MiniDB network server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--file", default="data.db")
    args = parser.parse_args()

    db = MiniDB(args.file, verbose=False)
    server = MiniDBServer((args.host, args.port), db)
    print(f"MiniDB server listening on {args.host}:{args.port} (file={args.file})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
