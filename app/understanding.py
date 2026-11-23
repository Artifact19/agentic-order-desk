"""Understanding the customer's message: dish, diet, budget, delivery time, follow-ups and commands.

There is no fixed list of dishes. The dish is found by combining two readers and checking both against the text:

* rules remove everything that is not the dish (budget, time, diet words, quantities and filler words) and keep
  the longest remaining phrase, e.g. "get me 2 masala dosa under 200" -> "masala dosa";
* the local language model proposes the dish as well. Its answer is accepted only if every word appears in the
  message (so it cannot invent a dish), and it is widened with the neighbouring words that the rules also kept,
  because small models tend to shorten names ("dosa" -> "masala dosa").

Zomato has the final word: a dish that returns no restaurants is reported back instead of guessed.
"""
import re

from .llm import extract_json

VEG_PATTERN = re.compile(r"\b(veg|vegetarian|veggie|pure veg)\b", re.I)
NON_VEG_PATTERN = re.compile(r"\b(non[- ]?veg|non[- ]?vegetarian|chicken|mutton|meat|egg|eggs|fish|prawns?|"
                             r"keema|lamb|pepperoni)\b", re.I)
DIET_WORDS = re.compile(r"\b(pure\s+veg|non[- ]?veg(?:etarian)?|veg(?:etarian|gie)?)\b", re.I)
CURRENCY = r"(?:rs\.?|inr|₹|rupees)?"
PRICE_PATTERN = re.compile(
    r"(?:under|below|less than|within|max(?:imum)?|budget(?:\s+of)?|upto|up to|at most|not more than)\s*"
    + CURRENCY + r"\s*(\d{2,5})(?!\s*(?:min|mins|minutes|hr|hrs|hour|hours))"
    r"|(?:rs\.?|inr|₹)\s*(\d{2,5})"
    r"|(\d{2,5})\s*(?:rs|rupees|₹|/-)?\s*(?:tak|ke andar|mein|or less|and below|max)\b", re.I)
ETA_PATTERN = re.compile(r"(\d{1,3})\s*(?:min|mins|minute|minutes)\b", re.I)
HOUR_PATTERN = re.compile(r"\b(half an hour|an hour|one hour|(\d(?:\.\d)?)\s*(?:hr|hrs|hour|hours))\b", re.I)

CHEAPER_PATTERN = re.compile(r"\b(cheaper|less expensive|lower (?:the )?(?:price|budget)|budget[- ]friendly|"
                             r"reduce (?:the )?(?:price|budget)|too expensive|too costly)\b", re.I)
PRICIER_PATTERN = re.compile(r"\b((?:increase|raise|higher|more) (?:the )?budget|spend more|premium|"
                             r"money is not an issue)\b", re.I)
FASTER_PATTERN = re.compile(r"\b(faster|quicker|sooner|less time|hurry|asap|too slow)\b", re.I)
SLOWER_PATTERN = re.compile(r"\b(no rush|more time|can wait|slower is fine|not in a hurry)\b", re.I)
REFRESH_PATTERN = re.compile(r"\b(refresh|recheck|re-check|check again|latest prices|fresh prices|search again)\b", re.I)
REPEAT_PATTERN = re.compile(r"\b(same|again|usual|last time|repeat|reorder)\b", re.I)
ORDER_PATTERN = re.compile(r"\b(order|get me|i want|i'?d like|hungry|again|same|usual|deliver|food|craving|find|"
                           r"looking for|show me|mangwa|chahiye)\b", re.I)

COMMAND_PATTERNS = [
    ("approve", re.compile(r"^\s*(yes|yep|yeah|confirm|place (?:the )?order|go ahead|book it|order it|pay|"
                           r"looks good|ok(?:ay)?|sure)\b", re.I)),
    ("next", re.compile(r"\b(next|another (?:one|option)|other option|something else|show (?:me )?(?:another|"
                        r"the next)|don'?t like (?:it|this)|not this|skip)\b", re.I)),
    ("cancel", re.compile(r"\b(cancel|never ?mind|forget it|stop the order)\b", re.I)),
    ("explain", re.compile(r"^\s*(why|explain|how did you (?:choose|pick)|what'?s the reason)\b", re.I)),
    ("recap", re.compile(r"\b(what did i (?:ask|order|say|want)|recap|summari[sz]e|my context|remind me|"
                         r"what do you remember)\b", re.I)),
]

# Words that are never part of a dish name in a food order.
FILLERS = set("""
a an the some any one two three four five six i im i'm me my we us you your u can could would will please pls plz
want wanna need get give find show bring send order ordering deliver delivered delivery for to of in on at with and
or but from like just also maybe something anything food dish meal item items hungry craving crave feel feeling having
have eat eating something nice good best tasty delicious yummy spicy quick fast cheap healthy really very
it this that these those there here hello hi hey thanks thank ok okay now today tonight lunch dinner breakfast snack
under below within budget max maximum minimum less than more upto up minutes mins min time price rs rupees inr only
same again usual last repeat reorder what about how why which is are was be do does did whats what's let lets let's
bhai yaar bro mangwa mangao mangwado de do tak ka ki ke chahiye wala wali kuch aur ek abhi jaldi please
around nearby near place restaurant restaurants cafe options option home hostel campus
yes yep yeah sure confirm cancel next skip no not explain go ahead book pay looks fine great
as so too well then if else all every much many few lot lots bit little kind sort type style way thing things stuff
am was were been being has had should shall may might must could get got getting gimme lemme wanna gonna
quickly quick soon asap urgently right away immediately
cheaper faster quicker sooner refresh latest check recheck search
""".split())

QUANTITY = re.compile(r"\b\d+\s*(?:x|pcs|pieces|plates?|nos)?\b", re.I)
SPELLING = {"biriyani": "biryani", "biriani": "biryani", "briyani": "biryani", "pizzza": "pizza",
            "sandwhich": "sandwich", "burgur": "burger", "coffe": "coffee"}

PARSER_SYSTEM_PROMPT = (
    "You extract a food order from a customer message. Reply with exactly one JSON object and nothing else. "
    'Keys: "dish" (the food or drink they want, copied from the message, lowercase, or null), '
    '"is_veg" (true, false or null), "max_price" (number or null), "max_eta_minutes" (integer or null). '
    "Use null for anything the message does not state."
)

FIELDS = ("item", "is_veg", "max_price", "max_eta_minutes")


def normalise_message(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _tokens(text: str) -> list:
    return re.findall(r"[a-z][a-z'-]*", text.lower())


def _strip_constraints(text: str) -> str:
    lowered = text.lower()
    for pattern in (PRICE_PATTERN, ETA_PATTERN, HOUR_PATTERN, CHEAPER_PATTERN, PRICIER_PATTERN, FASTER_PATTERN,
                    SLOWER_PATTERN, REFRESH_PATTERN, DIET_WORDS, QUANTITY):
        lowered = pattern.sub(" | ", lowered)
    return lowered


def _spell(word: str) -> str:
    return SPELLING.get(word, word)


def extract_dish_rules(text: str):
    """Longest run of words that are not constraints or filler, e.g. 'masala dosa'."""
    runs, current = [], []
    for chunk in re.split(r"[|,.;!?\n]", _strip_constraints(text)):
        for word in _tokens(chunk):
            if word in FILLERS:
                if current:
                    runs.append(current)
                current = []
            else:
                current.append(word)
        if current:
            runs.append(current)
        current = []
    if not runs:
        return None
    best = max(runs, key=len)  # first longest run
    return " ".join(_spell(w) for w in best[:4])


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def reconcile_dish(text: str, model_dish, rule_dish) -> tuple:
    """Pick the dish from the model and rule readings. Returns (dish, method)."""
    message_words = _tokens(text)
    singular_words = [_singular(w) for w in message_words]
    model_words = _tokens(DIET_WORDS.sub(" ", str(model_dish or "")))  # diet is its own field, not part of the dish
    grounded = bool(model_words) and all(_singular(w) in singular_words for w in model_words)
    if not grounded:
        return rule_dish, ("rules" if rule_dish else "none") + ("; model answer rejected" if model_words else "")
    # Widen the model's answer with the neighbouring words that the rules also kept ("dosa" -> "masala dosa").
    rule_words = set(_tokens(rule_dish or ""))
    start = singular_words.index(_singular(model_words[0]))
    end = start + len(model_words)
    while start > 0 and message_words[start - 1] in rule_words:
        start -= 1
    while end < len(message_words) and message_words[end] in rule_words:
        end += 1
    widened = " ".join(_spell(w) for w in message_words[start:end] if w not in FILLERS) or None
    if widened is None:
        return rule_dish, "rules"
    method = "model + rules agree" if widened == rule_dish else ("model, widened by rules"
                                                                  if len(_tokens(widened)) > len(model_words) else "model")
    return widened, method


def _price(text: str):
    match = PRICE_PATTERN.search(text)
    if not match:
        return None
    return float(next(g for g in match.groups() if g))


def _eta(text: str):
    match = ETA_PATTERN.search(text)
    if match:
        return int(match.group(1))
    hours = HOUR_PATTERN.search(text)
    if hours:
        phrase = hours.group(1).lower()
        if phrase == "half an hour":
            return 30
        if phrase in ("an hour", "one hour"):
            return 60
        return int(float(hours.group(2)) * 60)
    return None


def extract_explicit_fields(text: str) -> dict:
    is_veg = False if NON_VEG_PATTERN.search(text) else (True if VEG_PATTERN.search(text) else None)
    return {"item": extract_dish_rules(text), "is_veg": is_veg, "max_price": _price(text), "max_eta_minutes": _eta(text)}


def detect_modifiers(text: str) -> list:
    found = []
    for name, pattern in (("cheaper", CHEAPER_PATTERN), ("pricier", PRICIER_PATTERN), ("faster", FASTER_PATTERN),
                          ("slower", SLOWER_PATTERN), ("refresh", REFRESH_PATTERN)):
        if pattern.search(text):
            found.append(name)
    return found


def classify_intent(text: str) -> str:
    explicit = extract_explicit_fields(text)
    if any(explicit[k] is not None for k in ("is_veg", "max_price", "max_eta_minutes")) or detect_modifiers(text):
        return "order"
    for intent, pattern in COMMAND_PATTERNS:
        if pattern.search(text):
            return intent
    if explicit["item"] or ORDER_PATTERN.search(text):
        return "order"
    return "unknown"


def _to_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_message(llm, text: str) -> dict:
    explicit = extract_explicit_fields(text)
    raw_output = llm.generate(PARSER_SYSTEM_PROMPT, f"Customer message: {text}", max_new_tokens=80)
    proposed = extract_json(raw_output) or {}
    numbers_in_text = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)}
    fields = {}
    for key in ("max_price", "max_eta_minutes"):
        value = _to_number(proposed.get(key))
        accepted = value is not None and value in numbers_in_text and explicit[key] in (None, value)
        fields[key] = value if accepted else explicit[key]
        if key == "max_eta_minutes" and fields[key] is not None:
            fields[key] = int(fields[key])
    if explicit["is_veg"] is not None:
        fields["is_veg"] = explicit["is_veg"]
    else:
        fields["is_veg"] = proposed["is_veg"] if isinstance(proposed.get("is_veg"), bool) else None
    model_dish = proposed.get("dish") or proposed.get("item")
    fields["item"], dish_method = reconcile_dish(text, model_dish, explicit["item"])
    return {"fields": fields, "modifiers": detect_modifiers(text), "repeat": bool(REPEAT_PATTERN.search(text)),
            "raw_output": raw_output, "dish": {"rules": explicit["item"], "model": model_dish,
                                               "chosen": fields["item"], "method": dish_method},
            "llm_agreed": {k: fields[k] == explicit[k] for k in FIELDS}}


def apply_session_context(parsed: dict, context: dict) -> tuple:
    fields, sources, notes = dict(parsed["fields"]), {}, []
    previous = context.get("constraints") or {}
    turn = context.get("last_order_turn")
    selection = (context.get("pending") or {}).get("selection") or context.get("last_selection")

    for key in FIELDS:
        if fields[key] is not None:
            sources[key] = "this message"
        elif previous.get(key) is not None:
            fields[key] = previous[key]
            sources[key] = f"session context (turn {turn})"

    modifiers = parsed["modifiers"]
    if "cheaper" in modifiers and parsed["fields"]["max_price"] is None:
        if selection:
            fields["max_price"] = float(selection["price"] - 1)
            notes.append(f"cheaper than the last suggestion (Rs {selection['price']})")
        elif fields.get("max_price"):
            fields["max_price"] = float(round(fields["max_price"] * 0.8))
            notes.append("budget lowered by 20%")
        sources["max_price"] = "refined from session context"
    if "pricier" in modifiers and parsed["fields"]["max_price"] is None and fields.get("max_price"):
        fields["max_price"] = float(round(fields["max_price"] * 1.25))
        sources["max_price"] = "refined from session context"
        notes.append("budget raised by 25%")
    if "faster" in modifiers and parsed["fields"]["max_eta_minutes"] is None:
        if selection:
            fields["max_eta_minutes"] = max(int(selection["eta_minutes"]) - 1, 5)
            notes.append(f"faster than the last suggestion ({selection['eta_minutes']} min)")
        elif fields.get("max_eta_minutes"):
            fields["max_eta_minutes"] = max(int(fields["max_eta_minutes"]) - 5, 5)
            notes.append("delivery window shortened by 5 min")
        sources["max_eta_minutes"] = "refined from session context"
    if "slower" in modifiers and parsed["fields"]["max_eta_minutes"] is None and fields.get("max_eta_minutes"):
        fields["max_eta_minutes"] = int(fields["max_eta_minutes"]) + 15
        sources["max_eta_minutes"] = "refined from session context"
        notes.append("delivery window extended by 15 min")
    if "refresh" in modifiers:
        notes.append("fresh prices requested")
    return fields, sources, notes


def complete_from_memory(fields: dict, sources: dict, recalled: list, defaults: dict) -> tuple:
    """Fill missing values from the most similar past order, then from defaults. The dish has no default."""
    completed, completed_sources = dict(fields), dict(sources)
    past_orders = sorted((m for m in recalled if m["memory_type"] == "order"),
                         key=lambda m: m["similarity"], reverse=True)
    for key in FIELDS:
        if completed.get(key) is not None:
            continue
        if past_orders and past_orders[0].get("constraints", {}).get(key) is not None:
            completed[key] = past_orders[0]["constraints"][key]
            completed_sources[key] = f"long-term memory ({past_orders[0]['memory_id']})"
        elif key in defaults:
            completed[key] = defaults[key]
            completed_sources[key] = "default policy"
        else:
            completed[key] = None
    if completed.get("max_price") is not None:
        completed["max_price"] = float(completed["max_price"])
    if completed.get("max_eta_minutes") is not None:
        completed["max_eta_minutes"] = int(completed["max_eta_minutes"])
    return completed, completed_sources
