# MiniDB

A key-value database engine built from scratch in Python — no third-party
dependencies in the core engine. MiniDB implements the internals real
databases rely on: an append-only log for durability, an in-memory index
for O(1) lookups, checksummed records with crash recovery, secondary
indexes with range queries, TTL expiry, transactions, concurrency-safe
locking, and a network server so it can be run like a real client-server
database.

## Why I built this

Most projects reach for SQLite or Postgres without ever seeing what's
underneath. MiniDB is my attempt to build those internals myself and
understand *why* databases are built the way they are: why point lookups
can be O(1), how a database survives a crash mid-write, how secondary
indexes trade memory for query speed, why compaction exists, and what it
actually takes to expose a data store safely to multiple clients at once.

## Features

- **Durable, append-only storage** — every write is appended as a JSON
  record to a log file; nothing is ever overwritten in place.
- **O(1) key lookups** — an in-memory primary index maps each key to its
  byte offset in the log, so `GET` seeks directly to the record instead
  of scanning the file.
- **Crash-safe recovery** — every record carries a CRC32 checksum.
  Corrupt or truncated lines (e.g. from a crash mid-write) are detected
  and skipped during startup instead of crashing the process.
- **fsync'd writes** — every write is flushed and `fsync`'d before the
  in-memory index is updated, so acknowledged writes survive a crash.
- **Secondary indexes backed by a real, self-rebalancing B+ tree** —
  `INDEX age` builds an actual B+ tree (internal nodes + doubly-linked
  leaves), giving O(log n) equality *and* range queries (`>`, `<`, `>=`,
  `<=`), kept incrementally in sync on every write. Deletes fully
  rebalance the tree (borrowing from a sibling, or merging siblings and
  propagating the merge upward) rather than leaving underfull nodes, the
  same way a production B-tree index does.
- **Composite queries** — `FIND age>18 AND city=NYC` combines multiple
  conditions, narrowing candidates through any available indexes first.
- **TTL / expiring keys** — `SET session x=1 --ttl=30` expires a key
  after 30 seconds; expiry is checked lazily on read.
- **Transactions** — `db.transaction()` batches multiple `SET`/`DELETE`
  calls so they're staged in memory and only hit disk if the block
  completes without raising.
- **Concurrency-safe** — an in-process reentrant lock plus an advisory
  file lock (`fcntl.flock`) mean one `MiniDB` instance can be shared
  safely across threads, and multiple processes won't corrupt the same
  file.
- **Auto-compaction** — the log is compacted automatically once the
  dead-record ratio crosses a configurable threshold, reclaiming space
  from deleted/overwritten records without manual intervention.
- **Network server** — `python -m minidb.server` exposes MiniDB over TCP
  with the same command syntax as the REPL, so it can run like a real
  client-server database (a toy Redis, essentially). Supports optional
  token auth via `MINIDB_AUTH_TOKEN` for when it's exposed beyond
  localhost.
- **Browser-friendly HTTP demo** — `web/app.py` is a small Flask wrapper
  around the same engine, with a terminal-style page you can open and
  click around in. This is what you'd actually deploy for a link on a
  resume, since the TCP server above needs a socket client. See
  [DEPLOY.md](DEPLOY.md) for both options.
- **Interactive REPL** — a command-line shell with history/line-editing,
  transactions, and a `HELP` command.
- **Fully tested** — 26 unit tests covering persistence, crash recovery,
  TTL, transactions, concurrent writes, and the B+ tree itself (including
  a randomized insert/delete stress test that verifies both correctness
  and that every node meets its minimum occupancy after rebalancing).

## How it works

MiniDB is a small log-structured storage engine:

1. **Writes are append-only.** `SET` and `DELETE` never touch existing
   bytes — they append a new JSON record (or a `deleted: true` tombstone)
   to the end of the log, `fsync`'d before the write is acknowledged.
2. **The primary index points at the latest version.** Every write
   updates an in-memory `key -> byte offset` dict, so `GET` never scans —
   it seeks straight to the offset and reads one line.
3. **Every record is checksummed.** A CRC32 over the record's fields is
   stored alongside it. On startup (or if a read ever turns up a bad
   checksum), the corrupt line is skipped rather than trusted.
4. **Secondary indexes are B+ trees.** `INDEX <field>` builds a real B+
   tree over that field's values: internal nodes route a search down to
   the right leaf in O(log n), leaves hold the actual key → record-keys
   buckets and are linked together in sorted order. A search or range
   query descends once, then (for ranges) walks the linked leaves — no
   re-descending the tree per match. Inserts split overflowing nodes
   upward; deletes borrow from a sibling or merge siblings on underflow,
   propagating the merge upward (and shrinking the tree's height when the
   root itself collapses to one child) — the tree stays balanced in both
   directions, the same way a real database index does.
5. **Transactions stage, then flush.** Operations inside a
   `with db.transaction():` block are buffered in memory and only
   applied — under a single lock acquisition — if the block exits
   without an exception.
6. **Locking makes it safe to share.** A reentrant thread lock protects
   in-process concurrent access; an advisory `flock` on a sidecar lock
   file prevents two separate processes from writing at the same time.
7. **Compaction reclaims space.** Because writes are append-only, the
   log grows even when keys are overwritten or deleted. `COMPACT`
   rewrites the file keeping only the current live value per key; this
   also runs automatically once dead records make up too much of the
   log.

## Project structure

```
.
├── minidb/
│   ├── __init__.py     # public API exports
│   ├── db.py           # core engine: log storage, indexing, TTL, locking, transactions
│   ├── bptree.py         # real B+ tree: internal nodes, linked leaves, split-on-insert
│   ├── indexes.py       # FieldIndex: thin wrapper mapping field values onto the B+ tree
│   ├── commands.py      # shared command parser/executor (used by REPL and server)
│   └── server.py        # TCP server exposing MiniDB over the network
├── repl.py               # interactive command-line shell
├── client.py             # interactive client for the network server
├── web/
│   ├── app.py             # Flask HTTP wrapper (deployable, browser-friendly)
│   ├── requirements.txt
│   └── static/index.html   # terminal-style demo page
├── benchmark.py           # indexed vs. unindexed query benchmark
├── tests/
│   ├── test_db.py          # core engine tests
│   ├── test_bptree.py      # B+ tree tests (incl. rebalancing stress test)
│   └── test_web.py         # HTTP wrapper tests
├── Dockerfile              # runs the TCP server (minidb.server)
├── Dockerfile.web          # runs the HTTP demo (web/app.py)
├── DEPLOY.md               # how to put either one online
├── requirements.txt
└── README.md
```

## Getting started

```bash
python repl.py
```

Starts an interactive shell backed by `data.db` (created automatically).

### Run the network server

```bash
python -m minidb.server --port 9999 --file data.db
```

In another terminal:

```bash
python client.py --port 9999
```

Multiple clients can connect to the same server at once.

### Run the browser-friendly HTTP demo

```bash
pip install -r web/requirements.txt
python web/app.py
```
Open `http://localhost:8080` — a terminal-style page where you can type
commands directly, backed by the same engine underneath.

### Deploy it somewhere live

See [DEPLOY.md](DEPLOY.md) for a quick ngrok demo, a plain VPS + systemd
setup, a Dockerized deploy to Fly.io, or (recommended for a link you can
actually share) deploying the HTTP demo to a free host like Render.

### Run the tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

### Run the benchmark

```bash
python benchmark.py --n 20000
```

## Usage

```
Commands:
  SET <key> <field=value> [field=value ...] [--ttl=seconds]
  GET <key>
  DELETE <key>
  FIND <field><op><value> [AND <field><op><value> ...]   e.g. FIND age>18 AND city=NYC
  INDEX <field>                                          e.g. INDEX age
  LIST
  COMPACT
  BEGIN / COMMIT / ROLLBACK    (batch SET/DELETE atomically)
  HELP
  EXIT
```

### Example session

```
minidb> SET user1 name=Alice age=30 city=NYC
Set user1 = {'name': 'Alice', 'age': 30, 'city': 'NYC'}

minidb> SET user2 name=Bob age=17 city=LA
Set user2 = {'name': 'Bob', 'age': 17, 'city': 'LA'}

minidb> INDEX city
Index created on 'city' (2 distinct values)

minidb> FIND city=NYC AND age>=18
user1: {'name': 'Alice', 'age': 30, 'city': 'NYC'}

minidb> SET session1 token=abc123 --ttl=2
Set session1 = {'token': 'abc123'} (ttl=2.0s)

minidb> GET session1
{'token': 'abc123'}
(...2 seconds later...)
minidb> GET session1
(nil)

minidb> BEGIN
OK: transaction started
minidb> SET user3 name=Carol age=40 city=SF
minidb> DELETE user2
minidb> COMMIT
OK: transaction committed

minidb> LIST
['user1', 'user3', 'session1']

minidb> COMPACT
OK
```

## Using MiniDB programmatically

```python
from minidb.db import MiniDB

db = MiniDB("data.db")

db.set("user1", {"name": "Alice", "age": 30})
db.set("session1", {"token": "abc123"}, ttl=30)   # expires in 30s

db.create_index("age")
db.find("age", ">=", 18)                           # range query via index
db.find_where([("age", ">=", 18), ("city", "=", "NYC")])  # composite AND

with db.transaction() as txn:
    txn.set("user2", {"name": "Bob", "age": 25})
    txn.delete("user1")
# both writes are applied together, or neither if an exception is raised

db.compact()
```

## Design notes / trade-offs

- **Single log file per database.** There's no sharding or multi-file
  storage — this is a teaching/portfolio project, not a production
  engine.
- **Range queries are O(log n + m).** The B+ tree gets you to the start
  of a range in O(log n), then walking linked leaves costs O(m) for the
  m matches — inherent to any range query (you have to return the
  matches), not a shortcut that could be optimized away.
- **Transactions are staged in memory, not WAL-based.** If the process
  crashes *while* a committed transaction's staged writes are being
  flushed to disk, the transaction may be partially applied. Full ACID
  durability would need a proper write-ahead log — noted as a natural
  next step.
- **Locking is advisory (`flock`), not mandatory.** A process that
  doesn't use MiniDB's `_file_lock` could still write to the file
  directly and bypass it — fine for this project's scope, worth knowing
  as a limitation.
- **Values must be JSON-serializable.** Since records persist as JSON
  lines, only JSON-compatible types are supported.

## Possible next steps

- Write-ahead log for fully crash-safe transactions
- Composite (multi-field) indexes, so `FIND a=X AND b=Y` can use one
  index instead of intersecting two
- An `EXPLAIN` command showing which index (if any) a query will use
- Binary record format instead of JSON lines, for smaller files and faster parsing
- Replication (a second MiniDB instance following the log as a read replica)
- A proper wire protocol (e.g. RESP-like) instead of line-based text, for a real client library

## License

MIT
