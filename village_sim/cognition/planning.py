"""Versioned proposals and legal actions; models never execute world changes."""

from __future__ import annotations

import copy

from ..decisions import Action

CONTRACT = "plan-v1"
MODES = ("rules", "hybrid")


def legal_actions(agent, observation: dict) -> list[dict]:
    from .. import survival
    suggested = observation["suggested_action"]
    routine = {"action": "routine", "location": suggested["place"], "target": None,
               "place": suggested["place"], "activity": suggested["activity"]}
    if survival.collapsed(agent):
        return []
    actions = [routine]
    for kind, places in (("drink", survival.DRINK_PLACES),
                         ("eat", (survival.FOOD_CENTER, "fire")),
                         ("collect_water", survival.SOURCES)):
        for place in places:
            actions.append({"action": kind, "location": place, "target": None,
                            "place": place, "activity": "collect water" if kind == "collect_water" else kind})
    if (observation["weather"] == "storm" or observation["festival"]
            or observation["minute"] // 60 < 6 or observation["minute"] // 60 >= 23):
        return actions
    work = {
        "fisher": [("jetty", "fishing"), ("shore", "fishing"), ("nets", "mending nets")],
        "gardener": [("garden", "tending the garden")],
        "boatwright": [("shore", "checking the boats"), ("nets", "repairing tackle")],
    }[agent.role]
    work = list(dict.fromkeys([*work, ("jetty", "fishing"), ("garden", "tending the garden"), ("nets", "mending nets")]))
    if 6 <= observation["minute"] // 60 < 18:
        for place, activity in work:
            actions.append({"action": "work", "location": place, "target": None,
                            "place": place, "activity": activity})
    for place in dict.fromkeys((agent.home, "bench", "fire")):
        actions.append({"action": "rest", "location": place, "target": None,
                        "place": place, "activity": "taking a quiet break"})
    for other in observation["villagers"]:
        actions.append({"action": "visit", "location": other["destination"], "target": other["id"],
                        "place": other["destination"], "activity": f"visiting {other['name']}"})
    return actions


def match_step(step: dict, actions: list[dict]) -> dict:
    if not isinstance(step, dict):
        raise ValueError("Plan steps must be objects")
    for action in actions:
        if all(step.get(key) == action[key] for key in ("action", "location", "target")):
            return action
    raise ValueError("Plan step is no longer a legal action")


def validate_proposal(proposal: dict, observation: dict) -> dict:
    """Normalize only explicit plan-v1 proposals; no legacy voice gains authority."""
    from .. import survival
    if not isinstance(proposal, dict) or proposal.get("contract") != CONTRACT:
        raise ValueError("Hybrid planning requires the plan-v1 contract")
    goal, steps, evidence = proposal.get("goal"), proposal.get("steps"), proposal.get("memory_ids", [])
    if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 200:
        raise ValueError("Plan goal must contain 1–200 characters")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
        raise ValueError("Plans need 1–8 steps")
    available = {memory["id"] for key in ("relevant_memories", "recent_memories")
                 for memory in observation.get(key, [])}
    available.update(memory["id"] for memory in observation["agent"].get("memory", []))
    if (not isinstance(evidence, list) or len(evidence) > 24
            or any(type(identifier) is not int or identifier not in available for identifier in evidence)):
        raise ValueError("Plan evidence must identify memories in the observation")
    normalized = []
    for step in steps:
        if isinstance(step, dict) and step.get("action") in ("drink", "eat", "collect_water"):
            survival.validate_activity(step.get("location"), step["action"].replace("_", " "))
        action = match_step(step, observation["allowed_actions"])
        duration, reason = step.get("duration_minutes"), step.get("reason", "")
        if type(duration) is not int or not 15 <= duration <= 180 or duration % 15:
            raise ValueError("Step duration must be 15–180 minutes in 15-minute increments")
        if not isinstance(reason, str) or len(reason) > 200:
            raise ValueError("Step reason must be at most 200 characters")
        normalized.append({key: action[key] for key in ("action", "location", "target")}
                          | {"duration_minutes": duration, "reason": reason.strip()})
    if sum(step["duration_minutes"] for step in normalized) > 1440:
        raise ValueError("A plan may last at most one simulated day")
    return {"contract": CONTRACT, "goal": goal.strip(), "steps": normalized,
            "memory_ids": list(dict.fromkeys(evidence))}


def start_plan(proposal: dict, *, revision: int, step: int) -> dict:
    return {**copy.deepcopy(proposal), "id": revision, "revision": revision,
            "created_step": step, "updated_step": step, "status": "active",
            "index": 0, "remaining": proposal["steps"][0]["duration_minutes"] // 15,
            "reason": None}


def action_for(plan: dict, observation: dict) -> Action:
    action = match_step(plan["steps"][plan["index"]], observation["allowed_actions"])
    return Action(action["place"], action["activity"], thought=plan["steps"][plan["index"]]["reason"] or None)
