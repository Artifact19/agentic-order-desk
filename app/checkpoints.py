import copy
import time
from dataclasses import dataclass
from typing import Callable

from .database import Database
from .events import EventBus
from .faults import FaultInjector
from .repositories import RunRepository

RETRYABLE_ERRORS = (ConnectionError, TimeoutError)


@dataclass
class Step:
    name: str
    label: str
    run: Callable[[dict], dict]


class RunHalted(Exception):
    def __init__(self, step: str, error: Exception, attempts: int):
        super().__init__(f"{step}: {error}")
        self.step = step
        self.error = error
        self.attempts = attempts


class CheckpointedRunner:
    def __init__(self, db: Database, runs: RunRepository, bus: EventBus, faults: FaultInjector,
                 attempts: int, base_delay: float):
        self.db = db
        self.runs = runs
        self.bus = bus
        self.faults = faults
        self.attempts = attempts
        self.base_delay = base_delay

    def start(self, run_id: str, state: dict, plan: Callable[[dict], list]) -> dict:
        state = {**state, "completed_steps": [], "step_metrics": {}}
        self.runs.save_checkpoint(run_id, 0, "start", state, 1, 0.0)
        self.bus.publish(run_id, "run_started", resumed=False)
        return self._execute(run_id, state, plan, seq=0)

    def resume(self, run_id: str, plan: Callable[[dict], list]) -> dict:
        checkpoint = self.runs.latest_checkpoint(run_id)
        if checkpoint is None:
            raise LookupError(f"No checkpoint stored for run {run_id}")
        state = checkpoint["state"]
        self.runs.mark_resumed(run_id)
        self.bus.publish(run_id, "run_started", resumed=True, from_step=checkpoint["step"],
                         resume_count=self.runs.get(run_id)["resume_count"])
        self.bus.publish(run_id, "plan", steps=[{"name": s.name, "label": s.label} for s in plan(state)])
        for name in state["completed_steps"]:
            metrics = state["step_metrics"].get(name, {})
            self.bus.publish(run_id, "step_restored", step=name, label=metrics.get("label", name),
                             original_ms=metrics.get("duration_ms"), db_queries=metrics.get("db_queries", 0),
                             cache=metrics.get("cache", []), sql=metrics.get("sql", []))
        return self._execute(run_id, state, plan, seq=checkpoint["seq"])

    def _execute(self, run_id: str, state: dict, plan: Callable[[dict], list], seq: int) -> dict:
        while True:
            remaining = [s for s in plan(state) if s.name not in state["completed_steps"]]
            if not remaining:
                break
            step = remaining[0]
            self.runs.update(run_id, "running", current_step=step.name)
            try:
                state, metrics = self._run_step(run_id, step, state)
            except RunHalted as halt:
                self.runs.update(run_id, "interrupted", current_step=step.name, error=str(halt.error))
                self.faults.disarm(step.name)
                self.bus.publish(run_id, "step_failed", step=step.name, label=step.label,
                                 error=str(halt.error), attempts=halt.attempts, resumable=True)
                self.bus.publish(run_id, "run_finished", status="interrupted", step=step.name,
                                 error=str(halt.error))
                return {"status": "interrupted", "step": step.name, "error": str(halt.error)}
            except Exception as error:
                self.runs.update(run_id, "failed", current_step=step.name, error=f"{type(error).__name__}: {error}")
                self.bus.publish(run_id, "step_failed", step=step.name, label=step.label,
                                 error=f"{type(error).__name__}: {error}", attempts=1, resumable=True)
                self.bus.publish(run_id, "run_finished", status="failed", step=step.name, error=str(error))
                return {"status": "failed", "step": step.name, "error": str(error)}
            seq += 1
            state["completed_steps"].append(step.name)
            state["step_metrics"][step.name] = metrics
            self.runs.save_checkpoint(run_id, seq, step.name, state, metrics["attempts"], metrics["duration_ms"])
            self.bus.publish(run_id, "step_completed", step=step.name, label=step.label, checkpoint_seq=seq,
                             **{k: v for k, v in metrics.items() if k != "label"})
            self.bus.publish(run_id, "plan", steps=[{"name": s.name, "label": s.label} for s in plan(state)])
        return {"status": "completed", "state": state}

    def _run_step(self, run_id: str, step: Step, state: dict) -> tuple:
        self.bus.publish(run_id, "step_started", step=step.name, label=step.label)
        for attempt in range(1, self.attempts + 1):
            started = time.perf_counter()
            try:
                if self.faults.step_delay_seconds:
                    time.sleep(self.faults.step_delay_seconds)
                self.faults.check(step.name)
                with self.db.metered() as meter:
                    working = copy.deepcopy(state)
                    working["_cache_events"] = []
                    result = step.run(working)
                cache_events = result.pop("_cache_events", [])
                return result, {"label": step.label, "attempts": attempt,
                                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                                "db_queries": meter.count, "sql": meter.statements, "cache": cache_events}
            except RETRYABLE_ERRORS as error:
                if attempt == self.attempts:
                    raise RunHalted(step.name, error, attempt) from error
                delay = self.base_delay * (2 ** (attempt - 1))
                self.bus.publish(run_id, "step_retry", step=step.name, label=step.label, attempt=attempt,
                                 error=str(error), retry_in_ms=int(delay * 1000))
                time.sleep(delay)
        raise RuntimeError("unreachable")
