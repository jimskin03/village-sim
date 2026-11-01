"""Private reflections and trust learned from kept or missed meetings.

Insights are interpretations. They can cite only memories the resident already
holds, and they never rewrite biography, episodes, or trust scores. Trust moves
by one tenth when a meeting is confirmed kept or missed, and it stays in [-1, 1].
"""

from __future__ import annotations

import copy

CONTRACT = "reflection-v1"
HOUR = 4
TRIGGER = 5.0


def _social():
    from . import conversations
    return conversations


def tenths(value) -> int:
    return int(round(float(value) * 10))


def bump_trust(current, delta):
    """Move by exactly one tenth and clamp to [-1, 1]."""
    step = 1 if delta > 0 else -1
    updated = max(-10, min(10, tenths(current) + step))
    return updated / 10


def _blocked(sim, agent):
    return agent.last_reflection_step >= 0 and sim.step - agent.last_reflection_step < HOUR


def _pending(sim, aid):
    agent = sim.residents[aid]
    return [memory for memory in agent.memory if memory["id"] >= agent.reflection_cursor]


def due(sim, aid):
    if sim.cognition_mode != "hybrid" or aid not in sim.agents:
        return False
    agent = sim.residents[aid]
    pending = _pending(sim, aid)
    if _blocked(sim, agent) or not pending:
        return False
    if agent.reflection_reason == "day_end":
        return True
    return sum(memory["importance"] for memory in pending) >= TRIGGER


def _cited_people(records):
    people = set()
    for memory in records:
        people.update(memory.get("agents") or [])
        partner = memory.get("partner")
        if isinstance(partner, str):
            people.add(partner)
    return people


def _insight_supported(sim, agent, memory):
    helper = memory.get("preferred_helper")
    sources = memory.get("source_ids") or []
    if not isinstance(helper, str) or not sources:
        return False
    found = []
    for source in sources:
        record = sim.memory_store(agent.id).get(source)
        if record is None:
            return False
        found.append(record)
    return helper in _cited_people(found)


def choose_helper(sim, agent, task):
    """Prefer a grounded insight, then the highest trust, for this task."""
    crafts = _social().CRAFTS
    others = [other for other in sim.agents.values() if other.id != agent.id]
    if task in crafts:
        others = [other for other in others if other.role in crafts[task]]
    elif task != "talk":
        return None
    if not others:
        return None
    allowed = {other.id: other for other in others}
    for memory in reversed(agent.memory):
        if memory.get("kind") != "insight" or memory.get("task") not in (None, task):
            continue
        helper = memory.get("preferred_helper")
        if helper in allowed and _insight_supported(sim, agent, memory):
            return allowed[helper]

    def rank(other):
        return (
            agent.trust.get(other.id, 0.0),
            1 if other.id in agent.relationships else 0,
            -list(sim.residents).index(other.id),
        )

    return max(others, key=rank)


def _distinguished(sim, agent, task, helper):
    if any(memory.get("kind") == "insight" and memory.get("preferred_helper") == helper.id
           and memory.get("task") in (None, task) and _insight_supported(sim, agent, memory)
           for memory in agent.memory):
        return True
    crafts = _social().CRAFTS
    others = [other for other in sim.agents.values() if other.id != agent.id
              and (task not in crafts or other.role in crafts[task])]
    scores = [agent.trust.get(other.id, 0.0) for other in others]
    mine = agent.trust.get(helper.id, 0.0)
    return bool(scores) and mine == max(scores) and max(scores) > min(scores)


def context(sim, aid):
    if not due(sim, aid):
        return None
    from .memory import memory_view
    agent = sim.agents[aid]
    pending = _pending(sim, aid)
    ranked = sorted(pending, key=lambda memory: (memory["importance"], memory["id"]), reverse=True)[:24]
    evidence = memory_view(ranked, limit=24, characters=6000)
    return {
        "contract": CONTRACT,
        "step": sim.step,
        "speaker": aid,
        "persona": agent.persona,
        "trust": {partner: agent.trust[partner] for partner in sorted(agent.trust)},
        "goals": copy.deepcopy(agent.goals),
        "evidence": evidence,
        "tasks": list(_social().TASKS),
    }


def validate_insights(data, request):
    """Accept at most three interpretations grounded in the supplied evidence."""
    if not isinstance(data, dict) or data.get("contract") != CONTRACT or set(data) - {"step"} != {"contract", "insights"}:
        raise ValueError("Invalid reflection")
    insights = data.get("insights")
    evidence = {memory["id"]: memory for memory in request.get("evidence", [])
                if isinstance(memory, dict) and type(memory.get("id")) is int}
    tasks = set(request.get("tasks") or _social().TASKS)
    if not isinstance(insights, list) or len(insights) > 3:
        raise ValueError("A reflection contains at most three insights")
    normalized = []
    for item in insights:
        if not isinstance(item, dict):
            raise ValueError("Invalid insight")
        text = item.get("text")
        sources = item.get("source_ids")
        participants = item.get("participants")
        helper = item.get("preferred_helper")
        task = item.get("task")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 240:
            raise ValueError("Insight text must contain 1–240 characters")
        if (not isinstance(sources, list) or not 1 <= len(sources) <= 16
                or any(type(source) is not int or source not in evidence for source in sources)):
            raise ValueError("Insight evidence is missing or unsupported")
        cited = _cited_people(evidence[source] for source in sources)
        if (not isinstance(participants, list) or len(participants) > 6
                or any(not isinstance(person, str) or person not in cited for person in participants)):
            raise ValueError("Insight participants are not in the cited evidence")
        if helper is None and task is None:
            pass
        elif (not isinstance(helper, str) or helper == request["speaker"] or helper not in participants
              or task not in tasks):
            raise ValueError("A preferred helper must be a cited participant and a real task")
        else:
            pass
        normalized.append({
            "text": text.strip(),
            "source_ids": list(dict.fromkeys(sources)),
            "participants": list(dict.fromkeys(participants)),
            "preferred_helper": helper,
            "task": task,
        })
    return {"contract": CONTRACT, "insights": normalized}


def _store(sim, aid, insights):
    agent = sim.residents[aid]
    # Close the current evidence window first. New insights count next time.
    agent.reflection_cursor = sim.next_event_id
    agent.last_reflection_step = sim.step
    agent.reflection_reason = None
    for insight in insights:
        details = {}
        if insight["participants"]:
            details["about"] = insight["participants"]
        if insight["preferred_helper"] is not None:
            details["preferred_helper"] = insight["preferred_helper"]
            details["task"] = insight["task"]
        sim.event(insight["text"], "insight", [aid], source_ids=insight["source_ids"], **details)


def apply_turn(sim, aid, data):
    if aid not in sim.agents:
        raise ValueError("Unknown villager")
    if type(data.get("step")) is not int or data["step"] != sim.step:
        raise ValueError("Reflection is stale; fetch a fresh observation")
    request = context(sim, aid)
    if request is None:
        raise ValueError("Reflection is not due")
    _store(sim, aid, validate_insights(data, request)["insights"])


def _summary(evidence):
    record = max(evidence, key=lambda memory: (memory["importance"], memory["id"]))
    text = f"I will remember this: {record['text']}".strip()
    return {
        "text": text[:240].strip() or "I will remember what I just experienced.",
        "source_ids": [record["id"]],
        "participants": [],
        "preferred_helper": None,
        "task": None,
    }


def _helper_insight(sim, aid, evidence):
    agent = sim.residents[aid]
    cited = {}
    for memory in evidence:
        people = [person for person in _cited_people([memory]) if person != aid and person in sim.agents]
        for person in people:
            current = cited.get(person)
            rank = (
                memory.get("task") in _social().TASKS,
                memory.get("kind") == "trust",
                memory.get("kind") == "commitment",
                memory["id"],
            )
            if current is None or rank >= current[0]:
                cited[person] = (rank, memory)
    if len(cited) < 2:
        return None
    ranked = sorted(cited, key=lambda person: (agent.trust.get(person, 0.0), cited[person][1]["id"]))
    low, high = ranked[0], ranked[-1]
    low_score, high_score = agent.trust.get(low, 0.0), agent.trust.get(high, 0.0)
    if not (high_score > low_score and high_score >= 0 and low_score < 0):
        return None
    memory = cited[high][1]
    task = memory.get("task")
    if task not in _social().TASKS or high not in _cited_people([memory]):
        return None
    return {
        "text": (f"{sim.residents[low].name} has broken plans with me, so I would ask "
                 f"{sim.residents[high].name} next time.")[:240],
        "source_ids": [memory["id"]],
        "participants": [high],
        "preferred_helper": high,
        "task": task,
    }


def fallback(sim, aid):
    request = context(sim, aid)
    if request is None:
        return
    insights = [_summary(request["evidence"])]
    helper = _helper_insight(sim, aid, request["evidence"])
    if helper is not None:
        insights.append(helper)
    apply_turn(sim, aid, {"contract": CONTRACT, "step": sim.step, "insights": insights})


def consider(sim, *, day_end=False):
    if sim.cognition_mode != "hybrid":
        return
    for aid in list(sim.agents):
        agent = sim.agents[aid]
        if day_end and not _blocked(sim, agent) and _pending(sim, aid):
            agent.reflection_reason = "day_end"
        if not due(sim, aid) or (agent.provider and sim.step < agent.provider_until):
            continue
        fallback(sim, aid)


def _open_task(agent):
    tasks = _social().TASKS
    for memory in reversed(agent.memory):
        if memory.get("kind") in ("trust", "commitment", "insight") and memory.get("task") in tasks:
            return memory["task"]
    return "talk"


def adopt_help_plans(sim):
    """When no model is connected, visit the person trust or an insight prefers."""
    if sim.cognition_mode != "hybrid":
        return
    social = _social()
    for aid in list(sim.agents):
        agent = sim.agents[aid]
        reason = agent.replan_reason or ""
        if reason not in ("insight", "trust", "commitment_missed", "commitment_completed"):
            continue
        if agent.provider and sim.step < agent.provider_until:
            continue
        if social.active(sim, aid) or social.meeting_action(sim, aid):
            continue
        task = _open_task(agent)
        helper = choose_helper(sim, agent, task)
        if helper is None or not _distinguished(sim, agent, task, helper):
            continue
        try:
            observation = sim.observe(aid)
            visit = next((action for action in observation["allowed_actions"]
                          if action["action"] == "visit" and action["target"] == helper.id), None)
            if visit is None:
                continue
            evidence = [memory["id"] for memory in reversed(agent.memory)
                        if memory.get("kind") in ("trust", "insight", "commitment", "promise")][:8]
            sim.submit(aid, {
                "contract": "plan-v1",
                "goal": f"Ask {helper.name} for help with {task.replace('_', ' ')}"[:200],
                "steps": [{
                    "action": "visit", "location": visit["location"], "target": helper.id,
                    "duration_minutes": 45,
                    "reason": f"I would rather ask {helper.name}.",
                }],
                "memory_ids": evidence,
                "step": sim.step,
                "revision": agent.cognitive_revision,
            })
        except (ValueError, KeyError, TypeError):
            continue


def adjust_trust(sim, commitment, outcome):
    status = commitment["status"]
    if status not in ("completed", "missed"):
        return
    people = commitment["terms"]["participants"]
    task = commitment["terms"]["task"]
    after_delta = 0.1 if status == "completed" else -0.1
    for aid in people:
        other = next(person for person in people if person != aid)
        agent = sim.residents[aid]
        before = agent.trust.get(other, 0.0)
        updated = bump_trust(before, after_delta)
        if updated == before:
            continue
        agent.trust[other] = updated
        direction = "rose" if after_delta > 0 else "fell"
        sim.event(
            f"{agent.name}'s trust in {sim.residents[other].name} {direction} to {updated:.1f} "
            f"after a {status} meeting.",
            "trust", [aid], source_ids=[outcome["id"]], partner=other, task=task,
        )
