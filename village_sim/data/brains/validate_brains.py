"""Validate the authored Lantern Bay brain draft without importing it into a village."""
from pathlib import Path
import json

root = Path(__file__).resolve().parent
pack = json.loads((root / "villager-brains.json").read_text(encoding="utf-8"))
roster = {"mara", "tom", "lin", "ada", "ivo", "nell", "ren", "bea"}
assert set(pack["villagers"]) == roster
assert set(pack["active_roster_at_draft"]) == roster - {"ren", "bea"}
assert set(pack["optional_roster"]) == {"ren", "bea"}
seen_ids = set()
for name, brain in pack["villagers"].items():
    individual = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
    assert brain == individual, f"per-villager copy differs: {name}"
    assert brain["resident_id"] == name
    assert len(brain["nodes"]) == 24
    assert len(brain["behavior"]["facets"]) == 5
    ids = set()
    eras = {e["id"] for e in brain["biography"]["eras"]}
    for item in brain["nodes"]:
        assert item["id"].startswith(name + "-m")
        assert item["id"] not in seen_ids and item["id"] not in ids
        ids.add(item["id"])
        assert item["kind"] in {"event", "learned", "routine"}
        assert item["certainty"] in {"anchor", "gist", "fragment"}
        assert item["era"] in eras
        assert 0 <= item["age"][0] <= item["age"][1] <= brain["age"]
        assert 0 <= item["strength"] <= 1
        assert len(item["fact"]) <= 1200
    seen_ids |= ids
    assert set(brain["starting_traits"]) == {"risk", "patience", "fairness", "generosity", "sociability", "home", "deference"}
    assert all(0 <= v <= 1 for v in brain["starting_traits"].values())
    pids = {p["id"] for p in brain["pathways"]}
    for route in brain["pathways"]:
        assert route["route"] and set(route["route"]).issubset(ids)
    for edge in brain["links"]:
        assert edge[0] in pids and edge[1] in pids and 0 <= edge[2] <= 1
    for facet in brain["behavior"]["facets"]:
        assert facet["evidence"] and set(r[0] for r in facet["evidence"]).issubset(ids)
        assert all(0 <= r[1] <= 1 for r in facet["evidence"])
    for person in brain["initial_social_context"]:
        assert person["resident_id"] in roster - {name}
        assert -1 <= person["suggested_initial_trust"] <= 1

# The two sides of important shared backstory incidents must align chronologically.
shared = [
    ("mara", 17, "ada", 12),  # rescue
    ("mara", 16, "ada", 11),  # mentorship
    ("mara", 21, "ada", 18),  # cloudbank warning
    ("tom", 15, "nell", 11),  # hairline crack
    ("tom", 16, "nell", 12),  # apology
    ("tom", 14, "lin", 13),   # garden water gate
    ("bea", 13, "lin", 11),   # Lin's arrival
    ("bea", 14, "lin", 12),   # shelter seedlings
    ("mara", 13, "bea", 11),  # ration debate
    ("ada", 12, "ivo", 16),  # storm comfort immediately after rescue
    ("ada", 17, "ren", 11),  # copied sea route
]
for aid, ai, bid, bi in shared:
    a = pack["villagers"][aid]
    b = pack["villagers"][bid]
    years_ago_a = a["age"] - a["nodes"][ai - 1]["age"][0]
    years_ago_b = b["age"] - b["nodes"][bi - 1]["age"][0]
    assert abs(years_ago_a - years_ago_b) <= 1, (aid, ai, bid, bi, years_ago_a, years_ago_b)
print(f"Validated {len(roster)} fictional character biographies, {len(seen_ids)} memory nodes, "
      f"{sum(len(v['behavior']['facets']) for v in pack['villagers'].values())} evidence-linked facets, "
      f"and {len(shared)} cross-character chronological checks.")
