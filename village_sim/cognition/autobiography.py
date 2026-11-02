"""Private, proposed pre-simulation life histories and bounded recall."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from .memory import _tokens

ROOT = Path(__file__).resolve().parents[1] / "data" / "brains"
ROSTER = ("mara", "tom", "lin", "ada", "ivo", "nell", "ren", "bea")
NAMES = {"mara": "Mara", "tom": "Tom", "lin": "Lin", "ada": "Ada",
         "ivo": "Ivo", "nell": "Nell", "ren": "Ren", "bea": "Bea"}
VERSION = "lantern-bay-villager-life-brains-v1"
_PACK = None


def _load_pack():
    global _PACK
    if _PACK is not None:
        return _PACK
    combined = json.loads((ROOT / "villager-brains.json").read_text(encoding="utf-8"))
    if (combined.get("id") != VERSION or set(combined.get("villagers", {})) != set(ROSTER)
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


def validate_state(brains, residents):
    if not isinstance(brains, dict) or not set(brains) <= set(residents) <= set(ROSTER):
        raise ValueError("Invalid resident autobiographies")
    for aid, brain in brains.items():
        validate_brain(brain, aid)


def _score(query, values):
    query_words = _tokens(query)
    words = set()
    for value in values:
        if isinstance(value, str):
            words.update(_tokens(value))
    if not query_words or not words:
        return 0.0
    return len(query_words & words) / (len(query_words) * len(words)) ** 0.5


def recall(brain, query, *, memory_limit=4, facet_limit=2):
    """Retrieve subjective anchors without simulated-recency decay."""
    if not brain:
        return None
    route_boost = set()
    for pathway in brain["pathways"]:
        if _score(query, pathway.get("cues", [])) >= 0.16:
            route_boost.update(pathway["route"])
    ranked_nodes = []
    for node in brain["nodes"]:
        relevance = _score(query, [node["fact"], node.get("feeling", ""), node.get("domain", ""),
                                  *node.get("fragments", [])])
        if node["id"] in route_boost:
            relevance += 0.12
        if relevance:
            ranked_nodes.append((relevance * 0.75 + node["strength"] * 0.25, node))
    selected = [node for _, node in sorted(ranked_nodes,
                key=lambda pair: (pair[0], pair[1]["strength"], -pair[1]["age"][1]), reverse=True)[:memory_limit]]
    evidence_strength = {node["id"]: node["strength"] for node in brain["nodes"]}
    facets = []
    for facet in brain["behavior"]["facets"]:
        relevance = _score(query, [*facet.get("cues", []), facet.get("label", "")])
        if relevance:
            evidence = sorted(facet["evidence"], key=lambda ref: ref[1] * evidence_strength[ref[0]], reverse=True)
            facets.append((relevance, {"id": facet["id"], "label": facet["label"],
                             "alternative": facet.get("alternative", ""),
                             "revisable": bool(facet.get("revisable")),
                             "evidence": [ref[0] for ref in evidence[:2]]}))
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


def bias_work_location(brain, query, action, candidates):
    """Use one relevant, evidence-backed facet to break a routine location tie."""
    if not brain or action.place not in candidates or len(candidates) < 2:
        return action
    best_choice = None
    for facet in brain["behavior"]["facets"]:
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
