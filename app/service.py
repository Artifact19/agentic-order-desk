"""The ordering agent: one checkpointed pipeline per request, driving a real Zomato tab through browser tools.

    understand -> recall -> connect -> search -> menu:<restaurant> x N -> rank -> explain -> (you approve) -> checkout

Every step is saved in SQLite as it finishes, so a failure costs only the step that failed and Resume continues from
the last save. The agent never pays: approval opens Zomato's checkout page with the bill and stops there.
"""
import hashlib
import re
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import config
from .cache import CacheRegistry
from .checkpoints import CheckpointedRunner, Step
from .database import Database
from .events import EventBus
from .faults import FaultInjector
from .llm import load_llm
from .memory import LongTermMemoryStore, describe_order_memory, describe_rejection_memory, load_embedder
from .ranking import customer_highlights, explain_decision, rank_candidates
from .repositories import ObservationRepository, OrderRepository, RunRepository, SessionRepository
from .sessions import SessionContextStore, remember_exchange
from .understanding import (apply_session_context, classify_intent, complete_from_memory, normalise_message,
                            parse_message)
from .zomato_browser import ZomatoBrowser, ZomatoNotReady

RANKING_FIELDS = ("item_id", "item_name", "restaurant_id", "restaurant_name", "price", "list_price", "eta_minutes",
                  "eta_range", "rating", "offer_text", "url", "utility_score", "rating_pts", "speed_pts", "price_pts",
                  "memory_adjustment", "memory_reasons", "final_score")

HELP_TEXT = ("Tell me what you'd like and I'll find it on Zomato. Try \"veg pizza under 300 within 40 minutes\", then "
             "follow up with \"something cheaper\", \"make it faster\" or \"what about biryani?\". While a suggestion "
             "is open you can say \"yes\", \"next\", \"why\" or \"cancel\". Say \"refresh\" to read prices again.")

ASK_DISH_TEXT = ("Which dish would you like? For example \"paneer tikka under 300\", \"cold coffee in 20 minutes\" or "
                 "\"veg biryani\".")


def _minutes(text):
    """'35 min', '1.5 hr', '1 hr 10 min' -> minutes."""
    if not text:
        return None
    hours = re.search(r"([\d.]+)\s*(?:hr|hour)", text, re.I)
    mins = re.search(r"([\d.]+)\s*min", text, re.I)
    total = (float(hours.group(1)) * 60 if hours else 0) + (float(mins.group(1)) if mins else 0)
    return int(total) or None


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class AgentService:
    def __init__(self, database_path=config.DATABASE_PATH, memory_dir=config.MEMORY_DIR,
                 use_llm=config.USE_LLM, use_embeddings=config.USE_EMBEDDINGS, browser=None):
        self.caches = CacheRegistry(config.CACHE_TTL_SECONDS, config.CACHE_MAX_ENTRIES)
        self.db = Database(database_path)
        self.observations = ObservationRepository(self.db)
        self.session_repo = SessionRepository(self.db)
        self.runs = RunRepository(self.db)
        self.orders = OrderRepository(self.db)
        self.recovered_runs = self.runs.interrupt_orphaned_runs()
        self.contexts = SessionContextStore(self.session_repo, self.caches["session"])
        self.llm, self.llm_status = load_llm(config.LLM_MODEL_ID, use_llm)
        embedder, self.embedder_status = load_embedder(config.EMBEDDING_MODEL_ID, use_embeddings)
        self.memory = LongTermMemoryStore(memory_dir, embedder, self.caches["embedding"])
        self.bus = EventBus()
        self.faults = FaultInjector()
        self.runner = CheckpointedRunner(self.db, self.runs, self.bus, self.faults,
                                         config.RETRY_ATTEMPTS, config.RETRY_BASE_DELAY_SECONDS)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-run")
        self._session_locks = defaultdict(threading.RLock)
        self.browser = browser or ZomatoBrowser(config.CHROME_PROFILE_DIR, config.SCREENS_DIR, city=config.ZOMATO_CITY,
                                                max_restaurants=config.MAX_RESTAURANTS, port=config.CHROME_DEBUG_PORT)
        self.zomato_skip_login = False

    # ------------------------------------------------------------------ sessions and messages
    def create_session(self) -> dict:
        return self.session_view(self.contexts.create()["session_id"])

    def clear_session(self, session_id: str) -> dict:
        with self._session_locks[session_id]:
            evicted = self.contexts.close(session_id, "cleared by user")
        view = self.create_session()
        view["cleared"] = {"previous_session_id": session_id, "cache_entries_evicted": evicted}
        return view

    def session_view(self, session_id: str) -> dict:
        context = self._context(session_id)
        runs = self.runs.list_for_session(session_id)
        active = self.runs.get(context["active_run_id"]) if context["active_run_id"] else None
        return {"session_id": session_id, "context": self._public_context(context),
                "messages": self.session_repo.list_messages(session_id), "runs": runs,
                "active_run": self.run_view(active["run_id"]) if active else None}

    def handle_message(self, session_id: str, text: str, address=None) -> dict:
        text = (text or "").strip()
        if not text:
            raise ServiceError("Message is empty")
        if len(text) > config.MAX_MESSAGE_LENGTH:
            raise ServiceError(f"Message exceeds {config.MAX_MESSAGE_LENGTH} characters")
        with self._session_locks[session_id]:
            context = self._context(session_id)
            active = self.runs.get(context["active_run_id"]) if context["active_run_id"] else None
            if active and active["status"] == "running":
                raise ServiceError("The agent is still working on your previous message", 409)
            if address:
                context["address"] = address.strip()[:200]
            context["turn_no"] += 1
            self._add_message(context, "user", "text", text)
            intent = classify_intent(text)
            if intent == "order":
                return self._start_order(context, text)
            handler = {"approve": self._approve, "next": self._next_option, "cancel": self._cancel,
                       "explain": self._explain, "recap": self._recap}.get(intent, self._help)
            return {"type": "reply", "intent": intent, "messages": [handler(context)],
                    "context": self._public_context(context)}

    def act(self, session_id: str, action: str, idempotency_key=None) -> dict:
        with self._session_locks[session_id]:
            context = self._context(session_id)
            if action == "approve" and idempotency_key:
                existing = self.db.fetch_one("SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,))
                if existing:
                    return {"type": "reply", "intent": action, "duplicate": True, "order": existing,
                            "messages": [], "context": self._public_context(context)}
            context["turn_no"] += 1
            label = {"approve": "Add to Zomato cart", "next": "Show next option", "cancel": "Cancel"}.get(action)
            if label is None:
                raise ServiceError(f"Unknown action '{action}'")
            self._add_message(context, "user", "action", label)
            handler = {"approve": lambda c: self._approve(c, idempotency_key), "next": self._next_option,
                       "cancel": self._cancel}[action]
            return {"type": "reply", "intent": action, "messages": [handler(context)],
                    "context": self._public_context(context)}

    # ------------------------------------------------------------------ runs
    def resume_run(self, run_id: str) -> dict:
        run = self.runs.get(run_id)
        if run is None:
            raise ServiceError("Run not found", 404)
        if run["status"] not in ("interrupted", "failed"):
            raise ServiceError(f"Run is {run['status']} and cannot be resumed", 409)
        with self._session_locks[run["session_id"]]:
            context = self._context(run["session_id"])
            if context["active_run_id"] != run_id:
                raise ServiceError("A newer request in this session replaced this run", 409)
            self.runs.update(run_id, "running", current_step=run["current_step"])
        self.bus.open(run_id)
        self.executor.submit(self._execute_run, run_id, True)
        return {"run_id": run_id, "status": "running", "resumed": True}

    def run_view(self, run_id: str) -> dict:
        run = self.runs.get(run_id)
        if run is None:
            raise ServiceError("Run not found", 404)
        checkpoint = self.runs.latest_checkpoint(run_id)
        state = checkpoint["state"] if checkpoint else {}
        return {"run_id": run_id, "status": run["status"], "current_step": run["current_step"],
                "error": run["error"], "resume_count": run["resume_count"], "turn_no": run["turn_no"],
                "message": run["input"]["message"], "completed_steps": state.get("completed_steps", []),
                "step_metrics": state.get("step_metrics", {}),
                "planned_steps": [{"name": s.name, "label": s.label} for s in self._plan(state)] if state else [],
                "checkpoints": self.runs.list_checkpoints(run_id),
                "restaurants": state.get("restaurants", []), "tool_calls": state.get("tool_calls", []),
                "search": state.get("search"), "offers_count": len(state.get("offers", [])),
                "dish": (state.get("parsed") or {}).get("dish")}

    def fault_steps(self) -> list:
        """Steps that can be broken on purpose: the fixed ones plus the restaurants from the latest search."""
        steps = [("understand", "Understand message"), ("recall", "Recall long-term memory"),
                 ("connect", "Check Zomato sign-in"), ("search", "Search Zomato")]
        row = self.db.fetch_one("SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1")
        if row:
            checkpoint = self.runs.latest_checkpoint(row["run_id"])
            for profile in (checkpoint["state"].get("agent_profiles", []) if checkpoint else []):
                steps.append((f"menu:{profile['restaurant_id']}", f"Read menu at {profile['name']}"))
        steps += [("rank", "Rank dishes"), ("explain", "Explain decision")]
        return [{"step": s, "label": label} for s, label in steps]

    # ------------------------------------------------------------------ system views
    def system_view(self) -> dict:
        return {"llm": {"model": self.llm.model_id, "status": self.llm_status},
                "embeddings": {"model": self.memory.embedder.name, "status": self.embedder_status},
                "browser": {"port": config.CHROME_DEBUG_PORT, "attached": self.browser.attached,
                            "chrome": self.browser.channel_used, "city": getattr(self.browser, "city", "") or config.ZOMATO_CITY,
                            "last_status": self.browser.last_status},
                "caches": self.caches.snapshot(),
                "database": {"total_queries": self.db.total_queries, "handoffs": self.orders.count(),
                             "observations": self.observations.counts(),
                             "query_plans": self.observations.query_plans()},
                "memory": self.memory.counts(), "faults": self.faults.describe(),
                "recovered_runs_on_startup": self.recovered_runs}

    def observations_view(self) -> dict:
        return {"counts": self.observations.counts(), "dishes": self.observations.recent_dishes()}

    def flush_data_caches(self) -> dict:
        dropped = sum(self.caches[name].clear() for name in ("search", "menu", "parse", "embedding"))
        return {"entries_dropped": dropped, "caches": self.caches.snapshot()}

    def memory_view(self) -> dict:
        return {"counts": self.memory.counts(), "records": list(reversed(self.memory.records))}

    def search_memory(self, query: str) -> list:
        return self.memory.search(query, min_similarity=0.0)

    def reset_memory(self) -> dict:
        self.memory.clear()
        return self.memory.counts()

    def shutdown(self):
        try:
            self.executor.submit(self.browser.close).result(timeout=10)
        except Exception:
            pass
        self.executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ Zomato account
    def zomato_status(self, refresh: bool = False) -> dict:
        status = self.browser.account_status(refresh=refresh)
        return {**status, "ready": self._zomato_ready(status), "skipped": self.zomato_skip_login,
                "require_login": config.REQUIRE_ZOMATO_LOGIN, "require_address": config.REQUIRE_ZOMATO_ADDRESS}

    def zomato_connect(self) -> dict:
        self.zomato_skip_login = False
        status = self.browser.open_for_login()
        return {**status, "ready": self._zomato_ready(status)}

    def _zomato_ready(self, status: dict) -> bool:
        if self.zomato_skip_login:
            return bool(status.get("browser_running", True))
        return bool((status.get("logged_in") or not config.REQUIRE_ZOMATO_LOGIN)
                    and (status.get("address_set") or not config.REQUIRE_ZOMATO_ADDRESS))

    # ------------------------------------------------------------------ helpers
    def _context(self, session_id: str) -> dict:
        try:
            return self.contexts.get(session_id)
        except KeyError:
            raise ServiceError("Session not found or already cleared", 404)

    def _add_message(self, context: dict, role: str, kind: str, text: str, payload=None) -> dict:
        message_id = self.session_repo.add_message(context["session_id"], context["turn_no"], role, kind, text, payload)
        remember_exchange(context, role, text)
        return {"message_id": message_id, "turn_no": context["turn_no"], "role": role, "kind": kind,
                "text": text, "payload": payload}

    def _reply(self, context: dict, kind: str, text: str, payload=None) -> dict:
        message = self._add_message(context, "agent", kind, text, payload)
        self.contexts.save(context)
        return message

    def _public_context(self, context: dict) -> dict:
        pending = context["pending"]
        return {key: context[key] for key in ("session_id", "turn_no", "address", "constraints",
                                              "constraint_sources", "last_order_turn", "rejected_items",
                                              "orders", "turns", "window", "active_run_id")} | {
            "pending": {"run_id": pending["run_id"], "selection": pending["selection"],
                        "remaining": len(pending["ranking"])} if pending else None,
            "window_limit": config.CONTEXT_WINDOW_TURNS * 2}

    def _start_order(self, context: dict, text: str) -> dict:
        context["superseded_pending"] = context.get("pending")
        if context["pending"]:
            context["last_selection"] = context["pending"]["selection"]
            context["pending"] = None
        run_id = f"run-{uuid.uuid4().hex[:10]}"
        run_input = {"message": text, "turn_no": context["turn_no"], "address": context["address"],
                     "context": {"constraints": context["constraints"], "last_order_turn": context["last_order_turn"],
                                 "last_selection": context["last_selection"],
                                 "rejected_item_ids": [r["item_id"] for r in context["rejected_items"]]}}
        self.runs.create(run_id, context["session_id"], context["turn_no"], run_input)
        context["active_run_id"] = run_id
        self.contexts.save(context)
        self.bus.open(run_id)
        self.executor.submit(self._execute_run, run_id, False)
        return {"type": "run", "run_id": run_id, "context": self._public_context(context)}

    def _plan(self, state: dict) -> list:
        steps = [Step("understand", "Understand the request", self._step_understand),
                 Step("recall", "Recall long-term memory", self._step_recall)]
        constraints = state.get("constraints")
        if constraints is not None and not constraints.get("item"):
            return steps  # no dish yet: ask the customer instead of browsing
        steps += [Step("connect", "Check Zomato sign-in and address", self._step_connect),
                  Step("search", "Search Zomato and apply filters", self._step_search)]
        for profile in state.get("agent_profiles", []):
            steps.append(Step(f"menu:{profile['restaurant_id']}", f"Read menu at {profile['name']}",
                              lambda s, p=profile: self._step_menu(s, p)))
        steps += [Step("rank", "Rank dishes (MAUT + memory)", self._step_rank),
                  Step("explain", "Explain the choice", self._step_explain)]
        return steps

    def _execute_run(self, run_id: str, resume: bool):
        try:
            if resume:
                outcome = self.runner.resume(run_id, self._plan)
            else:
                run = self.runs.get(run_id)
                outcome = self.runner.start(run_id, {"run_id": run_id, "session_id": run["session_id"],
                                                     "input": run["input"]}, self._plan)
            if outcome["status"] == "completed":
                self._finalise(run_id, outcome["state"])
        except Exception as error:
            self.runs.update(run_id, "failed", error=f"{type(error).__name__}: {error}")
            self.bus.publish(run_id, "run_finished", status="failed", error=str(error))

    def _tool_call(self, state: dict, step: str, tool: str, detail: str, result=None, cached: bool = False):
        entry = {"step": step, "tool": tool, "detail": detail, "cached": cached,
                 "url": (result or {}).get("url"), "screenshot": None if cached else (result or {}).get("screenshot")}
        state.setdefault("tool_calls", []).append(entry)
        self.bus.publish(state["run_id"], "tool_call", **entry)

    # ------------------------------------------------------------------ pipeline steps
    def _step_understand(self, state: dict) -> dict:
        message = state["input"]["message"]
        key = ("parse", self.llm.model_id, normalise_message(message))
        parsed, hit = self.caches["parse"].get_or_load(key, lambda: parse_message(self.llm, message))
        state["_cache_events"].append({"cache": "parse", "key": normalise_message(message)[:60], "hit": hit})
        fields, sources, notes = apply_session_context(parsed, state["input"]["context"])
        state.update(parsed=parsed, fields=fields, sources=sources, refinement_notes=notes)
        return state

    def _step_recall(self, state: dict) -> dict:
        fields = state["fields"]
        query = state["input"]["message"]
        if fields.get("item"):
            diet = {True: "veg ", False: "non-veg "}.get(fields.get("is_veg"), "")
            query = f"{query} ({diet}{fields['item']})"
        recalled = self.memory.search(query)
        latest = self.memory.latest("order") if state["parsed"].get("repeat") else None
        if latest and not (fields.get("item") and fields["item"] != latest.get("constraints", {}).get("item")):
            recalled = [{**latest, "similarity": 1.0, "recall": "most recent order"}] + \
                       [m for m in recalled if m["memory_id"] != latest["memory_id"]]
        constraints, sources = complete_from_memory(fields, state["sources"], recalled, config.DEFAULT_REQUEST)
        state.update(recalled=recalled, constraints=constraints, sources=sources, offers=[], restaurants=[],
                     tool_calls=[], agent_profiles=[])
        return state

    def _step_connect(self, state: dict) -> dict:
        """Wait for the customer to sign in and choose an address in the agent's Chrome window before browsing."""
        status = self.browser.account_status(launch=True)
        if not self._zomato_ready(status):
            self.browser.open_for_login()
            deadline = time.time() + config.ZOMATO_WAIT_SECONDS
            last_note = None
            while not self._zomato_ready(status):
                missing = [label for label, ok in (
                    ("sign in", status.get("logged_in") or not config.REQUIRE_ZOMATO_LOGIN),
                    ("choose your delivery address", status.get("address_set") or not config.REQUIRE_ZOMATO_ADDRESS))
                    if not ok]
                note = ("Waiting for you to " + " and ".join(missing) + " in the Chrome window the agent opened"
                        if status.get("browser_running") else "The agent's Chrome window was closed; reopening it")
                if note != last_note:
                    self.bus.publish(state["run_id"], "waiting", step="connect", note=note,
                                     logged_in=bool(status.get("logged_in")), location=status.get("location", ""))
                    last_note = note
                if time.time() > deadline:
                    raise ZomatoNotReady(f"Still waiting to {' and '.join(missing)} on Zomato. "
                                         "Finish it in the Chrome window, then press Continue.")
                time.sleep(2.5)
                status = self.browser.account_status(launch=not status.get("browser_running"))
        state["zomato_account"] = {k: status.get(k) for k in ("logged_in", "user", "location", "address_set")} | {
            "skipped": self.zomato_skip_login}
        self.bus.publish(state["run_id"], "waiting", step="connect", note="", done=True)
        return state

    def _step_search(self, state: dict) -> dict:
        c = state["constraints"]
        refresh = "refresh" in state["parsed"]["modifiers"]
        location = (state.get("zomato_account") or {}).get("location") or ""
        key = ("search", c["item"], c["is_veg"] is True, location.lower())
        cached = None if refresh else self.caches["search"].get(key)
        state["_cache_events"].append({"cache": "search", "key": f"{c['item']} veg={c['is_veg'] is True}",
                                       "hit": cached is not None})
        if cached is None:
            result = self.browser.search(state["run_id"], c["item"], c["is_veg"] is True)
            result["read_at"] = time.time()
            self.caches["search"].set(key, result)
            self.observations.record_restaurants(result["restaurants"])
            self._tool_call(state, "search", "search", "; ".join(result["actions"]), result)
        else:
            result = cached
            self._tool_call(state, "search", "search", f"Reused the search read {int(time.time() - result['read_at'])} s "
                            f"ago ({len(result['restaurants'])} restaurants); say 'refresh' to read it again",
                            result, cached=True)
        shortlisted, restaurants = [], []
        for r in result["restaurants"]:
            reason = None
            if r["rating"] is None:
                reason = "Not rated yet on Zomato"
            elif c["max_eta_minutes"] and r["eta_high"] > c["max_eta_minutes"]:
                reason = f"Delivery {r['eta_low']}–{r['eta_high']} min is longer than {c['max_eta_minutes']} min"
            if reason:
                restaurants.append({"restaurant_id": r["restaurant_id"], "restaurant": r["name"], "outcome": "Skipped",
                                    "items_offered": 0, "detail": reason})
            else:
                shortlisted.append(r)
        shortlisted.sort(key=lambda r: (r["rating"], -r["eta_high"]), reverse=True)
        kept = shortlisted[:config.MAX_RESTAURANTS]
        for r in shortlisted[config.MAX_RESTAURANTS:]:
            restaurants.append({"restaurant_id": r["restaurant_id"], "restaurant": r["name"], "outcome": "Skipped",
                                "items_offered": 0,
                                "detail": f"Rated {r['rating']}; only the top {config.MAX_RESTAURANTS} by rating are opened"})
        state.update(agent_profiles=kept, restaurants=restaurants, search={
            "url": result["url"], "filters": result["filters"], "actions": result["actions"],
            "screenshot": result.get("screenshot"), "cards_read": len(result["restaurants"]),
            "shortlisted": len(kept), "reused": cached is not None})
        return state

    def _step_menu(self, state: dict, profile: dict) -> dict:
        c = state["constraints"]
        step = f"menu:{profile['restaurant_id']}"
        refresh = "refresh" in state["parsed"]["modifiers"]
        key = ("menu", profile["restaurant_id"], c["item"], c["is_veg"])
        cached = None if refresh else self.caches["menu"].get(key)
        state["_cache_events"].append({"cache": "menu", "key": f"{profile['name'][:30]}:{c['item']}",
                                       "hit": cached is not None})
        if cached is None:
            result = self.browser.read_menu(state["run_id"], profile, c["item"], c["is_veg"])
            self.caches["menu"].set(key, result)
            self.observations.record_dishes(profile["restaurant_id"], result["dishes"], c["item"])
        else:
            result = cached
        eta_range = f"{profile['eta_low']}–{profile['eta_high']} min"
        within = sorted((d for d in result["dishes"] if c["max_price"] is None or d["price"] <= c["max_price"]),
                        key=lambda d: d["price"])[:config.MAX_DISHES_PER_RESTAURANT]
        offers = [{"restaurant_id": profile["restaurant_id"], "restaurant_name": profile["name"],
                   "item_id": d["item_id"], "item_name": d["name"], "list_price": d["price"], "price": d["price"],
                   "eta_minutes": profile["eta_high"], "eta_range": eta_range, "rating": profile["rating"],
                   "offer_text": profile["offer"], "url": profile["url"]} for d in within]
        if result.get("closed"):
            outcome, detail = "Skipped", f"Closed now ({result['closed']})"
        elif not result.get("delivers", True):
            outcome, detail = "Skipped", "Does not deliver to your address"
        elif offers:
            outcome = "Dishes found"
            detail = f"{len(offers)} {c['item']} dish(es) within Rs {c['max_price']:g}" + (
                f"; offer: {profile['offer']}" if profile["offer"] else "")
        elif result["dishes"]:
            outcome = "Over budget"
            detail = (f"{len(result['dishes'])} {c['item']} dish(es), cheapest Rs "
                      f"{min(d['price'] for d in result['dishes'])}")
        else:
            outcome, detail = "No match", f"No {'veg ' if c['is_veg'] else ''}{c['item']} on the menu"
        state["offers"].extend(offers)
        state["restaurants"].append({"restaurant_id": profile["restaurant_id"], "restaurant": profile["name"],
                                     "outcome": outcome, "items_offered": len(offers), "detail": detail,
                                     "rating": profile["rating"], "eta": eta_range, "offer": profile["offer"]})
        self._tool_call(state, step, "read_menu",
                        f"{'Reused menu' if cached else 'Read menu'} at {profile['name']}: {result['dishes_on_page']} "
                        f"dishes, {len(result['dishes'])} match '{c['item']}'", result, cached=cached is not None)
        return state

    def _step_rank(self, state: dict) -> dict:
        excluded = set(state["input"]["context"]["rejected_item_ids"])
        c = state["constraints"]
        scoring = {**c, "max_price": c["max_price"] or max([o["price"] for o in state["offers"]] or [1]),
                   "max_eta_minutes": c["max_eta_minutes"] or 60}
        ranked = rank_candidates(state["offers"], excluded, state["recalled"], scoring)
        state["ranking"] = [{k: r.get(k) for k in RANKING_FIELDS} for r in ranked]
        return state

    def _step_explain(self, state: dict) -> dict:
        ranking = state["ranking"]
        state["explanation"] = explain_decision(self.llm, ranking[0], ranking) if ranking else None
        return state

    # ------------------------------------------------------------------ results
    def _finalise(self, run_id: str, state: dict):
        run = self.runs.get(run_id)
        with self._session_locks[run["session_id"]]:
            try:
                context = self.contexts.get(run["session_id"])
            except KeyError:
                self.runs.update(run_id, "completed")
                self.bus.publish(run_id, "run_finished", status="completed", message=None, context=None)
                return
            account = state.get("zomato_account") or {}
            if account.get("location"):
                context["address"] = account["location"]
            ranking = state.get("ranking") or []
            context["turns"].append({"turn_no": run["turn_no"], "message": run["input"]["message"],
                                     "constraints": state["constraints"], "notes": state["refinement_notes"],
                                     "matched": bool(ranking)})
            context["turns"] = context["turns"][-config.CONTEXT_WINDOW_TURNS:]
            summary = self._run_summary(state)
            restored, context["superseded_pending"] = context.get("superseded_pending"), None
            if not state["constraints"].get("item"):
                if restored:
                    context["pending"] = restored
                message = self._reply(context, "text", ASK_DISH_TEXT, {"run": summary})
            elif ranking:
                context.update(constraints=state["constraints"], constraint_sources=state["sources"],
                               last_order_turn=run["turn_no"])
                context["pending"] = {"run_id": run_id, "selection": ranking[0], "explanation": state["explanation"],
                                      "ranking": ranking, "offers": state["offers"], "recalled": state["recalled"],
                                      "constraints": state["constraints"], "query": run["input"]["message"],
                                      "rejected_here": []}
                message = self._reply(context, "recommendation", self._recommendation_text(ranking[0]),
                                      self._recommendation_payload(context["pending"], summary))
            else:
                c, notes = state["constraints"], state["refinement_notes"]
                diet = {True: "veg ", False: "non-veg "}.get(c["is_veg"], "")
                read = sum(1 for step in state["step_metrics"] if step.startswith("menu:"))
                excluded = len(state["input"]["context"]["rejected_item_ids"])
                reason = (f"I couldn't find {diet}{c['item']} under Rs {c['max_price']:g} delivered within "
                          f"{c['max_eta_minutes']} min on Zomato (read {read} menu(s))"
                          + (f" ({'; '.join(notes)})" if notes else "")
                          + (f", excluding {excluded} option(s) you declined" if excluded else "") + ".")
                if restored:
                    context["pending"] = restored
                    context["last_selection"] = restored["selection"]
                    reason += f" Your previous suggestion, {restored['selection']['item_name']}, is still open."
                else:
                    reason += " Try a higher budget, a longer delivery time or another dish."
                message = self._reply(context, "no_offer", reason, {"run": summary})
            self.runs.update(run_id, "completed")
        self.bus.publish(run_id, "run_finished", status="completed", message=message,
                         context=self._public_context(context))

    def _run_summary(self, state: dict) -> dict:
        metrics = state["step_metrics"]
        restaurants = state.get("restaurants", [])
        return {"constraints": state["constraints"], "sources": state["sources"], "notes": state["refinement_notes"],
                "dish": state["parsed"].get("dish"),
                "recalled": [{"memory_id": m["memory_id"], "memory_type": m["memory_type"],
                              "similarity": m["similarity"]} for m in state["recalled"]],
                "cards_read": (state.get("search") or {}).get("cards_read", 0),
                "menus_read": sum(1 for s in metrics if s.startswith("menu:")),
                "restaurants_with_dishes": sum(1 for r in restaurants if r["outcome"] == "Dishes found"),
                "offers_count": len(state.get("offers", [])),
                "tool_calls": len(state.get("tool_calls", [])),
                "browser_actions": sum(1 for t in state.get("tool_calls", []) if not t["cached"]),
                "db_queries": sum(m.get("db_queries", 0) for m in metrics.values()),
                "cache_hits": sum(1 for m in metrics.values() for c in m.get("cache", []) if c["hit"]),
                "cache_lookups": sum(len(m.get("cache", [])) for m in metrics.values()),
                "total_ms": round(sum(m.get("duration_ms", 0) for m in metrics.values()), 1),
                "search": state.get("search"), "parser_raw_output": state["parsed"]["raw_output"]}

    @staticmethod
    def _recommendation_text(selection: dict) -> str:
        offer = f" The restaurant shows '{selection['offer_text']}' on Zomato." if selection.get("offer_text") else ""
        return (f"I recommend {selection['item_name']} from {selection['restaurant_name']} at Rs {selection['price']}, "
                f"delivered in {selection.get('eta_range') or str(selection['eta_minutes']) + ' min'}.{offer}")

    @staticmethod
    def _recommendation_payload(pending: dict, summary=None) -> dict:
        return {"run_id": pending["run_id"], "selection": pending["selection"], "explanation": pending["explanation"],
                "highlights": customer_highlights(pending["selection"], pending["ranking"]),
                "ranking": pending["ranking"][:6], "remaining": len(pending["ranking"]),
                "option_no": len(pending["rejected_here"]) + 1, "run": summary}

    # ------------------------------------------------------------------ commands
    def _approve(self, context: dict, idempotency_key=None) -> dict:
        """Add the dish on Zomato, open checkout and read the bill. The customer presses Pay."""
        pending = context["pending"]
        if not pending:
            return self._reply(context, "text", "There is no open suggestion to confirm. Tell me what you'd like.")
        selection = pending["selection"]
        key = idempotency_key or f"{pending['run_id']}:{selection['item_id']}"
        order_id = "ZM-" + hashlib.sha1(key.encode()).hexdigest()[:8].upper()
        order, created = self.orders.place_once(key, {
            "order_id": order_id, "session_id": context["session_id"], "run_id": pending["run_id"],
            "item_id": selection["item_id"], "restaurant_id": selection["restaurant_id"],
            "price": selection["price"], "address": context["address"]})
        if not created:
            return self._reply(context, "text",
                               f"{selection['item_name']} was already handed to Zomato ({order['order_id']}).")
        try:
            result = self.executor.submit(self.browser.hand_off, pending["run_id"], selection["url"],
                                          selection["item_name"]).result(timeout=120)
        except Exception as error:
            result = {"url": selection["url"], "added_to_cart": False, "checkout_open": False, "bill": None,
                      "screenshot": "", "note": f"Could not drive the browser ({type(error).__name__}). "
                                                f"Open {selection['url']} yourself."}
        new_memories = self._write_memories(pending, approved=True)
        context["orders"].append({"order_id": order["order_id"], "item_name": selection["item_name"],
                                  "restaurant_name": selection["restaurant_name"], "price": selection["price"],
                                  "turn_no": context["turn_no"]})
        context["last_selection"] = selection
        context["pending"] = None
        bill = result.get("bill") or {}
        warnings = []
        checkout_minutes = _minutes(bill.get("delivery_time"))
        limit = pending["constraints"]["max_eta_minutes"]
        if checkout_minutes and limit and checkout_minutes > limit:
            warnings.append(f"Zomato's checkout says delivery in {bill['delivery_time']}, longer than your "
                            f"{limit} min limit")
        if bill.get("distance_km") and bill["distance_km"] > 10:
            warnings.append(f"The restaurant is {bill['distance_km']:g} km from your address "
                            f"(delivery fee {bill.get('delivery_fee')})")
        result["warnings"] = warnings
        tracking = [f"Opened {selection['restaurant_name']} on Zomato and added {selection['item_name']} "
                    f"(Rs {selection['price']}) to your cart" if result["added_to_cart"] else
                    f"Opened {selection['restaurant_name']} on Zomato in the agent's Chrome window",
                    result["note"], *[f"Check this: {w}" for w in warnings],
                    "The agent stops here: you place the order and pay on Zomato yourself"]
        if result.get("checkout_open") and bill.get("to_pay"):
            text = (f"Zomato checkout is open for {selection['item_name']} from {selection['restaurant_name']}: "
                    f"{bill['to_pay']} to pay (item {bill.get('item_total') or selection['price']}, delivery "
                    f"{bill.get('delivery_fee') or '?'}), delivery in {bill.get('delivery_time') or '?'}. "
                    + (" ".join(w + "." for w in warnings) + " " if warnings else "")
                    + "Press Pay in the Chrome window if you're happy; I never pay on your behalf.")
        else:
            text = (f"Handed to Zomato: {selection['item_name']} from {selection['restaurant_name']}. "
                    + ("It is in your cart. " if result["added_to_cart"] else "")
                    + "Finish checkout in the Chrome window; I never pay on your behalf.")
        return self._reply(context, "order", text, {"order": order, "selection": selection, "tracking": tracking,
                                                   "new_memories": new_memories, "idempotency_key": key,
                                                   "handoff": result})

    def _next_option(self, context: dict) -> dict:
        pending = context["pending"]
        if not pending:
            return self._reply(context, "text", "There is no open suggestion. Tell me what you'd like.")
        declined = pending["selection"]
        record = {k: declined[k] for k in ("item_id", "item_name", "restaurant_id", "restaurant_name", "price")}
        context["rejected_items"].append({**record, "turn_no": context["turn_no"]})
        pending["rejected_here"].append(record)
        excluded = {r["item_id"] for r in context["rejected_items"]}
        c = pending["constraints"]
        scoring = {**c, "max_price": c["max_price"] or max([o["price"] for o in pending["offers"]] or [1]),
                   "max_eta_minutes": c["max_eta_minutes"] or 60}
        remaining = rank_candidates(pending["offers"], excluded, pending["recalled"], scoring)
        if not remaining:
            new_memories = self._write_memories(pending, approved=False)
            context["pending"] = None
            return self._reply(context, "exhausted", "You have declined every dish that fits. Nothing was ordered.",
                               {"new_memories": new_memories})
        ranking = [{k: r.get(k) for k in RANKING_FIELDS} for r in remaining]
        pending.update(selection=ranking[0], ranking=ranking, explanation=explain_decision(self.llm, ranking[0], ranking))
        return self._reply(context, "recommendation",
                           f"Understood, skipping {declined['item_name']}. " + self._recommendation_text(ranking[0]),
                           self._recommendation_payload(pending))

    def _cancel(self, context: dict) -> dict:
        pending = context["pending"]
        if not pending:
            return self._reply(context, "text", "Nothing to cancel right now.")
        new_memories = self._write_memories(pending, approved=False)
        context["pending"] = None
        return self._reply(context, "cancelled", "Cancelled. Nothing was added to your Zomato cart.",
                           {"new_memories": new_memories})

    def _explain(self, context: dict) -> dict:
        pending = context["pending"]
        if not pending:
            return self._reply(context, "text", "There is no open suggestion to explain yet.")
        s = pending["selection"]
        return self._reply(context, "explain", pending["explanation"]["comparison"], {
            "justification": pending["explanation"]["justification"],
            "highlights": customer_highlights(s, pending["ranking"]), "item_name": s["item_name"],
            "breakdown": {k: s[k] for k in ("rating_pts", "speed_pts", "price_pts", "memory_adjustment",
                                            "final_score", "memory_reasons")}})

    def _recap(self, context: dict) -> dict:
        constraints = context["constraints"]
        parts = [f"This chat has {context['turn_no']} turn(s)."]
        if constraints:
            diet = {True: "veg ", False: "non-veg "}.get(constraints["is_veg"], "")
            parts.append(f"Current request: {diet}{constraints['item']}, up to Rs {constraints['max_price']:g}, "
                         f"within {constraints['max_eta_minutes']} min.")
        if context["rejected_items"]:
            parts.append("Declined: " + ", ".join(r["item_name"] for r in context["rejected_items"]) + ".")
        if context["orders"]:
            parts.append("Sent to Zomato: " + ", ".join(f"{o['item_name']} ({o['order_id']})"
                                                         for o in context["orders"]) + ".")
        if not constraints and not context["orders"]:
            parts.append("Nothing has been requested yet.")
        return self._reply(context, "recap", " ".join(parts), {"turns": context["turns"]})

    def _help(self, context: dict) -> dict:
        return self._reply(context, "text", HELP_TEXT)

    def _write_memories(self, pending: dict, approved: bool) -> list:
        written = []
        for item in pending["rejected_here"]:
            written.append(self.memory.add("rejection", describe_rejection_memory(item, pending["query"]), {
                "item_id": item["item_id"], "restaurant_id": item["restaurant_id"], "run_id": pending["run_id"]}))
        if approved:
            s = pending["selection"]
            written.append(self.memory.add("order", describe_order_memory(s, pending["query"]), {
                "item_id": s["item_id"], "restaurant_id": s["restaurant_id"], "run_id": pending["run_id"],
                "price_paid": s["price"],
                "constraints": {k: pending["constraints"][k] for k in ("item", "is_veg", "max_price",
                                                                       "max_eta_minutes")}}))
        return written
