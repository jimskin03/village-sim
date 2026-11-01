"""Replaceable rule-based decisions; external brains use the same Action contract."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    place: str
    activity: str
    speech: str | None = None
    result: str | None = None
    mood: str | None = None
    promise: str | None = None
    thought: str | None = None


def daily_plan(agent) -> list[dict]:
    craft = {
        "fisher": ("jetty", "fishing", "nets", "mending nets"),
        "gardener": ("garden", "tending the garden", "shore", "gathering shells"),
        "boatwright": ("shore", "checking the boats", "nets", "repairing tackle"),
    }[agent.role]
    return [
        {"hour": 0, "place": agent.home, "activity": "sleeping"},
        {"hour": 6, "place": craft[0], "activity": craft[1]},
        {"hour": 10, "place": craft[2], "activity": craft[3]},
        {"hour": 13, "place": "bench", "activity": "taking a quiet break"},
        {"hour": 15, "place": craft[0], "activity": craft[1]},
        {"hour": 18, "place": "fire", "activity": "sharing stories"},
        {"hour": 21, "place": agent.home, "activity": "winding down"},
        {"hour": 23, "place": agent.home, "activity": "sleeping"},
    ]


def decide(agent, observation: dict) -> Action:
    from . import survival
    if survival.collapsed(agent):
        return Action(agent.destination, "collapsed")
    if "collection_centers" in observation:
        action = survival.suggested_action(agent, observation)
        if action:
            return action
    if observation["weather"] == "storm":
        return Action(agent.home, "sheltering from the rain")
    if observation["festival"]:
        return Action("fire", "celebrating the lantern festival")
    hour = observation["minute"] // 60
    pantry = observation.get("pantry", {})
    if 6 <= hour < 18 and hour not in (13, 14):
        if agent.role in ("fisher", "boatwright") and pantry.get("net_condition") == "torn":
            return Action("nets", "mending the torn village net")
        if agent.role == "boatwright" and pantry.get("boat_condition") == "leaking":
            return Action("shore", "repairing the leaking village boat")
    item = next(p for p in reversed(agent.plan) if p["hour"] <= hour)
    work = next((g for g in agent.goals if g["id"] == "work"), None)
    if (6 <= hour < 13 or 15 <= hour < 18) and work and not work["completed"]:
        activity = {
            "fisher": "fishing for the village supper",
            "gardener": "gathering herbs for the village supper",
            "boatwright": "checking the village boats",
        }[agent.role]
        return Action(work["place"], activity)
    social = next((g for g in agent.goals if g["id"] == "social"), None)
    if 13 <= hour < 15 and social:
        friend = next(
            (v for v in observation.get("villagers", []) if v["id"] == social["partner"]),
            None,
        )
        if friend:
            return Action(social["place"], f"making time for {friend['name']}")
    return Action(item["place"], item["activity"])
