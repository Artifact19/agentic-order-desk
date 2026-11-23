import pytest

from app.service import ASK_DISH_TEXT, ServiceError
from app.understanding import classify_intent


def test_follow_up_inherits_session_context(service, order):
    session_id = service.create_session()["session_id"]
    first, _ = order(session_id, "Order a veg pizza under 450 within 30 minutes")
    assert first["message"]["kind"] == "recommendation"
    second, _ = order(session_id, "what about biryani?")
    constraints, sources = second["context"]["constraints"], second["context"]["constraint_sources"]
    assert constraints["item"] == "biryani"
    assert constraints["max_price"] == 450 and constraints["is_veg"] is True
    assert sources["item"] == "this message"
    assert sources["max_price"].startswith("session context")
    assert sources["max_eta_minutes"].startswith("session context")
    assert second["message"]["payload"]["selection"]["item_name"].lower().startswith("veg biryani")


def test_failed_refinement_keeps_previous_context_and_suggestion(service, order):
    session_id = service.create_session()["session_id"]
    first, _ = order(session_id, "Order a veg pizza under 450 within 30 minutes")
    selection = first["message"]["payload"]["selection"]
    refined, _ = order(session_id, "make it faster")
    assert refined["message"]["kind"] == "no_offer"
    assert refined["context"]["constraints"]["max_eta_minutes"] == 30
    assert refined["context"]["pending"]["selection"]["item_id"] == selection["item_id"]


def test_clear_cache_starts_a_new_session_without_context(service, order):
    session_id = service.create_session()["session_id"]
    order(session_id, "Order a veg pizza under 450 within 30 minutes")
    view = service.clear_session(session_id)
    assert view["session_id"] != session_id
    assert view["context"]["constraints"] is None
    assert view["context"]["window"] == []
    assert view["cleared"]["cache_entries_evicted"] == 1
    with pytest.raises(ServiceError):
        service.handle_message(session_id, "something cheaper")


def test_rejections_carry_across_turns_in_the_same_session(service, order):
    session_id = service.create_session()["session_id"]
    first, _ = order(session_id, "Order a veg pizza under 450 within 30 minutes")
    declined = first["message"]["payload"]["selection"]["item_id"]
    service.act(session_id, "next")
    again, _ = order(session_id, "order a veg pizza again")
    assert all(r["item_id"] != declined for r in again["message"]["payload"]["ranking"])


def test_new_session_repeat_order_uses_long_term_memory(service, order):
    first_session = service.create_session()["session_id"]
    order(first_session, "Order a veg biryani under 400 within 30 minutes")
    service.act(first_session, "approve")
    fresh = service.clear_session(first_session)["session_id"]
    event, _ = order(fresh, "Order the same as last time")
    constraints, sources = event["context"]["constraints"], event["context"]["constraint_sources"]
    assert constraints["item"] == "biryani"
    assert constraints["max_price"] == 400
    assert sources["item"].startswith("long-term memory")


def test_agent_asks_for_the_dish_instead_of_guessing(service, order, browser):
    session_id = service.create_session()["session_id"]
    event, run_id = order(session_id, "I'm hungry")
    assert event["message"]["kind"] == "text" and event["message"]["text"] == ASK_DISH_TEXT
    assert browser.count("search") == 0
    assert "search" not in service.run_view(run_id)["completed_steps"]


def test_zomato_address_becomes_the_session_address(service, order):
    session_id = service.create_session()["session_id"]
    event, _ = order(session_id, "veg pizza under 300 within 30 minutes")
    assert event["context"]["address"] == "Home - Green Park"


def test_command_intents():
    assert classify_intent("yes, place the order") == "approve"
    assert classify_intent("next option") == "next"
    assert classify_intent("why this one?") == "explain"
    assert classify_intent("what did I ask for?") == "recap"
    assert classify_intent("something cheaper") == "order"
    assert classify_intent("refresh") == "order"
    assert classify_intent("hello there") == "unknown"
