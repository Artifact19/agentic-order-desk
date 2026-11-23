import pytest

from app.understanding import extract_explicit_fields, reconcile_dish

CASES = [
    ("veg pizza under 300 within 40 minutes", "pizza", True, 300, 40),
    ("I'm craving paneer tikka, budget 350", "paneer tikka", None, 350, None),
    ("get me a cold coffee in 20 mins", "cold coffee", None, None, 20),
    ("something spicy like chicken biryani under 400", "chicken biryani", False, 400, None),
    ("order 2 masala dosa within 30 minutes", "masala dosa", None, None, 30),
    ("bhai hakka noodles mangwa do 200 tak", "hakka noodles", None, 200, None),
    ("a shawarma roll, non veg, max 220", "shawarma roll", False, 220, None),
    ("hot chocolate under ₹150 in half an hour", "hot chocolate", None, 150, 30),
    ("what about biryani?", "biryani", None, None, None),
    ("something cheaper", None, None, None, None),
]


@pytest.mark.parametrize("text, dish, veg, price, eta", CASES)
def test_rules_read_dish_and_constraints(text, dish, veg, price, eta):
    fields = extract_explicit_fields(text)
    assert (fields["item"], fields["is_veg"], fields["max_price"], fields["max_eta_minutes"]) == (dish, veg, price, eta)


def test_model_answer_is_widened_with_neighbouring_words():
    assert reconcile_dish("order 2 masala dosa within 30 minutes", "dosa", "masala dosa")[0] == "masala dosa"
    assert reconcile_dish("misal pav please, fast", "pav", "misal pav")[0] == "misal pav"


def test_model_answer_not_in_the_message_is_rejected():
    dish, method = reconcile_dish("get me a cold coffee in 20 mins", "burger", "cold coffee")
    assert dish == "cold coffee" and "rejected" in method


def test_model_can_correct_a_noisy_rule_reading():
    dish, method = reconcile_dish("I feel like having something Italian, maybe a margherita", "margherita", "italian")
    assert dish == "margherita" and method == "model"
