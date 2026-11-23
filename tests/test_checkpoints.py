from app import config
from conftest import wait_for_run

PIZZA = "Order a veg pizza under 450 within 30 minutes"
SLICE = "menu:zomato:corner-slice-old-town"
CHAI = "menu:zomato:tea-stop-hill-view"


def events_of(service, run_id, kind):
    return [e for e in service.bus._events[run_id] if e["type"] == kind]


def test_transient_fault_is_retried_automatically(service, order):
    session_id = service.create_session()["session_id"]
    service.faults.arm(SLICE, "transient")
    event, run_id = order(session_id, PIZZA)
    assert event["status"] == "completed"
    assert len(events_of(service, run_id, "step_retry")) == 1


def test_zomato_not_responding_is_retried(service, order, browser):
    session_id = service.create_session()["session_id"]
    browser.fail_next["read_menu"] = 1
    event, run_id = order(session_id, PIZZA)
    assert event["status"] == "completed"
    retries = events_of(service, run_id, "step_retry")
    assert len(retries) == 1 and "did not respond" in retries[0]["error"]


def test_outage_halts_and_resume_continues_from_checkpoint(service, order, browser):
    session_id = service.create_session()["session_id"]
    service.faults.arm(SLICE, "outage")
    event, run_id = order(session_id, PIZZA)
    assert event["status"] == "interrupted"
    halted = service.run_view(run_id)
    done_before = list(halted["completed_steps"])
    assert CHAI in done_before and SLICE not in done_before
    searches_before = browser.count("search")

    service.resume_run(run_id)
    finished = wait_for_run(service, run_id)
    assert finished["status"] == "completed"
    restored = [e["step"] for e in events_of(service, run_id, "step_restored")]
    executed = [e["step"] for e in events_of(service, run_id, "step_completed")]
    assert restored == done_before
    assert executed[0] == SLICE
    assert not set(executed) & set(done_before)
    assert browser.count("search") == searches_before  # the saved search is not repeated
    assert service.run_view(run_id)["resume_count"] == 1


def test_run_waits_for_zomato_sign_in_then_resumes(service, order, browser, monkeypatch):
    monkeypatch.setattr(config, "ZOMATO_WAIT_SECONDS", 0)
    browser.ready = False
    session_id = service.create_session()["session_id"]
    event, run_id = order(session_id, PIZZA)
    assert event["status"] == "failed" and event["step"] == "connect"
    assert ("open_for_login",) in browser.calls and browser.count("search") == 0
    assert any(e["note"].startswith("Waiting for you to sign in") for e in events_of(service, run_id, "waiting"))

    browser.ready = True
    service.resume_run(run_id)
    assert wait_for_run(service, run_id)["status"] == "completed"


def test_orphaned_runs_are_marked_interrupted_on_restart(service):
    session_id = service.create_session()["session_id"]
    service.runs.create("run-orphan", session_id, 1, {"message": "x"})
    assert service.runs.interrupt_orphaned_runs() == 1
    assert service.runs.get("run-orphan")["status"] == "interrupted"


def test_hand_off_happens_only_once(service, order, browser):
    session_id = service.create_session()["session_id"]
    order(session_id, PIZZA)
    first = service.act(session_id, "approve", "client-key-1")
    second = service.act(session_id, "approve", "client-key-1")
    assert first["messages"][0]["kind"] == "order"
    assert first["messages"][0]["payload"]["handoff"]["bill"]["to_pay"] == "₹180.15"
    assert second["duplicate"] is True
    assert service.orders.count() == 1 and browser.count("hand_off") == 1


def test_fault_menu_lists_the_real_steps(service, order):
    session_id = service.create_session()["session_id"]
    order(session_id, PIZZA)
    steps = [s["step"] for s in service.fault_steps()]
    assert steps[:4] == ["understand", "recall", "connect", "search"]
    assert SLICE in steps and steps[-2:] == ["rank", "explain"]
