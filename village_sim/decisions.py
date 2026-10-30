"""Replaceable rule-based decisions; external brains use the same Action contract."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    place: str
    activity: str
    speech: str | None = None


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
    if observation["weather"] == "storm":
        return Action(agent.home, "sheltering from the rain")
    if observation["festival"]:
        return Action("fire", "celebrating the lantern festival")
    hour = observation["minute"] // 60
    item = next(p for p in reversed(agent.plan) if p["hour"] <= hour)
    return Action(item["place"], item["activity"])
