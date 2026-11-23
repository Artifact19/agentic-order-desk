import re

from .memory import compute_memory_adjustment

EXPLAINER_SYSTEM_PROMPT = (
    "You are a food-order evaluation agent. Rewrite the verified comparison as two fluent sentences "
    "for the customer. Keep every fact exactly as given and do not add new numbers or claims. "
    "No lists, no markdown."
)

CONTRADICTION_TERMS = ["more expensive", "costlier", "pricier", "slower", "longer delivery",
                       "lower rating", "less satisfactory", "worse", "higher price", "prepar", "serving", "calorie"]

MONEY_PATTERN = re.compile(r"(?:rs\.?|₹)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:rupees|rs\b)", re.I)
MINUTES_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:min|mins|minutes)\b", re.I)


def _unit_values(pattern, text: str) -> set:
    return {float(next(g for g in match.groups() if g)) for match in pattern.finditer(text)}


def maut_score(item: dict, max_price: float, max_eta: int) -> dict:
    rating_pts = round((item["rating"] / 5.0) * 40.0, 2)
    speed_pts = round(((max_eta - item["eta_minutes"]) / max_eta) * 35.0, 2)
    price_pts = round(((max_price - item["price"]) / max_price) * 25.0, 2)
    return {"rating_pts": rating_pts, "speed_pts": speed_pts, "price_pts": price_pts,
            "total": round(rating_pts + speed_pts + price_pts, 2)}


def rank_candidates(candidates: list, excluded_ids: set, recalled: list, constraints: dict) -> list:
    ranked = []
    for candidate in candidates:
        if candidate["item_id"] in excluded_ids:
            continue
        maut = maut_score(candidate, constraints["max_price"], constraints["max_eta_minutes"])
        adjustment, reasons = compute_memory_adjustment(candidate, recalled)
        ranked.append({**candidate, "utility_score": maut["total"], "rating_pts": maut["rating_pts"],
                       "speed_pts": maut["speed_pts"], "price_pts": maut["price_pts"],
                       "memory_adjustment": adjustment, "memory_reasons": reasons,
                       "final_score": round(maut["total"] + adjustment, 2)})
    ranked.sort(key=lambda c: (c["final_score"], c["utility_score"]), reverse=True)
    return ranked


def template_comparison(best: dict, alternatives: list) -> str:
    if not alternatives:
        return f"{best['item_name']} is the only remaining option that satisfies every constraint."
    sentences = []
    for alt in alternatives:
        points = []
        if best["price"] < alt["price"]:
            points.append(f"Rs {alt['price'] - best['price']} cheaper")
        if best["eta_minutes"] < alt["eta_minutes"]:
            points.append(f"{alt['eta_minutes'] - best['eta_minutes']} min faster")
        if best["rating"] > alt["rating"]:
            points.append(f"rated higher ({best['rating']} vs {alt['rating']})")
        if best["memory_adjustment"] > alt["memory_adjustment"]:
            points.append("better supported by past orders")
        trade_off = (f", while {alt['item_name']} has the higher rating ({alt['rating']} vs {best['rating']})"
                     if alt["rating"] > best["rating"] else "")
        advantage = ", ".join(points) if points else "a higher combined score"
        sentences.append(f"Against {alt['item_name']} ({alt['final_score']}), it is {advantage}{trade_off}.")
    return f"{best['item_name']} scores {best['final_score']}. " + " ".join(sentences)


def is_grounded(generated: str, verified: str) -> bool:
    allowed = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", verified)}
    used = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", generated)}
    reversed_claim = any(t in generated.lower() and t not in verified.lower() for t in CONTRADICTION_TERMS)
    units_match = all(_unit_values(p, generated) <= _unit_values(p, verified) for p in (MONEY_PATTERN, MINUTES_PATTERN))
    return 40 <= len(generated) <= 700 and used <= allowed and units_match and not reversed_claim


def customer_highlights(selection: dict, ranking: list) -> list:
    others = [r for r in ranking if r["item_id"] != selection["item_id"]]
    points = ["Read live from Zomato just now: menu price, rating and delivery time"]
    if selection.get("offer_text"):
        points.append(f"Restaurant offer on Zomato: {selection['offer_text']} (terms shown at checkout)")
    if not others:
        points.append("The only option that fits everything you asked for")
    else:
        if all(selection["eta_minutes"] <= o["eta_minutes"] for o in others):
            points.append("Fastest delivery of your options")
        if all(selection["price"] <= o["price"] for o in others):
            points.append("Lowest price of your options")
        if all(selection["rating"] >= o["rating"] for o in others):
            points.append("Highest-rated restaurant of your options")
    for reason in selection["memory_reasons"]:
        if reason.startswith("ordered this item"):
            points.append("You've ordered this before")
        elif reason.startswith("ordered from this restaurant"):
            points.append("You've ordered from this restaurant before")
    if others and len(points) < 3:
        points.append("Best overall balance of price, speed and rating")
    return list(dict.fromkeys(points))


def explain_decision(llm, best: dict, ranked: list) -> dict:
    verified = template_comparison(best, ranked[1:3])
    generated = llm.generate(EXPLAINER_SYSTEM_PROMPT, f"Verified comparison: {verified}", max_new_tokens=120)
    grounded = bool(generated) and is_grounded(generated, verified)
    memory_part = (f" {best['memory_adjustment']:+g} memory ({', '.join(best['memory_reasons'])})"
                   if best["memory_reasons"] else " +0 memory")
    return {
        "comparison": generated if grounded else verified,
        "justification": (f"{best['item_name']} from {best['restaurant_name']} has the highest final score "
                          f"({best['final_score']}/100 = {best['utility_score']} MAUT{memory_part}) among "
                          f"{len(ranked)} eligible dishes read on Zomato, at Rs {best['price']} with "
                          f"{best.get('eta_range') or str(best['eta_minutes']) + ' min'} delivery."),
        "llm_rewrite_used": grounded,
    }
