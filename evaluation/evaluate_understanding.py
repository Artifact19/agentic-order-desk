"""Measure how well the agent reads requests: rules alone, the language model alone, and the two combined.

    python evaluation/evaluate_understanding.py                      # rules + Qwen2.5-0.5B and 1.5B
    python evaluation/evaluate_understanding.py --models Qwen/Qwen2.5-0.5B-Instruct
    python evaluation/evaluate_understanding.py --prompts evaluation/prompts_heldout.csv --name heldout

Writes evaluation/results/understanding_results.csv (one row per message and method) and
evaluation/results/understanding_summary.json, and prints a summary table.
"""
import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.llm import extract_json, load_llm  # noqa: E402
from app.understanding import PARSER_SYSTEM_PROMPT, extract_explicit_fields, parse_message  # noqa: E402

PROMPTS = ROOT / "evaluation" / "prompts.csv"
OUT = ROOT / "evaluation" / "results"


def norm(dish) -> str:
    words = re.findall(r"[a-z]+", str(dish or "").lower())
    return " ".join(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w for w in words)


def load_cases(path: Path = PROMPTS) -> list:
    with open(path, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["is_veg"] = {"true": True, "false": False}.get(row["is_veg"])
        row["max_price"] = float(row["max_price"]) if row["max_price"] else None
        row["max_eta_minutes"] = int(row["max_eta_minutes"]) if row["max_eta_minutes"] else None
    return rows


def score(case: dict, fields: dict) -> dict:
    dish_ok = norm(fields.get("item")) == norm(case["dish"])
    rest_ok = all(fields.get(k) == case[k] for k in ("is_veg", "max_price", "max_eta_minutes"))
    return {"dish_ok": dish_ok, "all_ok": dish_ok and rest_ok}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=["Qwen/Qwen2.5-0.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct"])
    parser.add_argument("--prompts", default=str(PROMPTS), help="labelled CSV (prompts.csv or prompts_heldout.csv)")
    parser.add_argument("--name", default="understanding", help="prefix for the result files")
    args = parser.parse_args()
    cases = load_cases(Path(args.prompts))
    OUT.mkdir(parents=True, exist_ok=True)
    rows, summary = [], {}

    def record(method, case, fields, ms, extra=""):
        result = score(case, fields)
        rows.append({"method": method, "message": case["message"], "expected": case["dish"],
                     "predicted": fields.get("item") or "", "dish_ok": result["dish_ok"], "all_fields_ok": result["all_ok"],
                     "ms": round(ms, 1), "note": extra})
        stats = summary.setdefault(method, {"dish_ok": 0, "all_ok": 0, "n": 0, "ms": []})
        stats["dish_ok"] += result["dish_ok"]
        stats["all_ok"] += result["all_ok"]
        stats["n"] += 1
        stats["ms"].append(ms)

    for case in cases:
        start = time.perf_counter()
        fields = extract_explicit_fields(case["message"])
        record("rules only", case, fields, (time.perf_counter() - start) * 1000)

    for model_id in args.models:
        llm, status = load_llm(model_id, True)
        short = model_id.split("/")[-1].replace("-Instruct", "")
        print(f"{model_id}: {status}")
        for case in cases:
            start = time.perf_counter()
            raw = llm.generate(PARSER_SYSTEM_PROMPT, f"Customer message: {case['message']}", max_new_tokens=80)
            ms = (time.perf_counter() - start) * 1000
            proposed = extract_json(raw) or {}
            model_fields = {"item": proposed.get("dish"), "is_veg": proposed.get("is_veg"),
                            "max_price": proposed.get("max_price"), "max_eta_minutes": proposed.get("max_eta_minutes")}
            for key, cast in (("max_price", float), ("max_eta_minutes", int)):
                try:
                    model_fields[key] = cast(model_fields[key]) if model_fields[key] is not None else None
                except (TypeError, ValueError):
                    model_fields[key] = None
            record(f"{short} only", case, model_fields, ms, raw.replace("\n", " ")[:120])
            start = time.perf_counter()
            parsed = parse_message(llm, case["message"])
            record(f"{short} + rules (agent)", case, parsed["fields"], (time.perf_counter() - start) * 1000,
                   parsed["dish"]["method"])

    with open(OUT / f"{args.name}_results.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    table = {}
    for method, stats in summary.items():
        ms = sorted(stats["ms"])
        table[method] = {"dish_accuracy": round(stats["dish_ok"] / stats["n"], 3),
                         "all_fields_accuracy": round(stats["all_ok"] / stats["n"], 3),
                         "dish_correct": stats["dish_ok"], "messages": stats["n"],
                         "median_ms": round(ms[len(ms) // 2], 1)}
    (OUT / f"{args.name}_summary.json").write_text(json.dumps(table, indent=2), encoding="utf-8")
    print(f"\n{'Method':34} {'Dish':>10} {'All fields':>11} {'Median ms':>10}")
    for method, t in table.items():
        print(f"{method:34} {t['dish_correct']:>3}/{t['messages']} {t['dish_accuracy']:>5.0%} "
              f"{t['all_fields_accuracy']:>10.0%} {t['median_ms']:>10.1f}")


if __name__ == "__main__":
    main()
