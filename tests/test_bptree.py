import random

from minidb.bptree import BPlusTree


def test_insert_and_search_single():
    tree = BPlusTree(order=4)
    tree.insert(5, "recA")
    assert tree.search(5) == {"recA"}
    assert tree.search(6) == set()


def test_duplicate_keys_accumulate_record_keys():
    tree = BPlusTree(order=4)
    tree.insert(5, "recA")
    tree.insert(5, "recB")
    assert tree.search(5) == {"recA", "recB"}


def test_many_inserts_force_multiple_splits_and_stay_correct():
    tree = BPlusTree(order=4)  # small order -> lots of splits for n=200
    n = 200
    for i in range(n):
        tree.insert(i, f"rec{i}")

    assert tree.height() > 1  # confirms splits actually happened
    for i in range(n):
        assert tree.search(i) == {f"rec{i}"}
    assert tree.search(n + 1) == set()


def test_delete_removes_entry():
    tree = BPlusTree(order=4)
    for i in range(50):
        tree.insert(i, f"rec{i}")
    tree.delete(25, "rec25")
    assert tree.search(25) == set()
    assert tree.search(24) == {"rec24"}


def test_delete_one_of_multiple_record_keys_keeps_the_rest():
    tree = BPlusTree(order=4)
    tree.insert(5, "recA")
    tree.insert(5, "recB")
    tree.delete(5, "recA")
    assert tree.search(5) == {"recB"}


def test_range_queries_match_brute_force():
    random.seed(42)
    tree = BPlusTree(order=5)
    values = [random.randint(0, 500) for _ in range(300)]
    for i, v in enumerate(values):
        tree.insert(v, f"rec{i}")

    def brute_force(op, target):
        expected = set()
        for i, v in enumerate(values):
            if op == ">" and v > target:
                expected.add(f"rec{i}")
            elif op == ">=" and v >= target:
                expected.add(f"rec{i}")
            elif op == "<" and v < target:
                expected.add(f"rec{i}")
            elif op == "<=" and v <= target:
                expected.add(f"rec{i}")
        return expected

    for op in (">", ">=", "<", "<="):
        for target in (0, 1, 100, 250, 499, 500, 501):
            assert tree.range(op, target) == brute_force(op, target), (op, target)


def test_delete_rebalances_via_borrow_or_merge():
    """After deleting most entries, every remaining non-root node must
    still meet its minimum occupancy -- this is the structural proof that
    delete() actually rebalances (borrows/merges) instead of just
    leaving holes in underfull nodes."""
    tree = BPlusTree(order=4)
    n = 100
    for i in range(n):
        tree.insert(i, f"rec{i}")

    # Delete all but a handful of scattered keys.
    for i in range(n):
        if i % 7 != 0:
            tree.delete(i, f"rec{i}")

    tree.check_invariants()

    for i in range(n):
        expected = {f"rec{i}"} if i % 7 == 0 else set()
        assert tree.search(i) == expected


def test_delete_all_entries_collapses_to_empty_root():
    tree = BPlusTree(order=4)
    for i in range(30):
        tree.insert(i, f"rec{i}")
    for i in range(30):
        tree.delete(i, f"rec{i}")

    tree.check_invariants()
    assert isinstance(tree.root, type(tree.root))  # root still a valid node
    assert list(tree.all_items()) == []
    assert tree.height() == 1  # fully collapsed back down to a single leaf


def test_randomized_insert_delete_stress_maintains_invariants_and_correctness():
    random.seed(7)
    tree = BPlusTree(order=5)
    reference = {}  # key -> set of record_keys, mirrors the tree

    for _ in range(2000):
        key = random.randint(0, 200)
        record_key = f"rec{random.randint(0, 5)}"  # small pool -> forces duplicate-key handling

        if random.random() < 0.65:
            tree.insert(key, record_key)
            reference.setdefault(key, set()).add(record_key)
        else:
            tree.delete(key, record_key)
            if key in reference:
                reference[key].discard(record_key)
                if not reference[key]:
                    del reference[key]

    tree.check_invariants()

    for key in range(0, 201):
        assert tree.search(key) == reference.get(key, set()), key

    # Cross-check a range query too, not just point lookups.
    expected_ge_100 = {rk for k, rks in reference.items() if k >= 100 for rk in rks}
    assert tree.range(">=", 100) == expected_ge_100


def test_all_items_yields_sorted_order():
    tree = BPlusTree(order=4)
    values = [7, 2, 9, 1, 5, 3, 8, 4, 6, 0]
    for v in values:
        tree.insert(v, f"rec{v}")

    seen_keys = [k for k, _ in tree.all_items()]
    assert seen_keys == sorted(values)
