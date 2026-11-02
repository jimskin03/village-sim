"""Private, proposed pre-simulation life histories and bounded recall."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from .knowledge import TRAIT_NAMES
from .memory import _tokens

ROOT = Path(__file__).resolve().parents[1] / "data" / "brains"
ROSTER = ("mara", "tom", "lin", "ada", "ivo", "nell", "ren", "bea")
NAMES = {"mara": "Mara", "tom": "Tom", "lin": "Lin", "ada": "Ada",
         "ivo": "Ivo", "nell": "Nell", "ren": "Ren", "bea": "Bea"}
VERSION = "lantern-bay-villager-life-brains-v1"
SAMPLE = 4
WORK_PLACES = {"jetty", "shore", "garden", "west_garden", "orchard", "nets"}
# Node indexes are the authored order of one shared incident, checked across both villagers.
SHARED_SCENES = (
    ("mara", 17, "ada", 12),   # rescue
    ("mara", 16, "ada", 11),   # mentorship
    ("mara", 21, "ada", 18),   # cloudbank warning
    ("tom", 15, "nell", 11),   # hairline crack
    ("tom", 16, "nell", 12),   # apology
    ("tom", 14, "lin", 13),    # garden water gate
    ("bea", 13, "lin", 11),    # Lin's arrival
    ("bea", 14, "lin", 12),    # shelter seedlings
    ("mara", 13, "bea", 11),   # ration debate
    ("ada", 12, "ivo", 16),    # storm comfort immediately after rescue
    ("ada", 17, "ren", 11),    # copied sea route
)
_PACK = None


def _load_pack():
    global _PACK
    if _PACK is not None:
        return _PACK
    combined = json.loads((ROOT / "villager-brains.json").read_text(encoding="utf-8"))
    if (combined.get("id") != VERSION or set(combined.get("villagers", {})) != set(ROSTER)
            or set(combined.get("active_roster_at_draft", [])) != set(ROSTER) - {"ren", "bea"}
            or set(combined.get("optional_roster", [])) != {"ren", "bea"}
            or combined.get("memory_policy", {}).get("append_only_runtime_events") is not True):
        raise ValueError("Invalid autobiographical brain pack manifest")
    brains = {}
    for aid in ROSTER:
        brain = json.loads((ROOT / f"{aid}.json").read_text(encoding="utf-8"))
        if brain != combined["villagers"][aid]:
            raise ValueError(f"The consolidated and individual brain differ for {aid}")
        validate_brain(brain, aid)
        brains[aid] = brain
    _PACK = brains
    return _PACK


def validate_brain(brain, aid=None):
    """Reject malformed biography data before it enters a village checkpoint."""
    biography = brain.get("biography") if isinstance(brain, dict) else None
    if (not isinstance(brain, dict) or brain.get("resident_id") not in ROSTER
            or (aid is not None and brain["resident_id"] != aid)
            or brain.get("id") != f"lantern-bay-{brain['resident_id']}-life-v1"
            or not isinstance(biography, dict) or biography.get("fictional") is not True
            or not isinstance(biography.get("summary"), str)
            or not 1 <= len(biography["summary"]) <= 500
            or type(brain.get("age")) is not int or brain["age"] < 1):
        raise ValueError("Invalid autobiographical brain identity")
    nodes = brain.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 24:
        raise ValueError("An autobiographical brain must contain 24 memory anchors")
    ids = set()
    eras = {era.get("id") for era in brain["biography"].get("eras", []) if isinstance(era, dict)}
    for node in nodes:
        if (not isinstance(node, dict) or not isinstance(node.get("id"), str)
                or not node["id"].startswith(brain["resident_id"] + "-m")
                or node["id"] in ids or node.get("era") not in eras
                or node.get("kind") not in ("event", "learned", "routine")
                or node.get("certainty") not in ("anchor", "gist", "fragment")
                or not isinstance(node.get("fact"), str) or not 1 <= len(node["fact"]) <= 1200
                or not isinstance(node.get("age"), list) or len(node["age"]) != 2
                or any(type(value) is not int for value in node["age"])
                or not 0 <= node["age"][0] <= node["age"][1] <= brain.get("age", -1)
                or type(node.get("strength")) not in (float, int)
                or not 0 <= node["strength"] <= 1):
            raise ValueError("Invalid autobiographical memory anchor")
        ids.add(node["id"])
    pathways = brain.get("pathways")
    if not isinstance(pathways, list) or not pathways:
        raise ValueError("Invalid autobiographical recall pathways")
    pathway_ids = {item.get("id") for item in pathways if isinstance(item, dict)}
    for pathway in pathways:
        if (not pathway.get("route") or not set(pathway["route"]) <= ids
                or not isinstance(pathway.get("cues"), list)
                or not all(isinstance(cue, str) for cue in pathway["cues"])):
            raise ValueError("Invalid autobiographical pathway")
    for link in brain.get("links", []):
        if (not isinstance(link, list) or len(link) != 3 or link[0] not in pathway_ids
                or link[1] not in pathway_ids or type(link[2]) not in (int, float)
                or not 0 <= link[2] <= 1):
            raise ValueError("Invalid autobiographical association")
    facets = brain.get("behavior", {}).get("facets")
    if not isinstance(facets, list) or len(facets) != 5:
        raise ValueError("Invalid autobiographical behavior facets")
    for facet in facets:
        evidence = facet.get("evidence") if isinstance(facet, dict) else None
        if (not isinstance(evidence, list) or not evidence
                or not isinstance(facet.get("id"), str)
                or not isinstance(facet.get("label"), str)
                or not isinstance(facet.get("cues"), list)
                or not all(isinstance(cue, str) for cue in facet["cues"])
                or not isinstance(facet.get("alternative"), str)
                or any(not isinstance(ref, list) or len(ref) != 2 or ref[0] not in ids
                       or type(ref[1]) not in (int, float) or not 0 <= ref[1] <= 1
                       for ref in evidence)):
            raise ValueError("Invalid evidence-linked behavior facet")
    traits = brain.get("starting_traits")
    trait_names = {"risk", "patience", "fairness", "generosity", "sociability", "home", "deference"}
    if (not isinstance(traits, dict) or set(traits) != trait_names
            or any(type(value) not in (int, float) or not 0 <= value <= 1 for value in traits.values())):
        raise ValueError("Invalid autobiographical starting traits")
    goals = brain.get("latent_goals")
    if (not isinstance(goals, list) or not goals
            or any(not isinstance(goal, dict) or not isinstance(goal.get("id"), str)
                   or not isinstance(goal.get("description"), str)
                   or not isinstance(goal.get("cues"), list)
                   or not all(isinstance(cue, str) for cue in goal["cues"])
                   for goal in goals)):
        raise ValueError("Invalid autobiographical latent goals")
    social = brain.get("initial_social_context")
    if (not isinstance(social, list)
            or any(not isinstance(item, dict) or item.get("resident_id") not in ROSTER
                   or item["resident_id"] == brain["resident_id"]
                   or not isinstance(item.get("subjective_relationship"), str)
                   or type(item.get("suggested_initial_trust")) not in (int, float)
                   or not -1 <= item["suggested_initial_trust"] <= 1
                   or item.get("seed_only") is not True
                   for item in social)):
        raise ValueError("Invalid autobiographical social context")
    return True


def seed(aid):
    """Return one resident's own immutable source copy."""
    return copy.deepcopy(_load_pack()[aid])


def _validate_lived(brain):
    """Check private outcome tallies and revisions without touching the authored anchors."""
    revisions = brain.get("revisions", [])
    facets = {facet["id"] for facet in brain["behavior"]["facets"]}
    if not isinstance(revisions, list) or len(revisions) > len(facets):
        raise ValueError("Invalid autobiographical revisions")
    seen = set()
    for revision in revisions:
        evidence = revision.get("evidence") if isinstance(revision, dict) else None
        if (revision.get("facet_id") not in facets or revision["facet_id"] in seen
                or not isinstance(revision.get("reading"), str) or not revision["reading"]
                or not isinstance(revision.get("previous"), str) or not revision["previous"]
                or revision.get("place") not in WORK_PLACES
                or type(revision.get("step")) is not int or revision["step"] < 0
                or not isinstance(evidence, list) or len(evidence) > 8
                or any(type(item) is not int or item < 0 for item in evidence)):
            raise ValueError("Invalid autobiographical revision")
        seen.add(revision["facet_id"])
    experience = brain.get("experience", {})
    if not isinstance(experience, dict) or not set(experience) <= WORK_PLACES:
        raise ValueError("Invalid autobiographical experience")
    for record in experience.values():
        events = record.get("events") if isinstance(record, dict) else None
        if (type(record.get("n")) is not int or record["n"] < 1
                or type(record.get("successes")) is not int
                or not 0 <= record["successes"] <= record["n"]
                or not isinstance(events, list) or len(events) > 8
                or any(type(item) is not int or item < 0 for item in events)):
            raise ValueError("Invalid autobiographical experience")


def validate_state(brains, residents):
    if not isinstance(brains, dict) or not set(brains) <= set(residents) <= set(ROSTER):
        raise ValueError("Invalid resident autobiographies")
    for aid, brain in brains.items():
        validate_brain(brain, aid)
        _validate_lived(brain)


def _years_ago(brain, index):
    return brain["age"] - brain["nodes"][index - 1]["age"][0]


def validate_pack():
    """Validate all 192 anchors, 40 facets, and the shared-scene chronology."""
    brains = _load_pack()
    seen = set()
    for brain in brains.values():
        for node in brain["nodes"]:
            if node["id"] in seen:
                raise ValueError(f"Duplicate autobiographical memory {node['id']}")
            seen.add(node["id"])
    facet_count = sum(len(brain["behavior"]["facets"]) for brain in brains.values())
    if len(seen) != 192 or facet_count != 40:
        raise ValueError("The autobiographical pack lost its authored memories or facets")
    for left, left_index, right, right_index in SHARED_SCENES:
        if abs(_years_ago(brains[left], left_index) - _years_ago(brains[right], right_index)) > 1:
            raise ValueError(f"Shared scene ages differ for {left} and {right}")
    return (f"Validated {len(brains)} fictional character biographies, {len(seen)} memory nodes, "
            f"{facet_count} evidence-linked facets, and {len(SHARED_SCENES)} cross-character chronological checks.")


def identity_traits(brain):
    """Trait values for a resident being created. Import does not call this."""
    traits = brain["starting_traits"]
    return {name: float(traits[name]) for name in TRAIT_NAMES}


def suggested_trust(sim, aid, other):
    """Biography trust breaks a tie. It is not written onto the resident."""
    if not getattr(sim, "autobiography_enabled", False):
        return 0.0
    brain = getattr(sim, "autobiographies", {}).get(aid)
    if not brain:
        return 0.0
    for item in brain.get("initial_social_context", []):
        if item.get("resident_id") == other:
            return float(item.get("suggested_initial_trust") or 0.0)
    return 0.0


def _score(query, values):
    query_words = _tokens(query)
    words = set()
    for value in values:
        if isinstance(value, str):
            words.update(_tokens(value))
    if not query_words or not words:
        return 0.0
    return len(query_words & words) / (len(query_words) * len(words)) ** 0.5


def _route_boost(brain, query):
    """Direct cue routes, plus linked routes scaled by the association weight."""
    direct = {}
    for pathway in brain["pathways"]:
        if _score(query, pathway.get("cues", [])) >= 0.16:
            direct[pathway["id"]] = 0.12
    by_id = {pathway["id"]: pathway for pathway in brain["pathways"]}
    boost = {}
    for pathway_id, amount in direct.items():
        for node_id in by_id[pathway_id]["route"]:
            boost[node_id] = max(boost.get(node_id, 0.0), amount)
    spread = {}
    for link in brain.get("links", []):
        source, dest, weight = link
        if source in direct and dest in by_id:
            spread[dest] = max(spread.get(dest, 0.0), direct[source] * float(weight))
        if dest in direct and source in by_id:
            spread[source] = max(spread.get(source, 0.0), direct[dest] * float(weight))
    for pathway_id, amount in spread.items():
        for node_id in by_id[pathway_id]["route"]:
            boost[node_id] = max(boost.get(node_id, 0.0), amount)
    return boost


def recall(brain, query, *, memory_limit=4, facet_limit=2):
    """Retrieve subjective anchors without simulated-recency decay."""
    if not brain:
        return None
    route_boost = _route_boost(brain, query)
    revisions = {item["facet_id"]: item for item in brain.get("revisions", [])}
    ranked_nodes = []
    for node in brain["nodes"]:
        lexical = _score(query, [node["fact"], node.get("feeling", ""), node.get("domain", ""),
                                *node.get("fragments", [])])
        relevance = lexical + route_boost.get(node["id"], 0.0)
        if relevance:
            ranked_nodes.append((relevance * 0.75 + node["strength"] * 0.25, lexical, node))
    order = sorted(ranked_nodes, key=lambda pair: (pair[0], pair[2]["strength"], -pair[2]["age"][1]), reverse=True)
    selected = [node for _, _, node in order[:memory_limit]]
    # One slot stays open for a scene reached only through an associative link.
    associated = next((node for _, lexical, node in order if lexical == 0 and node["id"] in route_boost), None)
    if associated is not None and associated not in selected and selected:
        selected[-1] = associated
    evidence_strength = {node["id"]: node["strength"] for node in brain["nodes"]}
    facets = []
    for facet in brain["behavior"]["facets"]:
        revision = revisions.get(facet["id"])
        relevance = _score(query, [*(facet.get("cues", [])), facet.get("label", ""),
                                   *([revision["reading"]] if revision else [])])
        if relevance:
            evidence = sorted(facet["evidence"], key=lambda ref: ref[1] * evidence_strength[ref[0]], reverse=True)
            facets.append((relevance, {
                "id": facet["id"],
                "label": revision["reading"] if revision else facet["label"],
                "previous": revision["previous"] if revision else None,
                "alternative": facet.get("alternative", ""),
                "revisable": bool(facet.get("revisable")),
                "revised": revision is not None,
                "evidence": [ref[0] for ref in evidence[:2]],
                "live_evidence": list(revision["evidence"]) if revision else [],
            }))
    facets = [item for _, item in sorted(facets, key=lambda pair: pair[0], reverse=True)[:facet_limit]]
    goals = []
    for goal in brain.get("latent_goals", []):
        relevance = _score(query, [goal.get("description", ""), *goal.get("cues", [])])
        if relevance:
            goals.append((relevance, {"id": goal["id"], "description": goal["description"]}))
    goals = [item for _, item in sorted(goals, key=lambda pair: pair[0], reverse=True)[:1]]
    query_words = _tokens(query)
    social_context = [
        {"resident": NAMES[item["resident_id"]],
         "impression": item["subjective_relationship"],
         "source": "this villager's proposed private recollection"}
        for item in brain.get("initial_social_context", [])
        if item.get("resident_id") in NAMES and NAMES[item["resident_id"]].casefold() in query_words
    ][:2]
    return {
        "source": "proposed fictional pre-simulation history; private and possibly mistaken",
        "identity": brain["biography"]["summary"][:360],
        "memories": [{"id": node["id"], "age": node["age"], "era": node["era"],
                      "kind": node["kind"], "certainty": node["certainty"],
                      "feeling": node.get("feeling"), "text": node["fact"][:240]}
                     for node in selected],
        "facets": facets,
        "latent_goals": goals,
        "social_context": social_context,
    }


def workplace_informed(brain, places):
    """Four outcomes at a candidate place are enough for lived experience to win."""
    if not brain:
        return False
    experience = brain.get("experience") or {}
    return any(isinstance(experience.get(place), dict) and experience[place].get("n", 0) >= SAMPLE
               for place in places)


def bias_work_location(brain, query, action, candidates):
    """Use one relevant, evidence-backed facet to break a routine location tie."""
    if (not brain or action.place not in candidates or len(candidates) < 2
            or workplace_informed(brain, candidates)):
        return action
    revised = {item["facet_id"] for item in brain.get("revisions", [])}
    best_choice = None
    for facet in brain["behavior"]["facets"]:
        if facet["id"] in revised:
            continue
        cues = facet.get("cues", [])
        relevance = _score(f"{query} {action.activity}", [*cues, facet["label"]])
        place_scores = {place: _score(place.replace("_", " "), cues) for place in candidates}
        best_place = max(candidates, key=lambda place: place_scores[place])
        if relevance >= 0.05 and place_scores[best_place] > 0:
            score = relevance + place_scores[best_place]
            if best_choice is None or score > best_choice[0]:
                best_choice = (score, best_place, place_scores[action.place], place_scores[best_place])
    if best_choice and best_choice[1] != action.place and best_choice[3] > best_choice[2]:
        return type(action)(best_choice[1], action.activity, thought=action.thought)
    return action


def note_work(brain, *, place, success, event_id, step):
    """Record a real outcome. A poor run adopts the facet's alternative and leaves the anchor text."""
    if not brain or place not in WORK_PLACES or type(event_id) is not int or type(step) is not int:
        return None
    record = brain.setdefault("experience", {}).setdefault(place, {"n": 0, "successes": 0, "events": []})
    record["n"] += 1
    record["successes"] += 1 if success else 0
    record["events"] = [*record["events"], event_id][-8:]
    if record["n"] < SAMPLE or record["successes"] / record["n"] >= 0.4:
        return None
    revisions = brain.setdefault("revisions", [])
    revised_ids = {item["facet_id"] for item in revisions}
    written = None
    for facet in brain["behavior"]["facets"]:
        if (not facet.get("revisable") or facet["id"] in revised_ids
                or _score(place.replace("_", " "), facet.get("cues", [])) <= 0):
            continue
        written = {
            "facet_id": facet["id"],
            "reading": facet["alternative"],
            "previous": facet["label"],
            "evidence": list(record["events"]),
            "place": place,
            "step": step,
        }
        revisions.append(written)
        revised_ids.add(facet["id"])
    return written
