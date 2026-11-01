"""Versioned, secret-free session checkpoints and atomic local saves."""

from __future__ import annotations

from dataclasses import asdict, fields
import copy
import json
import os
from pathlib import Path
import random
import tempfile

from .decisions import Action
from .cognition.memory import make_memory, validate_memory
from .simulation import Agent, PERSONAS, Simulation

SCHEMA_VERSION = 2
_RUNTIME = {"world", "rng", "decision", "agents", "residents", "pending"}


def export_session(sim: Simulation) -> dict:
    residents = {aid: asdict(agent) for aid, agent in sim.residents.items()}
    for agent in residents.values():
        agent["provider"] = None
        agent["provider_until"] = 0
    data = {
        "schema_version": SCHEMA_VERSION,
        "world": sim.world.config,
        "state": {key: value for key, value in vars(sim).items() if key not in _RUNTIME},
        "residents": residents,
        "active_agents": list(sim.agents),
        "pending": {aid: asdict(action) for aid, action in sim.pending.items()},
        "rng_state": sim.rng.getstate(),
    }
    # Detach mutable history and normalize RNG tuples for JSON round trips.
    return json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _events(items):
    return isinstance(items, list) and all(
        isinstance(item, dict)
        and all(isinstance(item.get(key), str) for key in ("text", "time", "kind"))
        and all(_integer(item.get(key)) for key in ("id", "step", "day"))
        and isinstance(item.get("agents"), list)
        and all(isinstance(aid, str) for aid in item["agents"])
        for item in items
    )


def _json_tree(value):
    if value is None or type(value) in (bool, int, str):
        return True
    if isinstance(value, list):
        return all(_json_tree(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_tree(item) for key, item in value.items())
    return False


def restore_session(data: dict) -> Simulation:
    """Build a complete candidate before the caller replaces its live session."""
    try:
        return _restore(_migrate(data))
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError):
        raise ValueError("That file does not contain a valid resumable village session") from None


def _migrate(data):
    """Upgrade v1 records without mutating the imported checkpoint."""
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
        raise ValueError("Unsupported session version")
    if data["schema_version"] != 1:
        return data
    data = copy.deepcopy(data)
    seen = data["state"].pop("seen_texts")
    if not isinstance(seen, dict) or not all(isinstance(key, str) and type(value) is bool
                                           for key, value in seen.items()):
        raise ValueError("Invalid legacy memory index")
    start = data["world"]["start_minute"]
    interval = data["world"]["minutes_per_step"]

    def upgrade_event(record):
        return {**record, "timestamp": start + record["step"] * interval,
                "source_ids": list(record.get("source_ids", []))}

    for key in ("events", "reflections"):
        if not _events(data["state"][key]):
            raise ValueError("Invalid legacy journal")
        data["state"][key] = [upgrade_event(record) for record in data["state"][key]]
    for resident in data["residents"].values():
        if not _events(resident["memory"]):
            raise ValueError("Invalid legacy memories")
        resident["memory"] = [make_memory(upgrade_event(record)) for record in resident["memory"]]
    data["schema_version"] = SCHEMA_VERSION
    return data


def _restore(data):
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported session version")
    state = data["state"]
    if not isinstance(state, dict) or not _integer(state.get("seed"), -(2**63)):
        raise ValueError("Invalid world state")
    candidate = Simulation(1, state["seed"])
    expected = set(vars(candidate)) - _RUNTIME
    if set(state) != expected or not _json_tree(state):
        raise ValueError("Incomplete world state")
    if data.get("world") != candidate.world.config:
        raise ValueError("Unsupported world geometry")
    for key in ("step", "weather_until", "festival_until"):
        if not _integer(state[key]):
            raise ValueError("Invalid clock")
    if state["weather"] not in ("clear", "storm") or not _events(state["events"]):
        raise ValueError("Invalid weather or journal")
    # Reject container/type changes for every state field, including future additions.
    for key, value in state.items():
        original = getattr(candidate, key)
        if original is not None and type(value) is not type(original):
            raise ValueError("Invalid state type")
    pantry = state.get("pantry")
    if pantry is not None:
        if set(pantry) != set(candidate.pantry):
            raise ValueError("Incomplete pantry")
        for key, original in candidate.pantry.items():
            value = pantry[key]
            if type(value) is not type(original):
                raise ValueError("Invalid pantry")
            if type(value) is int and value < 0:
                raise ValueError("Invalid pantry count")
        if pantry["boat_condition"] not in ("sound", "leaking") or pantry["net_condition"] not in ("sound", "torn"):
            raise ValueError("Invalid condition")
    if "reflections" in state and not _events(state["reflections"]):
        raise ValueError("Invalid reflections")
    if "next_event_id" in state and not _integer(state["next_event_id"]):
        raise ValueError("Invalid journal cursor")
    if not all(isinstance(value, str) and key in ("fisher", "gardener", "boatwright")
               for key, value in state.get("last_miss", {}).items()):
        raise ValueError("Invalid remembered shortage")
    known_ids = {persona[0] for persona in PERSONAS}
    result_ids = {"silver_fish", "mackerel", "empty_net", "torn_mesh", "first_basil",
                  "mint_bundle", "wilted_seedlings", "sealed_hull", "sound_boat",
                  "leaking_boat", "mended_mesh", "knotted_rope"}
    for key, row in state.get("daily_work", {}).items():
        if not (isinstance(row, dict) and _integer(row.get("day"), 1)
                and row.get("agent") in known_ids and row.get("place") in candidate.world.places
                and key == f"{row['day']}:{row['agent']}:{row['place']}"
                and isinstance(row.get("results"), dict)
                and all(result in result_ids and _integer(count, 1) for result, count in row["results"].items())):
            raise ValueError("Invalid work summary")
    supper_days = set()
    for supper in state.get("suppers", []):
        if not (isinstance(supper, dict) and _integer(supper.get("day"), 1)
                and supper["day"] not in supper_days
                and all(_integer(supper.get(key)) for key in ("fish", "herbs"))
                and isinstance(supper.get("missing"), dict) and set(supper["missing"]) == {"fish", "herbs"}
                and all(_integer(count) for count in supper["missing"].values())
                and isinstance(supper.get("text"), str)
                and isinstance(supper.get("empty_handed"), list)
                and all(name in {persona[1] for persona in PERSONAS} for name in supper["empty_handed"])):
            raise ValueError("Invalid supper ledger")
        supper_days.add(supper["day"])
    visitor = state.get("visitor")
    if visitor is not None and not (isinstance(visitor, dict)
            and visitor.get("id") == "visitor" and isinstance(visitor.get("name"), str)
            and visitor.get("place") == "guest_home"
            and (visitor.get("x"), visitor.get("y")) == candidate.world.point("guest_home")):
        raise ValueError("Invalid visitor")
    for item in state["events"] + state.get("reflections", []):
        if item["id"] >= state.get("next_event_id", len(state["events"])):
            raise ValueError("Invalid event sequence")
    for reflection in state.get("reflections", []):
        if reflection["kind"] != "reflection" or not _integer(reflection.get("reflected_day"), 1):
            raise ValueError("Invalid daily reflection")
    residents = data["residents"]
    active = data["active_agents"]
    if not isinstance(residents, dict) or not isinstance(active, list):
        raise ValueError("Invalid residents")
    personas = {persona[0]: persona for persona in PERSONAS}
    if not 1 <= len(active) <= len(PERSONAS) or len(set(active)) != len(active):
        raise ValueError("Invalid population")
    if not set(active) <= set(residents) <= set(personas):
        raise ValueError("Unknown resident")
    restored = {}
    agent_fields = {field.name for field in fields(Agent)}
    for aid, record in residents.items():
        if not isinstance(record, dict) or set(record) != agent_fields:
            raise ValueError("Invalid villager")
        if any(record[key] != value for key, value in zip(
            ("id", "name", "role", "home", "color", "persona"), personas[aid]
        )):
            raise ValueError("Invalid identity")
        if not all(type(record[key]) is int for key in ("x", "y")):
            raise ValueError("Invalid position")
        if (record["x"], record["y"]) not in candidate.world.walkable:
            raise ValueError("Invalid position")
        if record["destination"] not in candidate.world.places or not isinstance(record["activity"], str):
            raise ValueError("Invalid activity")
        if not _events(record["memory"]):
            raise ValueError("Invalid memories")
        memory_ids = set()
        for memory in record["memory"]:
            validate_memory(memory)
            if (memory["id"] in memory_ids or memory["id"] >= state["next_event_id"]
                    or memory["step"] > state["step"]
                    or memory["timestamp"] != candidate.world.config["start_minute"]
                        + memory["step"] * candidate.world.config["minutes_per_step"]
                    or not set(memory["agents"]) <= set(personas)
                    or aid not in memory["agents"]
                    or any(source >= memory["id"] for source in memory["source_ids"])):
                raise ValueError("Invalid memory history")
            memory_ids.add(memory["id"])
        relationships = record["relationships"]
        if not isinstance(relationships, dict) or not all(
            partner in personas and isinstance(fact, str)
            for partner, fact in relationships.items()
        ):
            raise ValueError("Invalid shared facts")
        if not isinstance(record["goals"], list) or not all(
            isinstance(goal, dict)
            and goal.get("id") in ("work", "social")
            and isinstance(goal.get("title"), str)
            and _integer(goal.get("day"), 1)
            and _integer(goal.get("target"), 1)
            and _integer(goal.get("progress"))
            and goal["progress"] <= goal["target"]
            and type(goal.get("completed")) is bool
            and goal.get("place") in candidate.world.places
            and (goal.get("partner") is None or goal["partner"] in personas)
            for goal in record["goals"]
        ):
            raise ValueError("Invalid goals")
        goal_ids = [goal["id"] for goal in record["goals"]]
        if "work" not in goal_ids or len(set(goal_ids)) != len(goal_ids):
            raise ValueError("Missing or duplicate goals")
        if not isinstance(record["plan"], list) or not record["plan"] or not all(
            isinstance(item, dict) and _integer(item.get("hour")) and item["hour"] < 24
            and item.get("place") in candidate.world.places and isinstance(item.get("activity"), str)
            for item in record["plan"]
        ):
            raise ValueError("Invalid plan")
        hours = [item["hour"] for item in record["plan"]]
        if hours[0] != 0 or hours != sorted(set(hours)):
            raise ValueError("Invalid plan order")
        for key in ("speech", "thought", "result"):
            if key in record and record[key] is not None and not isinstance(record[key], str):
                raise ValueError("Invalid expression")
        if "mood" in record and record["mood"] not in ("content", "hopeful", "worried", "tired"):
            raise ValueError("Invalid mood")
        for key in ("last_chat", "last_work", "work_count"):
            if not _integer(record[key], -100 if key.startswith("last_") else 0):
                raise ValueError("Invalid work state")
        record = {**record, "provider": None, "provider_until": 0}
        restored[aid] = Agent(**record)
    rng = random.Random()
    rng_state = data["rng_state"]
    if not isinstance(rng_state, list) or len(rng_state) != 3:
        raise ValueError("Invalid random state")
    rng.setstate((rng_state[0], tuple(rng_state[1]), rng_state[2]))
    candidate.__dict__.update(state)
    candidate.residents = restored
    candidate.agents = {aid: restored[aid] for aid in active}
    candidate.rng = rng
    if any(row["agent"] not in restored for row in state.get("daily_work", {}).values()):
        raise ValueError("Missing work history resident")
    candidate.pending = {}
    pending = data["pending"]
    if not isinstance(pending, dict) or not set(pending) <= set(active):
        raise ValueError("Invalid pending decisions")
    for aid, action in pending.items():
        if not isinstance(action, dict) or set(action) != {field.name for field in fields(Action)}:
            raise ValueError("Invalid pending decision")
        candidate.submit(aid, {**action, "step": candidate.step})
    # Exercise observation and snapshot paths before admitting the candidate.
    for aid in active:
        candidate.observe(aid)
    candidate.snapshot()
    return candidate


def write_session(path: str | Path, data: dict):
    """Replace in one filesystem operation; a failed write retains the old save."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_session(path: str | Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError(f"Cannot read the saved village at {path}; keep it for recovery or choose another --state-file") from None
