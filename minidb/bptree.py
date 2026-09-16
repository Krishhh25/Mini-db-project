"""A real B+ tree, used to back MiniDB's secondary indexes.

Unlike a plain sorted list, a B+ tree keeps insert/delete/search all at
O(log n) as the index grows, because it never has to shift O(n) elements
to keep things sorted -- each node holds a bounded number of entries, and
inserts only touch the O(log n) nodes on the path from the root down.

Structure:
  - Internal nodes hold separator keys and child pointers, and exist
    purely to route a search down to the right leaf.
  - Leaf nodes hold the actual (key -> set of record keys) entries, and
    are linked together in sorted order via `next`/`prev` pointers -- this
    is the classic B+ tree trick that makes range scans cheap: once you've
    located the start of a range in O(log n), you just walk the linked
    leaves instead of re-descending the tree for each subsequent match.
  - A node splits when it overflows `order` entries; the split propagates
    up, and if the root itself splits, a new root is created (the tree
    grows upward, keeping it balanced).

Deletion fully rebalances: when removing an entry drops a node below its
minimum occupancy, the tree first tries to borrow an entry from an
adjacent sibling (which only requires moving one key/child and adjusting
a separator), and falls back to merging with a sibling when borrowing
isn't possible. A merge can itself underflow the parent, so rebalancing
propagates upward exactly like insert's split propagates upward -- and if
the root's last two children get merged into one, that child becomes the
new root, shrinking the tree's height. This keeps node occupancy bounded
after heavy delete workloads, the same way a production B+ tree does.
"""
import bisect


class LeafNode:
    __slots__ = ("keys", "values", "next", "prev")

    def __init__(self):
        self.keys = []      # sorted list of distinct normalized keys
        self.values = []    # parallel list: values[i] = set of record keys for keys[i]
        self.next = None    # next leaf in sorted order (for forward range scans)
        self.prev = None    # previous leaf in sorted order (for backward range scans)

    def insert(self, key, record_key):
        idx = bisect.bisect_left(self.keys, key)
        if idx < len(self.keys) and self.keys[idx] == key:
            self.values[idx].add(record_key)
        else:
            self.keys.insert(idx, key)
            self.values.insert(idx, {record_key})

    def remove(self, key, record_key):
        idx = bisect.bisect_left(self.keys, key)
        if idx < len(self.keys) and self.keys[idx] == key:
            self.values[idx].discard(record_key)
            if not self.values[idx]:
                del self.keys[idx]
                del self.values[idx]

    def get(self, key):
        idx = bisect.bisect_left(self.keys, key)
        if idx < len(self.keys) and self.keys[idx] == key:
            return set(self.values[idx])
        return set()


class InternalNode:
    __slots__ = ("keys", "children")

    def __init__(self):
        self.keys = []       # separator keys, len == len(children) - 1
        self.children = []   # child nodes (LeafNode or InternalNode)


class BPlusTree:
    def __init__(self, order=32):
        if order < 3:
            raise ValueError("order must be >= 3")
        self.order = order          # max children per internal node / max entries per leaf
        self.root = LeafNode()

        # Minimum occupancy that triggers a borrow/merge on delete. These
        # follow the standard B+ tree rule of thumb: every non-root node
        # must stay at least half full. The root is exempt -- it's allowed
        # to be small (even a single leaf entry) since there's nothing to
        # merge it with.
        self.min_leaf = max(1, (order - 1) // 2)
        self.min_internal_children = max(2, (order + 1) // 2)

    # ---------- search ----------

    def _find_leaf(self, key):
        node = self.root
        while isinstance(node, InternalNode):
            idx = bisect.bisect_right(node.keys, key)
            node = node.children[idx]
        return node

    def search(self, key):
        """Exact-match lookup. O(log n)."""
        return self._find_leaf(key).get(key)

    def range(self, op, target):
        """Range lookup for op in {'>', '>=', '<', '<='}. O(log n) to find
        the starting leaf, then O(m) walking linked leaves in the
        direction of the scan, where m is the number of matches."""
        results = set()
        leaf = self._find_leaf(target)

        if op in (">", ">="):
            node = leaf
            while node is not None:
                for k, vals in zip(node.keys, node.values):
                    if (op == ">" and k > target) or (op == ">=" and k >= target):
                        results |= vals
                node = node.next
        elif op in ("<", "<="):
            node = leaf
            while node is not None:
                for k, vals in zip(node.keys, node.values):
                    if (op == "<" and k < target) or (op == "<=" and k <= target):
                        results |= vals
                node = node.prev
        else:
            raise ValueError(f"Unsupported range operator: {op}")
        return results

    # ---------- insert ----------

    def insert(self, key, record_key):
        result = self._insert(self.root, key, record_key)
        if result is not None:
            sep_key, new_node = result
            new_root = InternalNode()
            new_root.keys = [sep_key]
            new_root.children = [self.root, new_node]
            self.root = new_root

    def _insert(self, node, key, record_key):
        if isinstance(node, LeafNode):
            node.insert(key, record_key)
            if len(node.keys) > self.order - 1:
                return self._split_leaf(node)
            return None

        idx = bisect.bisect_right(node.keys, key)
        result = self._insert(node.children[idx], key, record_key)
        if result is not None:
            sep_key, new_child = result
            node.keys.insert(idx, sep_key)
            node.children.insert(idx + 1, new_child)
            if len(node.children) > self.order:
                return self._split_internal(node)
        return None

    def _split_leaf(self, leaf):
        mid = len(leaf.keys) // 2
        new_leaf = LeafNode()
        new_leaf.keys = leaf.keys[mid:]
        new_leaf.values = leaf.values[mid:]
        leaf.keys = leaf.keys[:mid]
        leaf.values = leaf.values[:mid]

        new_leaf.next = leaf.next
        if new_leaf.next is not None:
            new_leaf.next.prev = new_leaf
        new_leaf.prev = leaf
        leaf.next = new_leaf

        return (new_leaf.keys[0], new_leaf)

    def _split_internal(self, node):
        mid = len(node.keys) // 2
        sep_key = node.keys[mid]
        new_node = InternalNode()
        new_node.keys = node.keys[mid + 1:]
        new_node.children = node.children[mid + 1:]
        node.keys = node.keys[:mid]
        node.children = node.children[:mid + 1]
        return (sep_key, new_node)

    # ---------- delete ----------

    def delete(self, key, record_key):
        """Remove record_key from key's bucket, rebalancing (borrow or
        merge) any node that drops below minimum occupancy as a result."""
        self._delete(self.root, key, record_key)
        # If the root is an internal node that's been merged down to a
        # single child, that child becomes the new root -- this is how
        # the tree's height shrinks back down after enough deletes.
        if isinstance(self.root, InternalNode) and len(self.root.children) == 1:
            self.root = self.root.children[0]

    def _delete(self, node, key, record_key):
        """Delete record_key from key's bucket somewhere in node's
        subtree. Returns True if `node` itself is left underflowing
        (below minimum occupancy) so the caller can rebalance it."""
        if isinstance(node, LeafNode):
            node.remove(key, record_key)
            return node is not self.root and len(node.keys) < self.min_leaf

        idx = bisect.bisect_right(node.keys, key)
        if self._delete(node.children[idx], key, record_key):
            return self._rebalance_child(node, idx)
        return False

    def _rebalance_child(self, node, idx):
        """node.children[idx] is underflowing. Try borrowing a single
        entry from an adjacent sibling first (cheap, no further
        propagation needed); fall back to merging with a sibling, which
        may leave `node` itself underflowing -- returned to the caller so
        rebalancing can continue up the tree."""
        child = node.children[idx]
        left = node.children[idx - 1] if idx > 0 else None
        right = node.children[idx + 1] if idx + 1 < len(node.children) else None

        if isinstance(child, LeafNode):
            if left is not None and len(left.keys) > self.min_leaf:
                child.keys.insert(0, left.keys.pop())
                child.values.insert(0, left.values.pop())
                node.keys[idx - 1] = child.keys[0]
                return False
            if right is not None and len(right.keys) > self.min_leaf:
                child.keys.append(right.keys.pop(0))
                child.values.append(right.values.pop(0))
                node.keys[idx] = right.keys[0]
                return False

            if left is not None:
                left.keys.extend(child.keys)
                left.values.extend(child.values)
                left.next = child.next
                if left.next is not None:
                    left.next.prev = left
                del node.keys[idx - 1]
                del node.children[idx]
            else:
                child.keys.extend(right.keys)
                child.values.extend(right.values)
                child.next = right.next
                if child.next is not None:
                    child.next.prev = child
                del node.keys[idx]
                del node.children[idx + 1]
        else:
            if left is not None and len(left.children) > self.min_internal_children:
                child.keys.insert(0, node.keys[idx - 1])
                node.keys[idx - 1] = left.keys.pop()
                child.children.insert(0, left.children.pop())
                return False
            if right is not None and len(right.children) > self.min_internal_children:
                child.keys.append(node.keys[idx])
                node.keys[idx] = right.keys.pop(0)
                child.children.append(right.children.pop(0))
                return False

            if left is not None:
                left.keys.append(node.keys[idx - 1])
                left.keys.extend(child.keys)
                left.children.extend(child.children)
                del node.keys[idx - 1]
                del node.children[idx]
            else:
                child.keys.append(node.keys[idx])
                child.keys.extend(right.keys)
                child.children.extend(right.children)
                del node.keys[idx]
                del node.children[idx + 1]

        return node is not self.root and len(node.children) < self.min_internal_children

    # ---------- introspection (debugging / tests) ----------

    def height(self):
        depth = 1
        node = self.root
        while isinstance(node, InternalNode):
            depth += 1
            node = node.children[0]
        return depth

    def check_invariants(self):
        """Walk the whole tree verifying every non-root node meets its
        minimum occupancy, and that every internal node's children/keys
        counts are consistent. Raises AssertionError on the first
        violation found. Used by tests to prove delete() actually
        rebalances the tree rather than just leaving underfull nodes."""
        def walk(node, is_root):
            if isinstance(node, LeafNode):
                if not is_root:
                    assert len(node.keys) >= self.min_leaf, (
                        f"leaf underflow: {len(node.keys)} < {self.min_leaf}")
                assert len(node.keys) == len(node.values)
            else:
                if not is_root:
                    assert len(node.children) >= self.min_internal_children, (
                        f"internal underflow: {len(node.children)} < "
                        f"{self.min_internal_children}")
                assert len(node.children) == len(node.keys) + 1, (
                    "internal node children/keys count mismatch")
                for child in node.children:
                    walk(child, False)

        walk(self.root, True)
        return True

    def all_items(self):
        """Yield (key, record_key) pairs in sorted order, by walking the
        leaf linked list -- used for testing and debugging."""
        node = self.root
        while isinstance(node, InternalNode):
            node = node.children[0]
        while node is not None:
            for k, vals in zip(node.keys, node.values):
                for rk in vals:
                    yield (k, rk)
            node = node.next
