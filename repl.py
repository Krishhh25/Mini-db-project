"""Interactive command-line shell for MiniDB.

    python repl.py [--file data.db]

Type HELP once running for the full command list. Command history and
line-editing (arrow keys, Ctrl+R search) are enabled automatically on
platforms with readline.
"""
import argparse

try:
    import readline  # noqa: F401  (importing enables input() line-editing/history)
except ImportError:
    pass

from minidb.db import MiniDB
from minidb.commands import CommandProcessor, HELP_TEXT


def main():
    parser = argparse.ArgumentParser(description="MiniDB interactive shell.")
    parser.add_argument("--file", default="data.db")
    args = parser.parse_args()

    db = MiniDB(args.file, verbose=False)
    processor = CommandProcessor(db)

    print("MiniDB REPL.")
    print(HELP_TEXT)

    while True:
        try:
            raw = input("minidb> ").strip()
        except EOFError:
            print()
            break

        if not raw:
            continue
        if raw.upper() == "EXIT":
            print("Bye!")
            break

        print(processor.process(raw))


if __name__ == "__main__":
    main()
