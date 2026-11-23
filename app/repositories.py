import json
from datetime import datetime

from .database import Database


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ObservationRepository:
    """Everything the agent has read on Zomato, with when it was seen. Prices keep a history."""

    def __init__(self, db: Database):
        self.db = db

    def record_restaurants(self, restaurants: list):
        stamp = now_iso()
        with self.db.transaction():
            for r in restaurants:
                self.db.execute(
                    "INSERT INTO zomato_restaurants (restaurant_id, name, url, rating, eta_low, eta_high, cost_for_one, "
                    "offer, cuisines, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(restaurant_id) DO UPDATE SET name = excluded.name, rating = excluded.rating, "
                    "eta_low = excluded.eta_low, eta_high = excluded.eta_high, cost_for_one = excluded.cost_for_one, "
                    "offer = excluded.offer, cuisines = excluded.cuisines, last_seen = excluded.last_seen, "
                    "times_seen = times_seen + 1",
                    (r["restaurant_id"], r["name"], r["url"], r["rating"], r["eta_low"], r["eta_high"],
                     r["cost_for_one"], r["offer"], r["cuisines"], stamp, stamp))

    def record_dishes(self, restaurant_id: str, dishes: list, query: str):
        stamp = now_iso()
        with self.db.transaction():
            for d in dishes:
                previous = self.db.fetch_one("SELECT price FROM zomato_dishes WHERE item_id = ?", (d["item_id"],))
                self.db.execute(
                    "INSERT INTO zomato_dishes (item_id, restaurant_id, name, price, query, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(item_id) DO UPDATE SET price = excluded.price, "
                    "last_seen = excluded.last_seen",
                    (d["item_id"], restaurant_id, d["name"], d["price"], query, stamp, stamp))
                if previous is None or previous["price"] != d["price"]:
                    self.db.execute("INSERT INTO dish_prices VALUES (?, ?, ?)", (d["item_id"], d["price"], stamp))

    def counts(self) -> dict:
        row = self.db.fetch_one(
            "SELECT (SELECT COUNT(*) FROM zomato_restaurants) AS restaurants, (SELECT COUNT(*) FROM zomato_dishes) AS "
            "dishes, (SELECT COUNT(*) FROM dish_prices) AS price_points, (SELECT COUNT(DISTINCT query) FROM "
            "zomato_dishes) AS dishes_searched")
        return row

    def recent_dishes(self, limit: int = 40) -> list:
        return self.db.fetch_all(
            "SELECT d.item_id, d.name, d.price, d.query, d.last_seen, r.name AS restaurant_name, r.rating, "
            "r.eta_low, r.eta_high, (SELECT COUNT(*) FROM dish_prices p WHERE p.item_id = d.item_id) AS price_changes "
            "FROM zomato_dishes d JOIN zomato_restaurants r ON r.restaurant_id = d.restaurant_id "
            "ORDER BY d.last_seen DESC, d.price LIMIT ?", (limit,))

    def query_plans(self) -> dict:
        return {"dishes_by_query": self.db.explain(
            "SELECT * FROM zomato_dishes WHERE query = ? AND price <= ? ORDER BY price", ("pizza", 300)),
            "price_history": self.db.explain("SELECT * FROM dish_prices WHERE item_id = ? ORDER BY seen_at", ("x",))}


class SessionRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, session_id: str) -> dict:
        stamp = now_iso()
        self.db.execute("INSERT INTO sessions VALUES (?, ?, ?, 'active', NULL)", (session_id, stamp, stamp))
        return self.get(session_id)

    def get(self, session_id: str):
        return self.db.fetch_one("SELECT * FROM sessions WHERE session_id = ?", (session_id,))

    def close(self, session_id: str, reason: str):
        self.db.execute("UPDATE sessions SET status = 'closed', closed_reason = ?, last_active_at = ? "
                        "WHERE session_id = ?", (reason, now_iso(), session_id))

    def save_context(self, session_id: str, context: dict):
        stamp = now_iso()
        with self.db.transaction():
            self.db.execute("INSERT INTO session_context VALUES (?, ?, ?) ON CONFLICT(session_id) "
                            "DO UPDATE SET context = excluded.context, updated_at = excluded.updated_at",
                            (session_id, json.dumps(context), stamp))
            self.db.execute("UPDATE sessions SET last_active_at = ? WHERE session_id = ?", (stamp, session_id))

    def load_context(self, session_id: str):
        row = self.db.fetch_one("SELECT context FROM session_context WHERE session_id = ?", (session_id,))
        return json.loads(row["context"]) if row else None

    def add_message(self, session_id: str, turn_no: int, role: str, kind: str, text: str, payload=None) -> int:
        self.db.execute("INSERT INTO messages (session_id, turn_no, role, kind, text, payload, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (session_id, turn_no, role, kind, text, json.dumps(payload) if payload else None, now_iso()))
        return self.db.fetch_one("SELECT last_insert_rowid() AS id")["id"]

    def list_messages(self, session_id: str) -> list:
        rows = self.db.fetch_all("SELECT * FROM messages WHERE session_id = ? ORDER BY message_id", (session_id,))
        return [{**row, "payload": json.loads(row["payload"]) if row["payload"] else None} for row in rows]

    def list_sessions(self, limit: int = 20) -> list:
        return self.db.fetch_all(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.session_id AND m.role = 'user') "
            "AS user_turns FROM sessions s ORDER BY s.created_at DESC LIMIT ?", (limit,))


class RunRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, run_id: str, session_id: str, turn_no: int, run_input: dict):
        stamp = now_iso()
        self.db.execute("INSERT INTO runs (run_id, session_id, turn_no, status, input, created_at, updated_at) "
                        "VALUES (?, ?, ?, 'running', ?, ?, ?)",
                        (run_id, session_id, turn_no, json.dumps(run_input), stamp, stamp))

    def update(self, run_id: str, status: str, current_step=None, error=None):
        self.db.execute("UPDATE runs SET status = ?, current_step = ?, error = ?, updated_at = ? WHERE run_id = ?",
                        (status, current_step, error, now_iso(), run_id))

    def mark_resumed(self, run_id: str):
        self.db.execute("UPDATE runs SET status = 'running', error = NULL, resume_count = resume_count + 1, "
                        "updated_at = ? WHERE run_id = ?", (now_iso(), run_id))

    def get(self, run_id: str):
        row = self.db.fetch_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return {**row, "input": json.loads(row["input"])} if row else None

    def list_for_session(self, session_id: str) -> list:
        return self.db.fetch_all("SELECT run_id, turn_no, status, current_step, error, resume_count, created_at, "
                                 "updated_at FROM runs WHERE session_id = ? ORDER BY created_at", (session_id,))

    def save_checkpoint(self, run_id: str, seq: int, step: str, state: dict, attempts: int, duration_ms: float):
        self.db.execute("INSERT OR REPLACE INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (run_id, seq, step, json.dumps(state), attempts, round(duration_ms, 2), now_iso()))

    def latest_checkpoint(self, run_id: str):
        row = self.db.fetch_one("SELECT * FROM checkpoints WHERE run_id = ? ORDER BY seq DESC LIMIT 1", (run_id,))
        return {**row, "state": json.loads(row["state"])} if row else None

    def list_checkpoints(self, run_id: str) -> list:
        return self.db.fetch_all("SELECT seq, step, attempts, duration_ms, created_at FROM checkpoints "
                                 "WHERE run_id = ? ORDER BY seq", (run_id,))

    def interrupt_orphaned_runs(self) -> int:
        return self.db.execute("UPDATE runs SET status = 'interrupted', error = 'Process stopped while the run was "
                               "in progress', updated_at = ? WHERE status = 'running'", (now_iso(),))


class OrderRepository:
    def __init__(self, db: Database):
        self.db = db

    def place_once(self, idempotency_key: str, order: dict) -> tuple:
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order["order_id"], idempotency_key, order["session_id"], order["run_id"], order["item_id"],
             order["restaurant_id"], order["price"], order["address"], now_iso()))
        stored = self.db.fetch_one("SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,))
        return stored, inserted == 1

    def count(self) -> int:
        return self.db.fetch_one("SELECT COUNT(*) AS n FROM orders")["n"]
