import uuid

from .cache import TTLCache
from .config import CONTEXT_WINDOW_TURNS, DEFAULT_ADDRESS
from .repositories import SessionRepository


def new_context(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "turn_no": 0,
        "address": DEFAULT_ADDRESS,
        "constraints": None,
        "constraint_sources": {},
        "last_order_turn": None,
        "pending": None,
        "superseded_pending": None,
        "last_selection": None,
        "rejected_items": [],
        "orders": [],
        "turns": [],
        "window": [],
        "active_run_id": None,
    }


def remember_exchange(context: dict, role: str, text: str):
    context["window"].append({"turn_no": context["turn_no"], "role": role, "text": text})
    context["window"] = context["window"][-CONTEXT_WINDOW_TURNS * 2:]


class SessionContextStore:
    def __init__(self, repository: SessionRepository, cache: TTLCache):
        self.repository = repository
        self.cache = cache

    def create(self) -> dict:
        session_id = f"sess-{uuid.uuid4().hex[:10]}"
        self.repository.create(session_id)
        context = new_context(session_id)
        self.save(context)
        return context

    def get(self, session_id: str) -> dict:
        def load():
            session = self.repository.get(session_id)
            if session is None or session["status"] != "active":
                raise KeyError(session_id)
            return self.repository.load_context(session_id) or new_context(session_id)

        context, _ = self.cache.get_or_load(("session", session_id), load, tags=(session_id,))
        return context

    def save(self, context: dict):
        self.repository.save_context(context["session_id"], context)
        self.cache.set(("session", context["session_id"]), context, tags=(context["session_id"],))

    def close(self, session_id: str, reason: str) -> int:
        self.repository.close(session_id, reason)
        return self.cache.invalidate_tag(session_id)
