"""MiniDB: a small log-structured key-value store.

Design:
  - An append-only log of JSON records (one per line) is the source of
    truth. Writes never modify existing bytes -- SET and DELETE both just
    append a new record (DELETE appends a tombstone).
  - An in-memory primary index (key -> byte offset of its latest record)
    gives O(1) point lookups without scanning the file.
  - Secondary indexes (see indexes.py) give O(1) equality and O(log n)
    range lookups on fields of dict-shaped values.
  - Every record carries a CRC32 checksum. Corrupt or truncated lines
    (e.g. from a crash mid-write) are detected during recovery and
    skipped instead of crashing the process.
  - Keys can have a TTL; expired keys are treated as deleted lazily, the
    first time they're read after expiring.
  - Writes are protected by a reentrant thread lock plus an advisory
    file lock, so one MiniDB instance can be safely shared across threads
    (see server.py) and multiple processes won't corrupt the same file.
  - The log is compacted (dead records dropped) either manually via
    compact() or automatically once the dead-record ratio crosses a
    configurable threshold.
"""

import json
import os
import time
import threading
import zlib
import contextlib

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # e.g. Windows
    HAS_FCNTL = False

from .indexes import FieldIndex


class MiniDBError(Exception):
    pass


class CorruptRecordError(MiniDBError):
    pass


def _now():
    return time.time()


def _condition_matches(value, field, op, target):
    if field not in value:
        return False
    record_val = value[field]
    try:
        a, b = float(record_val), float(target)
    except (TypeError, ValueError):
        a, b = record_val, target

    if op == "=":
        return a == b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    raise ValueError(f"Unsupported operator: {op}")


def _matches_all(value, conditions):
    return all(_condition_matches(value, f, op, t) for f, op, t in conditions)


class MiniDB:
    def __init__(self, filename="data.db",
                 auto_compact_dead_ratio=0.5,
                 auto_compact_min_dead=50,
                 verbose=True):
        self.filename = filename
        self.lockfile = filename + ".lock"
        self.verbose = verbose

        self.index = {}            # key -> byte offset of latest live record
        self.field_indexes = {}    # field_name -> FieldIndex
        self._values_cache = {}    # key -> last known value (perf: avoids a disk read on writes)

        self.auto_compact_dead_ratio = auto_compact_dead_ratio
        self.auto_compact_min_dead = auto_compact_min_dead
        self._total_records = 0
        self._dead_records = 0
        self.corrupt_lines_skipped = 0

        self._thread_lock = threading.RLock()
        self._file_lock_fd = None
        self._file_lock_depth = 0

        if not os.path.exists(self.filename):
            open(self.filename, "w").close()
        open(self.lockfile, "a").close()

        self._rebuild_index()

    # ---------- internal: locking ----------

    @contextlib.contextmanager
    def _file_lock(self):
        """Reentrant advisory exclusive lock so multiple processes sharing
        this file don't interleave writes. Safe to nest (e.g. transaction()
        calling set()/delete() internally). No-op on platforms without fcntl."""
        if not HAS_FCNTL:
            yield
            return
        with self._thread_lock:
            if self._file_lock_depth == 0:
                self._file_lock_fd = open(self.lockfile, "w")
                fcntl.flock(self._file_lock_fd, fcntl.LOCK_EX)
            self._file_lock_depth += 1
        try:
            yield
        finally:
            with self._thread_lock:
                self._file_lock_depth -= 1
                if self._file_lock_depth == 0:
                    fcntl.flock(self._file_lock_fd, fcntl.LOCK_UN)
                    self._file_lock_fd.close()
                    self._file_lock_fd = None

    def _log(self, msg):
        if self.verbose:
            print(msg)

    # ---------- internal: record encode/decode/checksum ----------

    def _checksum(self, record_sans_crc):
        payload = json.dumps(record_sans_crc, sort_keys=True).encode("utf-8")
        return zlib.crc32(payload)

    def _encode(self, key, value, deleted=False, expires_at=None):
        rec = {"key": key, "value": value, "deleted": deleted}
        if expires_at is not None:
            rec["expires_at"] = expires_at
        rec["crc"] = self._checksum(rec)
        return rec

    def _decode_and_validate(self, line):
        """Parse a line and verify its checksum. Returns None (and counts
        it toward corrupt_lines_skipped) if the line is corrupt, truncated,
        or tampered with."""
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            self.corrupt_lines_skipped += 1
            return None

        if "crc" not in record or "key" not in record:
            self.corrupt_lines_skipped += 1
            return None

        crc = record.pop("crc")
        expected = self._checksum(record)
        record["crc"] = crc
        if crc != expected:
            self.corrupt_lines_skipped += 1
            return None
        return record

    # ---------- internal: recovery ----------

    def _rebuild_index(self):
        """Scan the file once at startup to build the primary index in
        memory, skipping any corrupt/truncated lines along the way."""
        self.index.clear()
        self._values_cache.clear()
        self._total_records = 0
        self._dead_records = 0
        self.corrupt_lines_skipped = 0

        with open(self.filename, "r") as f:
            while True:
                offset = f.tell()
                line = f.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                record = self._decode_and_validate(line)
                if record is None:
                    continue

                self._total_records += 1
                key = record["key"]
                is_expired = (record.get("expires_at") is not None
                              and record["expires_at"] < _now())

                if record.get("deleted") or is_expired:
                    if key in self.index:
                        self._dead_records += 1
                    self.index.pop(key, None)
                    self._values_cache.pop(key, None)
                else:
                    if key in self.index:
                        self._dead_records += 1  # previous version is now dead
                    self.index[key] = offset
                    self._values_cache[key] = record["value"]

        if self.corrupt_lines_skipped:
            self._log(f"Warning: skipped {self.corrupt_lines_skipped} "
                       f"corrupt/truncated record(s) during recovery.")

    # ---------- internal: secondary index maintenance ----------

    def _index_add(self, key, value):
        if not isinstance(value, dict):
            return
        for field, findex in self.field_indexes.items():
            if field in value:
                findex.add(key, value[field])

    def _index_remove(self, key, value):
        if not isinstance(value, dict):
            return
        for field, findex in self.field_indexes.items():
            if field in value:
                findex.remove(key, value[field])

    # ---------- public API: reads/writes ----------

    def set(self, key, value, ttl=None):
        """Set key to value. value should be JSON-serializable; dict values
        can be targeted by secondary indexes. ttl is optional seconds until
        the key expires."""
        with self._thread_lock, self._file_lock():
            old_value = self._values_cache.get(key)
            expires_at = _now() + ttl if ttl is not None else None
            record = self._encode(key, value, deleted=False, expires_at=expires_at)

            with open(self.filename, "a") as f:
                offset = f.tell()
                f.write(json.dumps(record) + "\n")
                f.flush()
                os.fsync(f.fileno())

            if key in self.index:
                self._dead_records += 1
            self.index[key] = offset
            self._values_cache[key] = value
            self._total_records += 1

            if old_value is not None:
                self._index_remove(key, old_value)
            self._index_add(key, value)

            self._log(f"Set {key} = {value}" + (f" (ttl={ttl}s)" if ttl else ""))
            self._maybe_auto_compact()

    def get(self, key):
        """O(1) point lookup using the primary index. Returns None if the
        key is missing, was deleted, has expired, or its record is
        corrupt."""
        with self._thread_lock:
            if key not in self.index:
                return None
            offset = self.index[key]
            with open(self.filename, "r") as f:
                f.seek(offset)
                line = f.readline()
            record = self._decode_and_validate(line)
            if record is None:
                self.index.pop(key, None)
                self._values_cache.pop(key, None)
                return None

            if record.get("expires_at") is not None and record["expires_at"] < _now():
                old_value = self._values_cache.get(key)
                self.index.pop(key, None)
                self._values_cache.pop(key, None)
                self._dead_records += 1
                if old_value is not None:
                    self._index_remove(key, old_value)
                return None

            return record["value"]

    def delete(self, key):
        with self._thread_lock, self._file_lock():
            old_value = self._values_cache.get(key)
            if old_value is None and key not in self.index:
                self._log(f"Delete {key}: (nil, nothing to do)")
                return

            record = self._encode(key, None, deleted=True)
            with open(self.filename, "a") as f:
                f.write(json.dumps(record) + "\n")
                f.flush()
                os.fsync(f.fileno())

            self.index.pop(key, None)
            self._values_cache.pop(key, None)
            self._total_records += 1
            self._dead_records += 1
            if old_value is not None:
                self._index_remove(key, old_value)

            self._log(f"Deleted {key}")
            self._maybe_auto_compact()

    def compact(self):
        """Rewrite the file keeping only the latest live value for each key."""
        with self._thread_lock, self._file_lock():
            temp_filename = self.filename + ".compact"
            new_index = {}
            with open(temp_filename, "w") as temp_f:
                for key, offset in self.index.items():
                    with open(self.filename, "r") as f:
                        f.seek(offset)
                        line = f.readline()
                    record = self._decode_and_validate(line)
                    if record is None or record.get("deleted"):
                        continue
                    new_offset = temp_f.tell()
                    temp_f.write(json.dumps(record) + "\n")
                    new_index[key] = new_offset

            os.replace(temp_filename, self.filename)
            self.index = new_index
            self._total_records = len(new_index)
            self._dead_records = 0
            self._log(f"Compacted. Live keys: {list(self.index.keys())}")

    def _maybe_auto_compact(self):
        if self._total_records == 0:
            return
        ratio = self._dead_records / self._total_records
        if (self._dead_records >= self.auto_compact_min_dead
                and ratio >= self.auto_compact_dead_ratio):
            self._log(f"Auto-compacting (dead ratio {ratio:.0%})...")
            self.compact()

    def keys(self):
        with self._thread_lock:
            return list(self.index.keys())

    def get_all(self):
        with self._thread_lock:
            return {key: self.get(key) for key in list(self.index.keys())}

    # ---------- secondary indexes & querying ----------

    def create_index(self, field):
        """Build a secondary index on `field` by scanning all current
        records once. Kept incrementally in sync afterward."""
        with self._thread_lock:
            findex = FieldIndex(field)
            for key, value in self.get_all().items():
                if isinstance(value, dict) and field in value:
                    findex.add(key, value[field])
            self.field_indexes[field] = findex
            self._log(f"Index created on '{field}' ({len(findex)} distinct values)")

    def find(self, field, op, target):
        """Single-condition query. Uses the secondary index (equality or
        range) when one exists on `field`; otherwise falls back to a full
        scan."""
        return self.find_where([(field, op, target)])

    def find_where(self, conditions):
        """Multi-condition AND query. conditions: list of (field, op, target).
        Uses any available secondary indexes to narrow the candidate set,
        then verifies every condition against the current value -- so
        results stay correct even when only some fields are indexed."""
        with self._thread_lock:
            candidate_keys = None
            for field, op, target in conditions:
                findex = self.field_indexes.get(field)
                if findex is None:
                    continue
                matched = findex.eq(target) if op == "=" else findex.range(op, target)
                candidate_keys = matched if candidate_keys is None else (candidate_keys & matched)

            if candidate_keys is None:
                candidate_keys = set(self.index.keys())

            results = {}
            for key in candidate_keys:
                value = self.get(key)
                if value is None or not isinstance(value, dict):
                    continue
                if _matches_all(value, conditions):
                    results[key] = value
            return results

    # ---------- transactions ----------

    @contextlib.contextmanager
    def transaction(self):
        """Batch multiple writes under a single lock acquisition.

        Usage:
            with db.transaction() as txn:
                txn.set("a", {...})
                txn.delete("b")

        Operations are staged in memory and only touch disk once the
        block exits normally; if the block raises, nothing is written.
        Note: reads (db.get) always see the last *committed* state, not
        pending writes inside an open transaction.
        """
        ops = []

        class _Txn:
            def set(_self, key, value, ttl=None):
                ops.append(("set", key, value, ttl))

            def delete(_self, key):
                ops.append(("delete", key))

        txn = _Txn()
        yield txn

        with self._thread_lock, self._file_lock():
            for op in ops:
                if op[0] == "set":
                    _, key, value, ttl = op
                    self.set(key, value, ttl=ttl)
                else:
                    _, key = op
                    self.delete(key)
