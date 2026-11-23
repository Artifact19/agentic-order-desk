import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS zomato_restaurants (
    restaurant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    rating REAL,
    eta_low INTEGER,
    eta_high INTEGER,
    cost_for_one INTEGER,
    offer TEXT,
    cuisines TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS zomato_dishes (
    item_id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL REFERENCES zomato_restaurants(restaurant_id),
    name TEXT NOT NULL,
    price INTEGER NOT NULL,
    query TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dishes_restaurant ON zomato_dishes(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_dishes_query ON zomato_dishes(query, price);
CREATE TABLE IF NOT EXISTS dish_prices (
    item_id TEXT NOT NULL REFERENCES zomato_dishes(item_id),
    price INTEGER NOT NULL,
    seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices_item ON dish_prices(item_id, seen_at);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    status TEXT NOT NULL,
    closed_reason TEXT
);
CREATE TABLE IF NOT EXISTS session_context (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    context TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    turn_no INTEGER NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, message_id);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    turn_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    error TEXT,
    resume_count INTEGER NOT NULL DEFAULT 0,
    input TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS checkpoints (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    seq INTEGER NOT NULL,
    step TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    restaurant_id TEXT NOT NULL,
    price INTEGER NOT NULL,
    address TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_WHITESPACE = re.compile(r"\s+")


class QueryMeter:
    def __init__(self):
        self.count = 0
        self.total_ms = 0.0
        self.statements = []

    def record(self, sql: str, duration_ms: float, rows: int):
        self.count += 1
        self.total_ms += duration_ms
        self.statements.append({"sql": _WHITESPACE.sub(" ", sql).strip()[:240],
                                "duration_ms": round(duration_ms, 3), "rows": rows})

    def as_dict(self) -> dict:
        return {"queries": self.count, "total_ms": round(self.total_ms, 3), "statements": self.statements}


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._local = threading.local()
        self.total_queries = 0
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    @contextmanager
    def metered(self):
        previous = getattr(self._local, "meter", None)
        meter = QueryMeter()
        self._local.meter = meter
        try:
            yield meter
        finally:
            self._local.meter = previous

    def _record(self, sql: str, started: float, rows: int):
        self.total_queries += 1
        meter = getattr(self._local, "meter", None)
        if meter is not None:
            meter.record(sql, (time.perf_counter() - started) * 1000, rows)

    def fetch_all(self, sql: str, params=()) -> list:
        started = time.perf_counter()
        with self._lock:
            rows = [dict(row) for row in self._conn.execute(sql, params).fetchall()]
        self._record(sql, started, len(rows))
        return rows

    def fetch_one(self, sql: str, params=()):
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params=()) -> int:
        started = time.perf_counter()
        with self._lock:
            cursor = self._conn.execute(sql, params)
        self._record(sql, started, cursor.rowcount)
        return cursor.rowcount

    def execute_many(self, sql: str, rows: list) -> None:
        started = time.perf_counter()
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(sql, rows)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        self._record(sql, started, len(rows))

    @contextmanager
    def transaction(self):
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def explain(self, sql: str, params=()) -> list:
        with self._lock:
            return [row["detail"] for row in self._conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()]

    def close(self):
        with self._lock:
            self._conn.close()
