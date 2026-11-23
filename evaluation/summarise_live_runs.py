"""Summarise completed live runs from the app's database: where the time goes, and what was read on Zomato.

    python evaluation/summarise_live_runs.py            # writes evaluation/results/live_runs.csv and .json
"""
import csv
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import DATABASE_PATH  # noqa: E402

OUT = ROOT / "evaluation" / "results"


def main():
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
    rows = []
    for run in db.execute("SELECT run_id, input, status, resume_count FROM runs WHERE status = 'completed' ORDER BY created_at"):
        checkpoint = db.execute("SELECT state FROM checkpoints WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
                                (run["run_id"],)).fetchone()
        if not checkpoint:
            continue
        state = json.loads(checkpoint["state"])
        metrics = state.get("step_metrics", {})
        if "search" not in metrics:
            continue
        ms = {name: m.get("duration_ms", 0) for name, m in metrics.items()}
        menus = [v for k, v in ms.items() if k.startswith("menu:")]
        reused = (state.get("search") or {}).get("reused", False)
        best = (state.get("ranking") or [None])[0]
        rows.append({
            "message": json.loads(run["input"])["message"],
            "dish": state["constraints"]["item"],
            "dish_method": (state.get("parsed", {}).get("dish") or {}).get("method"),
            "cards_read": (state.get("search") or {}).get("cards_read", 0),
            "menus_read": len(menus),
            "reused_from_cache": reused,
            "dishes_in_budget": len(state.get("offers", [])),
            "understand_ms": round(ms.get("understand", 0)),
            "search_ms": round(ms.get("connect", 0) + ms.get("search", 0)),
            "menus_ms": round(sum(menus)),
            "rank_ms": round(ms.get("rank", 0), 1),
            "explain_ms": round(ms.get("explain", 0)),
            "total_s": round(sum(ms.values()) / 1000, 1),
            "resumed": run["resume_count"],
            "chosen": f"{best['item_name']} · {best['restaurant_name']} · Rs {best['price']} · {best.get('eta_range')}" if best else "none",
        })
    OUT.mkdir(parents=True, exist_ok=True)
    if rows:
        with open(OUT / "live_runs.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (OUT / "live_runs.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    for r in rows:
        print(f"{r['message'][:42]:44} cards {r['cards_read']:>2} menus {r['menus_read']} reused={r['reused_from_cache']!s:5} "
              f"total {r['total_s']:>5}s (search {r['search_ms']/1000:.1f}s, menus {r['menus_ms']/1000:.1f}s, "
              f"model {(r['understand_ms'] + r['explain_ms'])/1000:.1f}s, rank {r['rank_ms']} ms) -> {r['chosen'][:60]}")


if __name__ == "__main__":
    main()
