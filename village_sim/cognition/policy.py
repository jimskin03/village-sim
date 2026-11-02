"""Local attempts, skill, and the estimate a resident carries into tomorrow.

One result nudges a shrunk average. A schedule changes only after repeated
evidence. The world, not this score, decides whether the attempt succeeds.
"""

from __future__ import annotations

from ..decisions import Action
from . import knowledge

ATTEMPT = "attempt-v1"
SKILLS = ("fish", "garden", "repair", "negotiate", "scout")
PRIOR_N = 4
PRIOR_SUCCESS = 0.55
SAMPLE = 4
FORBIDDEN = ("result", "success", "stock", "trust", "pantry", "health", "yield")


def initial_skills():
    return {name: {"level": 0.2, "practice": 0} for name in SKILLS}


def expected_success(agent, task, place) -> float:
    estimate = agent.estimates.get(f"{task}:{place}")
    successes = estimate["successes"] if estimate else 0
    count = estimate["n"] if estimate else 0
    return (PRIOR_N * PRIOR_SUCCESS + successes) / (PRIOR_N + count)


def _band(rate: float) -> str:
    if rate < 0.4:
        return "poor"
    if rate < 0.7:
        return "fair"
    return "good"


def _skill_name(task: str) -> str:
    return {"fish": "fish", "garden": "garden", "repair": "repair"}.get(task, "scout")


def informed(agent) -> bool:
    return any(item["n"] >= SAMPLE for item in agent.estimates.values())


def record_outcome(sim, agent, task, place, success, event_id=None):
    """Update the shrunk yield and, once the sample is large enough, a cited lesson."""
    key = f"{task}:{place}"
    estimate = agent.estimates.setdefault(key, {"n": 0, "successes": 0, "events": []})
    estimate["n"] += 1
    estimate["successes"] += 1 if success else 0
    if type(event_id) is int:
        estimate["events"] = [*estimate["events"], event_id][-8:]
    skill = agent.skills[_skill_name(task)]
    skill["practice"] += 1
    skill["level"] = round(min(1.0, 0.2 + skill["practice"] / 50), 2)
    if estimate["n"] < SAMPLE:
        return None
    rate = expected_success(agent, task, place)
    band = _band(rate)
    current = next((lesson for lesson in reversed(agent.lessons)
                    if lesson["conditions"].get("task") == task
                    and lesson["conditions"].get("place") == place
                    and not lesson.get("retired")), None)
    if current is not None and current["conditions"].get("band") == band:
        current["confidence"] = round(estimate["n"] / (estimate["n"] + PRIOR_N), 2)
        current["source_ids"] = list(estimate["events"])
        return None
    if current is not None:
        current["retired"] = True
    place_name = sim.world.places[place]["name"]
    text = (f"When I {task} at {place_name}, {estimate['successes']} of my last "
            f"{estimate['n']} attempts have worked.")
    sources = [item for item in estimate["events"] if type(item) is int]
    lesson = {"text": text, "conditions": {"task": task, "place": place, "band": band},
              "source_ids": sources, "confidence": round(estimate["n"] / (estimate["n"] + PRIOR_N), 2),
              "retired": False}
    agent.lessons.append(lesson)
    if len(agent.lessons) > 32:
        del agent.lessons[:-32]
    if sources:
        sim.event(text, "lesson", [agent.id], source_ids=sources, significant=True)
    return lesson


def task_for(place, role):
    from .. import work
    if place in work.TASKS["garden"]["places"]:
        return "garden"
    if place == "nets" or (place == "shore" and role == "boatwright"):
        return "repair"
    if place == "well":
        return "repair"
    if place in ("jetty", "shore"):
        return "fish"
    return None


def skill_level(agent, place) -> float:
    task = task_for(place, agent.role)
    if task is None:
        return 0.0
    return float(agent.skills[_skill_name(task)]["level"])


def _candidates(agent):
    """Compare places for this craft. A sure repair does not replace the supper."""
    from .. import work
    if agent.role == "gardener":
        return [("garden", place) for place in work.TASKS["garden"]["places"]]
    if agent.role == "boatwright":
        return [("repair", "shore"), ("repair", "nets")]
    return [("fish", "jetty"), ("fish", "shore")]


def _score(agent, task, place, distance):
    expected = expected_success(agent, task, place)
    level = agent.skills[_skill_name(task)]["level"]
    lesson = next((item for item in reversed(agent.lessons)
                   if not item.get("retired") and item["conditions"].get("task") == task
                   and item["conditions"].get("place") == place), None)
    tilt = 0.0
    if lesson is not None:
        tilt = {"poor": -0.2, "fair": 0.0, "good": 0.1}[lesson["conditions"]["band"]]
    return 2 * expected + 0.3 * level + tilt - 0.02 * distance


def preferred_work(sim, agent, current_place):
    """Return a new workplace only after repeated evidence beats the current one."""
    if not informed(agent):
        return None
    origin = sim.world.places[current_place]

    def distance(place):
        if place == current_place:
            return 0
        other = sim.world.places[place]
        return abs(origin["x"] - other["x"]) + abs(origin["y"] - other["y"])

    current_task = task_for(current_place, agent.role) or "fish"
    best_task, best_place = max(
        _candidates(agent),
        key=lambda item: _score(agent, item[0], item[1], distance(item[1])),
    )
    if best_place == current_place:
        return None
    if _score(agent, best_task, best_place, distance(best_place)) < _score(
            agent, current_task, current_place, 0) + 0.15:
        return None
    names = {
        "fish": f"Try {sim.world.places[best_place]['name']} for fish after the last attempts",
        "garden": f"Tend {sim.world.places[best_place]['name']} after the last attempts",
        "repair": f"Work at {sim.world.places[best_place]['name']} after the last attempts",
    }
    return {"place": best_place, "title": names[best_task]}


def _activity(task, place, role):
    if task == "fish":
        return "fishing for the village supper"
    if task == "garden":
        return "gathering herbs for the village supper"
    if place == "well":
        return "maintaining the well"
    if place == "nets":
        return "mending the torn village net"
    if role == "boatwright":
        return "repairing the leaking village boat"
    return "checking the village boats"


def _routine(agent, observation):
    hour = observation["minute"] // 60
    item = next(entry for entry in reversed(agent.plan) if entry["hour"] <= hour)
    return Action(item["place"], item["activity"])


def choose(sim, agent, observation) -> Action:
    """Score legal work when there is evidence. Otherwise keep the daily craft."""
    from .. import survival
    if survival.collapsed(agent):
        return Action(agent.destination, "collapsed")
    if observation["weather"] == "storm":
        return Action(agent.home, "sheltering from the rain")
    if observation["festival"]:
        return Action("fire", "celebrating the lantern festival")
    hour = observation["minute"] // 60
    if 13 <= hour < 15:
        social = next((goal for goal in agent.goals if goal["id"] == "social" and not goal["completed"]), None)
        friend = next((person for person in observation.get("villagers", [])
                       if social and person["id"] == social["partner"]), None)
        if friend:
            return Action(social["place"], f"making time for {friend['name']}")
    if not (6 <= hour < 18):
        return _routine(agent, observation)
    from .. import governance
    if governance.laboring(sim, agent):
        return Action("well", "maintaining the well")
    if agent.role == "fisher" and sim.pantry["net_condition"] == "torn":
        return Action("nets", "mending the torn village net")
    if agent.role == "boatwright" and sim.pantry["boat_condition"] == "leaking":
        return Action("shore", "repairing the leaking village boat")
    work = next((goal for goal in agent.goals if goal["id"] == "work"), None)
    if work is None or not informed(agent):
        if work and not work["completed"]:
            return Action(work["place"], {
                "fisher": "fishing for the village supper",
                "gardener": "gathering herbs for the village supper",
                "boatwright": "checking the village boats",
            }[agent.role])
        return _routine(agent, observation)
    origin = (agent.x, agent.y)

    def distance(place):
        point = sim.world.places[place]
        return abs(origin[0] - point["x"]) + abs(origin[1] - point["y"])

    task, place = max(_candidates(agent), key=lambda item: _score(agent, item[0], item[1], distance(item[1])))
    aquifer = knowledge.acting_belief(agent, "water:well")
    if aquifer is not None and type(aquifer["value"]) in (int, float) and aquifer["value"] <= 1:
        if _score(agent, "repair", "well", distance("well")) >= _score(agent, task, place, distance(place)):
            task, place = "repair", "well"
    return Action(place, _activity(task, place, agent.role))


def validate_attempt(data, observation) -> Action:
    """Accept a legal attempt. A result, stock edit, or trust edit is refused."""
    if not isinstance(data, dict) or data.get("contract") != ATTEMPT:
        raise ValueError("Living attempts use the attempt-v1 contract")
    if any(key in data and data[key] is not None for key in FORBIDDEN):
        raise ValueError("An attempt cannot choose its result")
    place, activity = data.get("place"), data.get("activity")
    match = next((item for item in observation.get("allowed_actions", [])
                  if item.get("place") == place and item.get("activity") == activity), None)
    if match is None:
        raise ValueError("Choose a current allowed action")
    goal = data.get("goal")
    if goal is not None and (not isinstance(goal, str) or not goal.strip() or len(goal) > 200):
        raise ValueError("Goal must be text, at most 200 characters")
    thought = goal.strip()[:200] if isinstance(goal, str) else None
    return Action(place, activity, thought=thought)


def validate_personal(record, next_event_id):
    skills = record["skills"]
    if not isinstance(skills, dict) or set(skills) != set(SKILLS):
        raise ValueError("Invalid skills")
    for skill in skills.values():
        if (not isinstance(skill, dict) or set(skill) != {"level", "practice"}
                or type(skill["practice"]) is not int or skill["practice"] < 0
                or type(skill["level"]) not in (int, float) or not 0 <= float(skill["level"]) <= 1):
            raise ValueError("Invalid skills")
    estimates = record["estimates"]
    if not isinstance(estimates, dict) or len(estimates) > 32:
        raise ValueError("Invalid yield estimates")
    for key, estimate in estimates.items():
        task, _, place = key.partition(":")
        if task not in ("fish", "garden", "repair") or not place:
            raise ValueError("Invalid yield estimates")
        if (not isinstance(estimate, dict) or set(estimate) != {"n", "successes", "events"}
                or type(estimate["n"]) is not int or estimate["n"] < 1
                or type(estimate["successes"]) is not int
                or not 0 <= estimate["successes"] <= estimate["n"]
                or not isinstance(estimate["events"], list)
                or any(type(item) is not int or item < 0 or item >= next_event_id
                       for item in estimate["events"])):
            raise ValueError("Invalid yield estimates")
    lessons = record["lessons"]
    if not isinstance(lessons, list) or len(lessons) > 32:
        raise ValueError("Invalid lessons")
    for lesson in lessons:
        conditions = lesson.get("conditions") if isinstance(lesson, dict) else None
        if (not isinstance(lesson, dict) or not isinstance(lesson.get("text"), str)
                or not isinstance(conditions, dict)
                or conditions.get("task") not in ("fish", "garden", "repair")
                or not isinstance(conditions.get("place"), str)
                or conditions.get("band") not in ("poor", "fair", "good")
                or not isinstance(lesson.get("source_ids"), list)
                or any(type(item) is not int or item < 0 or item >= next_event_id
                       for item in lesson["source_ids"])
                or type(lesson.get("confidence")) not in (int, float)
                or not 0 <= float(lesson["confidence"]) <= 1
                or type(lesson.get("retired")) is not bool):
            raise ValueError("Invalid lessons")
