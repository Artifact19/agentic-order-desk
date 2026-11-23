import threading
import time


class EventBus:
    def __init__(self, retain_runs: int = 200):
        self._events = {}
        self._finished = set()
        self._order = []
        self._condition = threading.Condition()
        self._retain = retain_runs

    def open(self, run_id: str):
        with self._condition:
            if run_id not in self._events:
                self._order.append(run_id)
            self._events[run_id] = []
            self._finished.discard(run_id)
            self._condition.notify_all()
            while len(self._order) > self._retain:
                stale = self._order.pop(0)
                self._events.pop(stale, None)
                self._finished.discard(stale)

    def publish(self, run_id: str, event_type: str, **data):
        with self._condition:
            stream = self._events.setdefault(run_id, [])
            stream.append({"seq": len(stream), "type": event_type, "at": time.time(), **data})
            if event_type == "run_finished":
                self._finished.add(run_id)
            self._condition.notify_all()

    def listen(self, run_id: str, since: int = 0, heartbeat_seconds: float = 10.0):
        cursor = since
        while True:
            with self._condition:
                stream = self._events.get(run_id)
                if stream is None:
                    return
                if cursor >= len(stream) and run_id not in self._finished:
                    self._condition.wait(timeout=heartbeat_seconds)
                batch = stream[cursor:]
                finished = run_id in self._finished
            if not batch:
                if finished:
                    return
                yield None
                continue
            for event in batch:
                yield event
            cursor += len(batch)
            if finished and cursor >= len(self._events.get(run_id, [])):
                return

    def has(self, run_id: str) -> bool:
        with self._condition:
            return run_id in self._events
