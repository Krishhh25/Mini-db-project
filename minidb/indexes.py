"""Secondary index for MiniDB, backed by a real B+ tree (see bptree.py).

A single B+ tree handles both equality and range lookups: equality is
just search(key), range is range(op, key) -- both O(log n) (plus O(m) for
range, where m is the number of matches). This replaces an earlier
hash-map + sorted-list hybrid with one structure, which is closer to how
real databases actually implement B-tree indexes.
"""
from .bptree import BPlusTree


def _sort_key(value):
    """Normalize a value for consistent ordering inside the tree:
    numeric-looking values sort numerically, everything else falls back
    to string comparison. Returns a tuple so numbers and strings never
    get compared directly (which would raise in Python 3)."""
    try:
        return (0, float(value))
    except (TypeError, ValueError):
        return (1, str(value))


class FieldIndex:
    """Secondary index on one field of dict-shaped record values."""

    def __init__(self, field, order=32):
        self.field = field
        self._tree = BPlusTree(order=order)
        self._distinct = set()  # grows-only; used only for the human-readable count in logs

    def __len__(self):
        return len(self._distinct)

    def add(self, key, value):
        self._tree.insert(_sort_key(value), key)
        self._distinct.add(value)

    def remove(self, key, value):
        self._tree.delete(_sort_key(value), key)

    def eq(self, target):
        return self._tree.search(_sort_key(target))

    def range(self, op, target):
        return self._tree.range(op, _sort_key(target))

    def height(self):
        """The tree's current depth -- exposed mainly for tests/curiosity."""
        return self._tree.height()
