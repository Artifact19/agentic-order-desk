import os
import threading

FAULT_MODES = {
    "transient": "Connection drops once; automatic retry recovers",
    "outage": "Service stays down; run halts and waits for a resume",
    "crash": "Server process is killed mid-step",
}


class InjectedFaultError(ConnectionError):
    pass


class FaultInjector:
    def __init__(self):
        self._armed = {}
        self._lock = threading.Lock()
        self.step_delay_seconds = 0.0

    def arm(self, step: str, mode: str) -> dict:
        if mode not in FAULT_MODES:
            raise ValueError(f"Unknown fault mode '{mode}'")
        with self._lock:
            self._armed[step] = mode
        return self.describe()

    def disarm(self, step=None):
        with self._lock:
            if step is None:
                self._armed.clear()
            else:
                self._armed.pop(step, None)

    def check(self, step: str):
        with self._lock:
            mode = self._armed.get(step)
            if mode == "transient":
                del self._armed[step]
        if mode == "transient":
            raise InjectedFaultError(f"Connection reset while executing '{step}'")
        if mode == "outage":
            raise InjectedFaultError(f"Service unavailable during '{step}'")
        if mode == "crash":
            with self._lock:
                self._armed.pop(step, None)
            os._exit(3)

    def describe(self) -> dict:
        with self._lock:
            return {"armed": [{"step": s, "mode": m} for s, m in self._armed.items()],
                    "modes": FAULT_MODES, "step_delay_seconds": self.step_delay_seconds}
