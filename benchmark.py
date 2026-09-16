"""Benchmark: indexed vs. unindexed equality and range queries.

    python benchmark.py [--n 20000]
"""
import argparse
import os
import random
import tempfile
import time

from minidb.db import MiniDB


def timeit(fn, repeats=5):
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return min(times)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20000, help="Number of records to insert")
    args = parser.parse_args()

    path = os.path.join(tempfile.gettempdir(), "minidb_benchmark.db")
    if os.path.exists(path):
        os.remove(path)

    db = MiniDB(path, verbose=False)

    print(f"Inserting {args.n} records...")
    cities = ["NYC", "LA", "SF", "Chicago", "Austin"]
    for i in range(args.n):
        db.set(f"user{i}", {
            "age": random.randint(1, 90),
            "city": random.choice(cities),
        })

    target_city = "SF"
    target_age = 40

    unindexed_eq = timeit(lambda: db.find("city", "=", target_city))
    unindexed_range = timeit(lambda: db.find("age", ">=", target_age))

    db.create_index("city")
    db.create_index("age")

    indexed_eq = timeit(lambda: db.find("city", "=", target_city))
    indexed_range = timeit(lambda: db.find("age", ">=", target_age))

    print()
    header = f"{'Query':<28}{'Unindexed (s)':<16}{'Indexed (s)':<16}{'Speedup':<10}"
    print(header)
    print("-" * len(header))
    print(f"{'city = SF (equality)':<28}{unindexed_eq:<16.5f}{indexed_eq:<16.5f}"
          f"{unindexed_eq / max(indexed_eq, 1e-9):<10.1f}")
    print(f"{'age >= 40 (range)':<28}{unindexed_range:<16.5f}{indexed_range:<16.5f}"
          f"{unindexed_range / max(indexed_range, 1e-9):<10.1f}")

    os.remove(path)
    lock = path + ".lock"
    if os.path.exists(lock):
        os.remove(lock)


if __name__ == "__main__":
    main()
