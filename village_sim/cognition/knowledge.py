"""What one resident can see, remember believing, and pass on by name.

Beliefs keep their evidence. A later report does not erase an earlier sighting,
and a listener remembers who spoke. The viewer snapshot stays omniscient.
"""

from __future__ import annotations

SEE = 4
PROVENANCE = ("observed", "told", "inferred")
TRAIT_NAMES = ("risk", "patience", "fairness", "generosity", "sociability", "home", "deference")

# Stable preferences. They tilt choices; they do not script a biography.
TRAITS = {
    "mara": {"risk": 0.35, "patience": 0.8, "fairness": 0.7, "generosity": 0.6, "sociability": 0.55, "home": 0.4, "deference": 0.45},
    "tom": {"risk": 0.55, "patience": 0.4, "fairness": 0.45, "generosity": 0.7, "sociability": 0.85, "home": 0.3, "deference": 0.35},
    "lin": {"risk": 0.25, "patience": 0.85, "fairness": 0.6, "generosity": 0.5, "sociability": 0.4, "home": 0.8, "deference": 0.5},
    "ada": {"risk": 0.8, "patience": 0.3, "fairness": 0.4, "generosity": 0.55, "sociability": 0.7, "home": 0.2, "deference": 0.25},
    "ivo": {"risk": 0.3, "patience": 0.75, "fairness": 0.65, "generosity": 0.45, "sociability": 0.25, "home": 0.7, "deference": 0.6},
    "nell": {"risk": 0.45, "patience": 0.6, "fairness": 0.55, "generosity": 0.5, "sociability": 0.6, "home": 0.5, "deference": 0.4},
    "ren": {"risk": 0.7, "patience": 0.35, "fairness": 0.3, "generosity": 0.4, "sociability": 0.65, "home": 0.35, "deference": 0.2},
    "bea": {"risk": 0.4, "patience": 0.5, "fairness": 0.9, "generosity": 0.9, "sociability": 0.8, "home": 0.55, "deference": 0.55},
}


def traits_for(aid: str) -> dict:
    return dict(TRAITS[aid])


def _distance(agent, place) -> int:
    return abs(agent.x - place["x"]) + abs(agent.y - place["y"])


def visible_places(sim, agent) -> list[str]:
    return [pid for pid, place in sim.world.places.items() if _distance(agent, place) <= SEE]


def remember(agent, *, subject, value, provenance, step, day, confidence, source=None, event_id=None):
    """Keep one belief per subject, provenance and source. Conflicts stay."""
    if provenance not in PROVENANCE:
        raise ValueError("Unknown belief provenance")
    confidence = round(float(confidence), 2)
    record = {"subject": subject, "value": value, "confidence": confidence,
              "provenance": provenance, "source": source, "event_id": event_id,
              "step": step, "day": day}
    for existing in agent.beliefs:
        if (existing["subject"] == subject and existing["provenance"] == provenance
                and existing["source"] == source):
            existing.update(record)
            return existing
    agent.beliefs.append(record)
    if len(agent.beliefs) > 64:
        agent.beliefs.sort(key=lambda item: (item["step"], item["confidence"]))
        del agent.beliefs[:-64]
    return record


def _rank(belief) -> tuple:
    order = {"observed": 3, "told": 2, "inferred": 1}
    return (belief["confidence"], order[belief["provenance"]], belief["step"])


def acting_belief(agent, subject):
    matches = [item for item in agent.beliefs if item["subject"] == subject]
    if not matches:
        return None
    return max(matches, key=_rank)


def observe_surroundings(sim, agent):
    """Record only the places this resident can see right now."""
    if sim.gameplay != "living":
        return []
    written = []
    eco = sim.ecology
    for pid in visible_places(sim, agent):
        if pid in eco["fish"]:
            ground = eco["fish"][pid]
            written.append(remember(
                agent, subject=f"fish:{pid}", value=ground["stock"], provenance="observed",
                step=sim.step, day=sim.day, confidence=0.9, event_id=None))
        elif pid in eco["gardens"]:
            plot = eco["gardens"][pid]
            written.append(remember(
                agent, subject=f"garden:{pid}", value=plot["fertility"], provenance="observed",
                step=sim.step, day=sim.day, confidence=0.9, event_id=None))
        elif pid in sim.water_sources:
            written.append(remember(
                agent, subject=f"water:{pid}", value=sim.water_sources[pid]["supply"],
                provenance="observed", step=sim.step, day=sim.day, confidence=0.9, event_id=None))
        elif pid == "food_center":
            written.append(remember(
                agent, subject="pantry:fish", value=sim.pantry["fish"], provenance="observed",
                step=sim.step, day=sim.day, confidence=0.9, event_id=None))
            written.append(remember(
                agent, subject="pantry:herbs", value=sim.pantry["herbs"], provenance="observed",
                step=sim.step, day=sim.day, confidence=0.9, event_id=None))
        elif pid == "water_center":
            written.append(remember(
                agent, subject="pantry:water", value=sim.pantry["water"], provenance="observed",
                step=sim.step, day=sim.day, confidence=0.9, event_id=None))
        elif pid == "nets":
            written.append(remember(
                agent, subject="tool:net", value=sim.pantry["net_condition"], provenance="observed",
                step=sim.step, day=sim.day, confidence=0.9, event_id=None))
        elif pid == "shore":
            written.append(remember(
                agent, subject="tool:boat", value=sim.pantry["boat_condition"], provenance="observed",
                step=sim.step, day=sim.day, confidence=0.9, event_id=None))
    return written


def _value(agent, subject, default=None):
    belief = acting_belief(agent, subject)
    return default if belief is None else belief["value"]


def localize(sim, agent, observation):
    """Replace omniscient stocks and distant activity with this resident's knowledge."""
    observe_surroundings(sim, agent)
    nearby = {person["id"] for person in observation["nearby"]}
    observation["local_knowledge"] = True
    observation["knowledge"] = {
        "traits": dict(agent.traits),
        "beliefs": list(agent.beliefs),
        "skills": agent.skills,
        "estimates": agent.estimates,
        "lessons": [lesson for lesson in agent.lessons if not lesson.get("retired")][-8:],
        "visible_places": visible_places(sim, agent),
    }
    observation["villagers"] = [person for person in observation["villagers"] if person["id"] in nearby]
    observation["present_villagers"] = [
        person for person in observation["present_villagers"]
        if person["id"] == agent.id or person["id"] in nearby
    ]
    fish = _value(agent, "pantry:fish")
    herbs = _value(agent, "pantry:herbs")
    water = _value(agent, "pantry:water")
    observation["pantry"] = {
        "fish": fish, "herbs": herbs, "water": water,
        "boat_condition": _value(agent, "tool:boat", "unknown"),
        "net_condition": _value(agent, "tool:net", "unknown"),
        "hearth_lit": sim.pantry["hearth_lit"],
    }
    sources = {}
    for place in sim.water_sources:
        belief = acting_belief(agent, f"water:{place}")
        if belief is not None and belief["provenance"] == "observed":
            sources[place] = {"supply": belief["value"], "fouled_until": sim.water_sources[place]["fouled_until"]}
    observation["water_sources"] = sources
    known_fish = fish or 0
    known_herbs = herbs or 0
    known_water = water or 0
    centers = observation["collection_centers"]
    centers["food"]["fish"] = known_fish
    centers["food"]["herbs"] = known_herbs
    centers["food"]["level"] = known_fish * 40 + known_herbs * 15
    centers["food"]["low"] = centers["food"]["level"] < centers["food"]["daily_need"]
    centers["water"]["level"] = known_water
    centers["water"]["low"] = known_water < centers["water"]["daily_need"]
    observation["result_options"] = []
    return observation


def share(sim, speaker, listener):
    """Pass one belief the speaker actually holds. The listener records the speaker."""
    if sim.gameplay != "living" or speaker.id == listener.id:
        return None
    ranked = sorted((item for item in speaker.beliefs if item["subject"].split(":", 1)[0]
                     in ("fish", "garden", "water", "pantry", "tool")), key=_rank)
    if not ranked:
        return None
    belief = ranked[-1]
    existing = next((item for item in listener.beliefs
                     if item["subject"] == belief["subject"] and item["provenance"] == "told"
                     and item["source"] == speaker.id and item["value"] == belief["value"]), None)
    if existing is not None:
        return None
    label = belief["subject"].replace(":", " ")
    text = f"{speaker.name} told {listener.name} that {label} was {belief['value']}."
    sources = [belief["event_id"]] if type(belief.get("event_id")) is int else []
    event = sim.event(text, "report", [speaker.id, listener.id], source_ids=sources,
                      subject=belief["subject"])
    remember(listener, subject=belief["subject"], value=belief["value"], provenance="told",
             source=speaker.id, step=sim.step, day=sim.day,
             confidence=min(0.85, round(belief["confidence"] * 0.7, 2)), event_id=event["id"])
    return event


def validate_personal(record, personas, next_event_id):
    traits = record["traits"]
    if not isinstance(traits, dict) or set(traits) != set(TRAIT_NAMES):
        raise ValueError("Invalid personal traits")
    if any(type(value) not in (int, float) or not 0 <= float(value) <= 1 for value in traits.values()):
        raise ValueError("Invalid personal traits")
    beliefs = record["beliefs"]
    if not isinstance(beliefs, list) or len(beliefs) > 64:
        raise ValueError("Invalid beliefs")
    for belief in beliefs:
        if not isinstance(belief, dict):
            raise ValueError("Invalid beliefs")
        if (not isinstance(belief.get("subject"), str) or not belief["subject"]
                or belief.get("provenance") not in PROVENANCE
                or type(belief.get("confidence")) not in (int, float)
                or not 0 <= float(belief["confidence"]) <= 1
                or type(belief.get("step")) is not int or belief["step"] < 0
                or type(belief.get("day")) is not int or belief["day"] < 1):
            raise ValueError("Invalid beliefs")
        source = belief.get("source")
        if source is not None and source not in personas and source != "village":
            raise ValueError("Invalid belief source")
        event_id = belief.get("event_id")
        if event_id is not None and (type(event_id) is not int or event_id < 0 or event_id >= next_event_id):
            raise ValueError("Invalid belief evidence")
        if type(belief.get("value")) not in (int, float, str):
            raise ValueError("Invalid belief value")
