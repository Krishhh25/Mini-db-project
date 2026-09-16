import json
import threading
import time

import pytest

from minidb.db import MiniDB


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


def test_set_and_get(db_path):
    db = MiniDB(db_path, verbose=False)
    db.set("user1", {"name": "Alice", "age": 30})
    assert db.get("user1") == {"name": "Alice", "age": 30}


def test_get_missing_key_returns_none(db_path):
    db = MiniDB(db_path, verbose=False)
    assert db.get("nope") is None


def test_delete(db_path):
    db = MiniDB(db_path, verbose=False)
    db.set("k", {"a": 1})
    db.delete("k")
    assert db.get("k") is None
    assert "k" not in db.keys()


def test_overwrite_keeps_latest_value(db_path):
    db = MiniDB(db_path, verbose=False)
    db.set("k", {"a": 1})
    db.set("k", {"a": 2})
    assert db.get("k") == {"a": 2}


def test_persistence_across_reopen(db_path):
    db = MiniDB(db_path, verbose=False)
    db.set("k1", {"a": 1})
    db.set("k2", {"a": 2})
    db.delete("k2")
    del db

    reopened = MiniDB(db_path, verbose=False)
    assert reopened.get("k1") == {"a": 1}
    assert reopened.get("k2") is None
    assert reopened.keys() == ["k1"]


def test_ttl_expiry(db_path):
    db = MiniDB(db_path, verbose=False)
    db.set("temp", {"a": 1}, ttl=0.05)
    assert db.get("temp") == {"a": 1}
    time.sleep(0.15)
    assert db.get("temp") is None


def test_corrupt_line_is_skipped_not_fatal(db_path):
    db = MiniDB(db_path, verbose=False)
    db.set("good", {"a": 1})

    with open(db_path, "a") as f:
        f.write("{not valid json\n")
        f.write(json.dumps({"key": "tampered", "value": {"a": 1},
                             "deleted": False, "crc": 0}) + "\n")

    reopened = MiniDB(db_path, verbose=False)
    assert reopened.get("good") == {"a": 1}
    assert reopened.get("tampered") is None
    assert reopened.corrupt_lines_skipped == 2


def test_compact_removes_dead_records(db_path):
    db = MiniDB(db_path, verbose=False)
    for i in range(5):
        db.set("k", {"a": i})
    db.compact()
    with open(db_path) as f:
        lines = [l for l in f if l.strip()]
    assert len(lines) == 1
    assert db.get("k") == {"a": 4}


def test_auto_compact_triggers(db_path):
    db = MiniDB(db_path, auto_compact_min_dead=3, auto_compact_dead_ratio=0.5, verbose=False)
    for i in range(10):
        db.set("k", {"a": i})
    with open(db_path) as f:
        lines = [l for l in f if l.strip()]
    assert len(lines) < 10
    assert db.get("k") == {"a": 9}


def test_secondary_index_equality(db_path):
    db = MiniDB(db_path, verbose=False)
    db.set("u1", {"city": "NYC"})
    db.set("u2", {"city": "LA"})
    db.set("u3", {"city": "NYC"})
    db.create_index("city")
    results = db.find("city", "=", "NYC")
    assert set(results.keys()) == {"u1", "u3"}


def test_secondary_index_range(db_path):
    db = MiniDB(db_path, verbose=False)
    for i, age in enumerate([15, 20, 25, 30, 35]):
        db.set(f"u{i}", {"age": age})
    db.create_index("age")
    results = db.find("age", ">=", 25)
    assert set(results.keys()) == {"u2", "u3", "u4"}


def test_composite_and_query(db_path):
    db = MiniDB(db_path, verbose=False)
    db.set("u1", {"city": "NYC", "age": 30})
    db.set("u2", {"city": "NYC", "age": 15})
    db.set("u3", {"city": "LA", "age": 30})
    db.create_index("city")
    results = db.find_where([("city", "=", "NYC"), ("age", ">=", 18)])
    assert set(results.keys()) == {"u1"}


def test_index_stays_correct_after_overwrite(db_path):
    db = MiniDB(db_path, verbose=False)
    db.set("u1", {"city": "NYC"})
    db.create_index("city")
    db.set("u1", {"city": "LA"})
    assert db.find("city", "=", "NYC") == {}
    assert set(db.find("city", "=", "LA").keys()) == {"u1"}


def test_transaction_commit(db_path):
    db = MiniDB(db_path, verbose=False)
    db.set("existing", {"a": 1})
    with db.transaction() as txn:
        txn.set("new", {"a": 2})
        txn.delete("existing")
    assert db.get("new") == {"a": 2}
    assert db.get("existing") is None


def test_transaction_rollback_on_exception(db_path):
    db = MiniDB(db_path, verbose=False)
    with pytest.raises(ValueError):
        with db.transaction() as txn:
            txn.set("ghost", {"a": 1})
            raise ValueError("boom")
    assert db.get("ghost") is None


def test_concurrent_writes_from_threads(db_path):
    db = MiniDB(db_path, verbose=False)

    def worker(n):
        for i in range(20):
            db.set(f"k{n}-{i}", {"n": n, "i": i})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(db.keys()) == 80
