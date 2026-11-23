PIZZA = "Order a veg pizza under 450 within 30 minutes"


def run_summary(event):
    return event["message"]["payload"]["run"]


def test_follow_up_reuses_search_and_menus(service, order, browser):
    session_id = service.create_session()["session_id"]
    first, _ = order(session_id, PIZZA)
    searches, menus = browser.count("search"), browser.count("read_menu")
    cheaper, _ = order(session_id, "something cheaper")
    assert browser.count("search") == searches and browser.count("read_menu") == menus
    summary = run_summary(cheaper)
    assert summary["browser_actions"] == 0 and summary["search"]["reused"] is True
    assert cheaper["message"]["payload"]["selection"]["price"] < first["message"]["payload"]["selection"]["price"]


def test_refresh_reads_zomato_again_and_records_price_changes(service, order, browser):
    session_id = service.create_session()["session_id"]
    order(session_id, PIZZA)
    before = service.observations.counts()
    browser.prices[("sunrise-snacks-river-road", "Cheese pizza")] = 150
    searches = browser.count("search")
    order(session_id, "refresh")
    assert browser.count("search") == searches + 1
    after = service.observations.counts()
    assert after["price_points"] == before["price_points"] + 1
    assert after["dishes"] == before["dishes"]


def test_everything_read_is_recorded(service, order):
    session_id = service.create_session()["session_id"]
    order(session_id, PIZZA)
    counts = service.observations.counts()
    assert counts["restaurants"] == 7 and counts["dishes"] >= 8 and counts["dishes_searched"] == 1
    dishes = service.observations_view()["dishes"]
    assert all(d["query"] == "pizza" for d in dishes)
    assert not any("base" in d["name"].lower() or "chicken" in d["name"].lower() for d in dishes)


def test_restaurants_that_do_not_deliver_are_skipped(service, order, browser):
    browser.not_delivering.add("the-bean-house-green-park")
    session_id = service.create_session()["session_id"]
    event, run_id = order(session_id, PIZZA)
    brew = next(r for r in service.run_view(run_id)["restaurants"] if r["restaurant"] == "The Bean House")
    assert brew["detail"] == "Does not deliver to your address"
    assert all(r["restaurant_name"] != "The Bean House" for r in event["message"]["payload"]["ranking"])


def test_queries_use_indexes(service):
    plans = service.observations.query_plans()
    assert any("idx_dishes_query" in row for row in plans["dishes_by_query"])
    assert any("idx_prices_item" in row for row in plans["price_history"])


def test_closed_restaurants_are_skipped(service, order, browser):
    browser.closed["corner-slice-old-town"] = "Opens at 7pm"
    session_id = service.create_session()["session_id"]
    event, run_id = order(session_id, PIZZA)
    slice_card = next(r for r in service.run_view(run_id)["restaurants"] if r["restaurant"] == "Corner Slice")
    assert slice_card["detail"] == "Closed now (Opens at 7pm)"
    assert all(r["restaurant_name"] != "Corner Slice" for r in event["message"]["payload"]["ranking"])
