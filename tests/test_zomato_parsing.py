"""Offline tests for the live Zomato tools: parsing of restaurant cards and menu matching rules."""
from app.zomato_browser import NON_VEG_WORDS, NOT_A_MEAL, dish_matches, dish_slug, keywords_for, parse_card

CARD = {"href": "/riverton/slice-hub-market-street/order?contextual_menu_params=abc",
        "name": "Slice Hub",
        "text": "50% OFF\n\nSlice Hub\n4.2\n\nPizza, Pasta, Fast Food, Beverages\n\n₹250 for one\n\n35–40 min"}


def test_parse_card_reads_every_field():
    r = parse_card(CARD)
    assert r["restaurant_id"] == "zomato:slice-hub-market-street"
    assert r["rating"] == 4.2
    assert r["cost_for_one"] == 250
    assert (r["eta_low"], r["eta_high"]) == (35, 40)
    assert r["offer"] == "50% OFF"
    assert r["cuisines"].startswith("Pizza, Pasta")
    assert r["url"] == "https://www.zomato.com/riverton/slice-hub-market-street/order"


def test_parse_card_without_rating_or_offer():
    r = parse_card({"href": "/riverton/new-place/order", "name": "New Place",
                    "text": "New Place\nNEW\nBurger, Fast Food\n₹150 for one\n20–25 min"})
    assert r["rating"] is None and r["offer"] == ""
    assert r["eta_high"] == 25


def test_dish_slugs_and_keywords():
    assert dish_slug("pizza") == "pizza"
    assert dish_slug("misal") == "misal-pav"
    assert dish_slug("masala dosa") == "masala-dosa"
    assert "roll" in keywords_for("wrap")[0]
    assert keywords_for("veg momos") == [["veg"], ["momo"]]
    assert dish_matches("Mysore Masala Dosa", "masala dosa") and not dish_matches("Plain Dosa", "masala dosa")
    assert dish_matches("Chicken Shawarma Wrap", "shawarma roll")


def test_menu_filters():
    assert NON_VEG_WORDS.search("Chicken Tikka Pizza")
    assert not NON_VEG_WORDS.search("Paneer Makhani Pizza")
    assert NOT_A_MEAL.search("Pizza Base (6 inches)")
    assert not NOT_A_MEAL.search("Classic Margherita Pizza")


def test_parse_checkout_bill():
    from app.zomato_browser import parse_bill
    text = ("Item total\n₹80\nDelivery partner fee for 21.5 km\nincludes limitless delivery fee\n₹414\n"
            "Platform fee\n₹14.99 ₹5\nGST (govt. taxes)\n₹25.60\nTo pay\n₹525.60\n"
            "Delivery at Home\nMaple Residency, Green Park\nCHANGE\nDelivery in 1.5 hr\n")
    bill = parse_bill(text)
    assert bill["to_pay"] == "₹525.60" and bill["item_total"] == "₹80"
    assert bill["delivery_fee"] == "₹414" and bill["distance_km"] == 21.5
    assert bill["taxes"] == "₹25.60" and bill["delivery_time"] == "1.5 hr"
    assert bill["deliver_to"].startswith("Home: Maple")


def test_parse_bill_with_discounted_fee():
    from app.zomato_browser import parse_bill
    text = ("You saved ₹34 on delivery\nAuto-applied on your order\nItem total\n₹140\nDelivery partner fee\n"
            "Goes to them for their time and effort\n₹59 ₹25\nPlatform fee\n₹14.99 ₹5\nGST (govt. taxes)\n₹9.15\n"
            "To pay\n₹180.15\nDelivery in 25–30 mins\n")
    bill = parse_bill(text)
    assert bill["delivery_fee"] == "₹25" and bill["distance_km"] is None
    assert bill["savings"] == "₹34 on delivery" and bill["to_pay"] == "₹180.15"


def test_checkout_minutes():
    from app.service import _minutes
    assert _minutes("1.5 hr") == 90 and _minutes("35 min") == 35 and _minutes("1 hr 10 min") == 70
    assert _minutes(None) is None
