"""Shared command parsing/execution used by both the interactive REPL
(repl.py) and the network server (minidb/server.py), so both interfaces
support exactly the same commands and behave identically.
"""


def coerce_scalar(s):
    """Turn a raw CLI token into a proper Python type: 'true'/'false' ->
    bool, 'null'/'none' -> None, numeric strings -> int/float, else str."""
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() in ("null", "none"):
        return None
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


def parse_fields(rest):
    fields = {}
    errors = []
    for token in rest.split():
        if "=" not in token:
            errors.append(f"Skipping invalid field '{token}' (expected field=value)")
            continue
        field, value = token.split("=", 1)
        fields[field] = coerce_scalar(value)
    return fields, errors


def parse_single_condition(token):
    operators = [">=", "<=", "=", ">", "<"]
    for op in operators:
        if op in token:
            field, target = token.split(op, 1)
            return field.strip(), op, coerce_scalar(target.strip())
    return None


def parse_conditions(rest):
    """Parses 'age>18 AND city=NYC' into [(field, op, target), ...]."""
    conditions = []
    for part in rest.split(" AND "):
        cond = parse_single_condition(part.strip())
        if cond is None:
            return None
        conditions.append(cond)
    return conditions


HELP_TEXT = """Commands:
  SET <key> <field=value> [field=value ...] [--ttl=seconds]
  GET <key>
  DELETE <key>
  FIND <field><op><value> [AND <field><op><value> ...]   e.g. FIND age>18 AND city=NYC
  INDEX <field>                                          e.g. INDEX age
  LIST
  COMPACT
  BEGIN / COMMIT / ROLLBACK    (batch SET/DELETE atomically)
  HELP
  EXIT"""


class CommandProcessor:
    """Executes one command line against a MiniDB instance and returns a
    plain-text response. Doesn't print anything itself -- callers decide
    where output goes (stdout for the REPL, a socket for the server).

    Holds BEGIN/COMMIT/ROLLBACK state, so use one CommandProcessor per
    session/connection.
    """

    def __init__(self, db):
        self.db = db
        self._txn = None
        self._txn_cm = None

    def process(self, raw):
        raw = raw.strip()
        if not raw:
            return ""
        parts = raw.split(" ", 2)
        command = parts[0].upper()

        try:
            if command == "SET":
                return self._cmd_set(parts)
            elif command == "GET":
                return self._cmd_get(parts)
            elif command == "DELETE":
                return self._cmd_delete(parts)
            elif command == "FIND":
                return self._cmd_find(raw)
            elif command == "INDEX":
                return self._cmd_index(parts)
            elif command == "LIST":
                return str(self.db.keys())
            elif command == "COMPACT":
                self.db.compact()
                return "OK"
            elif command == "BEGIN":
                return self._cmd_begin()
            elif command == "COMMIT":
                return self._cmd_commit()
            elif command == "ROLLBACK":
                return self._cmd_rollback()
            elif command == "HELP":
                return HELP_TEXT
            else:
                return f"Unknown command: {command}"
        except Exception as e:
            return f"Error: {e}"

    def _write_target(self):
        """SET/DELETE go to the active transaction if one is open,
        otherwise straight to the db."""
        return self._txn if self._txn is not None else self.db

    def _cmd_set(self, parts):
        if len(parts) < 3:
            return "Usage: SET <key> <field=value> [field=value ...] [--ttl=seconds]"
        key, rest = parts[1], parts[2]

        ttl = None
        tokens = []
        for token in rest.split():
            if token.startswith("--ttl="):
                ttl = float(token.split("=", 1)[1])
            else:
                tokens.append(token)

        fields, errors = parse_fields(" ".join(tokens))
        out = list(errors)
        if fields:
            self._write_target().set(key, fields, ttl=ttl)
            out.append(f"Set {key} = {fields}" + (f" (ttl={ttl}s)" if ttl else ""))
        return "\n".join(out) if out else "Nothing to set"

    def _cmd_get(self, parts):
        if len(parts) < 2:
            return "Usage: GET <key>"
        result = self.db.get(parts[1])
        return str(result) if result is not None else "(nil)"

    def _cmd_delete(self, parts):
        if len(parts) < 2:
            return "Usage: DELETE <key>"
        self._write_target().delete(parts[1])
        return f"Deleted {parts[1]}"

    def _cmd_find(self, raw):
        condition_str = raw[len("FIND "):].strip() if len(raw) > 5 else ""
        if not condition_str:
            return "Usage: FIND <field><op><value> [AND <field><op><value> ...]"
        conditions = parse_conditions(condition_str)
        if conditions is None:
            return "Could not parse condition(s). Use =, >, <, >=, or <=, joined with AND"
        results = self.db.find_where(conditions)
        if not results:
            return "(no matches)"
        return "\n".join(f"{k}: {v}" for k, v in results.items())

    def _cmd_index(self, parts):
        if len(parts) < 2:
            return "Usage: INDEX <field>"
        self.db.create_index(parts[1])
        return f"Index created on '{parts[1]}'"

    def _cmd_begin(self):
        if self._txn is not None:
            return "Error: transaction already in progress"
        self._txn_cm = self.db.transaction()
        self._txn = self._txn_cm.__enter__()
        return "OK: transaction started"

    def _cmd_commit(self):
        if self._txn is None:
            return "Error: no transaction in progress"
        self._txn_cm.__exit__(None, None, None)
        self._txn = None
        self._txn_cm = None
        return "OK: transaction committed"

    def _cmd_rollback(self):
        if self._txn is None:
            return "Error: no transaction in progress"
        try:
            # Throw into the generator so it exits *before* its apply loop
            # ever runs -- no buffered ops are written to disk.
            self._txn_cm.__exit__(RuntimeError, RuntimeError("rollback"), None)
        except RuntimeError:
            pass
        self._txn = None
        self._txn_cm = None
        return "OK: transaction rolled back"
