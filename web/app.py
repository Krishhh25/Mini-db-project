"""A small HTTP wrapper around MiniDB, so it can be deployed as an
ordinary web app (something a browser -- or a recruiter clicking a link
-- can actually open) instead of requiring a raw TCP client.

Internally this reuses the exact same CommandProcessor that the REPL and
the TCP server use (see minidb/commands.py), so the command syntax is
identical everywhere: SET/GET/DELETE/FIND/INDEX/LIST/COMPACT.

One deliberate simplification versus the TCP server: HTTP requests are
stateless, so BEGIN/COMMIT/ROLLBACK (which need a session) aren't
supported here -- use repl.py or client.py for transactions.

Run locally:
    python web/app.py

Environment variables:
    MINIDB_FILE        path to the database file (default: web_data.db)
    MINIDB_AUTH_TOKEN   if set, requests must send header X-API-Token
    PORT                port to listen on (default: 8080)
"""
import os
import sys

# Allow running this file directly (`python web/app.py`) without installing
# the project as a package -- put the repo root on sys.path so `import
# minidb` works regardless of the current working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, send_from_directory

from minidb.db import MiniDB
from minidb.commands import CommandProcessor, HELP_TEXT

DB_FILE = os.environ.get("MINIDB_FILE", "web_data.db")
AUTH_TOKEN = os.environ.get("MINIDB_AUTH_TOKEN")

app = Flask(__name__, static_folder="static", static_url_path="")
db = MiniDB(DB_FILE, verbose=False)

_BLOCKED_STATEFUL_COMMANDS = ("BEGIN", "COMMIT", "ROLLBACK")


def _authorized():
    if AUTH_TOKEN is None:
        return True
    return request.headers.get("X-API-Token", "") == AUTH_TOKEN


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/help")
def help_route():
    return jsonify({"help": HELP_TEXT})


@app.route("/api/command", methods=["POST"])
def run_command():
    if not _authorized():
        return jsonify({"error": "Unauthorized. Send header: X-API-Token"}), 401

    payload = request.get_json(silent=True) or {}
    cmd_text = (payload.get("command") or "").strip()
    if not cmd_text:
        return jsonify({"error": "Missing 'command'"}), 400

    if cmd_text.split()[0].upper() in _BLOCKED_STATEFUL_COMMANDS:
        return jsonify({
            "result": "Transactions aren't supported over this stateless web "
                       "demo. Use repl.py or client.py for BEGIN/COMMIT/ROLLBACK."
        })

    # A fresh CommandProcessor per request -- MiniDB itself is thread-safe,
    # and this avoids any shared per-connection state across requests.
    processor = CommandProcessor(db)
    result = processor.process(cmd_text)
    return jsonify({"result": result})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
