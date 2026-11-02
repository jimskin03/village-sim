"""Read-only session analytics over the complete, durable event history."""

from collections import Counter

from .events import shown_happiness

# Branches group the kinds a villager actually produces. Kinds that arrive
# later stay in the ledger and fall into Other rather than disappearing.
BRANCHES = (
    {"id": "work", "name": "Work", "mark": "⚒", "color": "#c4a36a",
     "kinds": ("work", "goal", "work_summary")},
    {"id": "talk", "name": "Conversations", "mark": "✎", "color": "#7faf9a",
     "kinds": ("conversation", "promise", "report")},
    {"id": "plans", "name": "Plans", "mark": "✶", "color": "#8aa0c8",
     "kinds": ("plan", "insight", "lesson", "reflection")},
    {"id": "world", "name": "World", "mark": "☾", "color": "#6f8fbf",
     "kinds": ("village", "weather", "festival", "supper", "survival", "collapse", "governance")},
    {"id": "visitors", "name": "Visitors", "mark": "◈", "color": "#c48b7a",
     "kinds": ("visitor", "visitor_speech", "visitor_influence", "visitor_work",
               "visitor_effect", "visitor_witness")},
    {"id": "life", "name": "Everyday life", "mark": "·", "color": "#b7a48a",
     "kinds": ("arrival", "activity", "trust")},
)


def session_analytics(sim, *, agent=None, kinds=(), search="", before=None, limit=40):
    """Page the full event ledger. Filters intersect; the cursor is an event id.

    `before` keeps events older than that id, so a page stays put when newer
    events arrive. Residents who have left remain in the session.
    """
    names = {person.id: person.name for person in sim.residents.values()}
    by_kind = Counter()
    participation = {aid: Counter() for aid in sim.residents}
    matched = []
    needle = search.casefold()
    kind_set = set(kinds) if kinds else None
    for event in sim.events:
        kind = event["kind"]
        by_kind[kind] += 1
        for aid in event["agents"]:
            participation.setdefault(aid, Counter())[kind] += 1
        if agent and agent not in event["agents"]:
            continue
        if kind_set is not None and kind not in kind_set:
            continue
        if needle:
            haystack = " ".join((
                event["text"], kind, *(names.get(aid, aid) for aid in event["agents"]),
            )).casefold()
            if needle not in haystack:
                continue
        matched.append(event)
    matched.reverse()
    if before is not None:
        matched = [event for event in matched if event["id"] < before]
    page = matched[:limit]
    known = {kind for branch in BRANCHES for kind in branch["kinds"]}
    branches = []
    for branch in BRANCHES:
        branches.append({**branch, "kinds": list(branch["kinds"]),
                         "count": sum(by_kind[kind] for kind in branch["kinds"])})
    other = [kind for kind in by_kind if kind not in known]
    if other:
        branches.append({"id": "other", "name": "Other", "mark": "+", "color": "#8d8a82",
                         "kinds": other, "count": sum(by_kind[kind] for kind in other)})
    minutes = sim.world.config["minutes_per_step"]
    return {
        "event_count": len(sim.events),
        "matching_events": len(matched) if before is None else _match_count(sim, agent, kind_set, needle, names),
        "next_before": page[-1]["id"] if len(matched) > len(page) else None,
        "by_kind": dict(by_kind),
        "events": [_public(event) for event in page],
        "residents": [_resident(sim, person, participation[person.id])
                      for person in sim.residents.values()],
        "branches": branches,
        "tasks_completed": sum(person.work_count for person in sim.residents.values()),
        "elapsed_minutes": sim.step * minutes,
        "history_complete": True,
    }


def _match_count(sim, agent, kind_set, needle, names):
    total = 0
    for event in sim.events:
        if agent and agent not in event["agents"]:
            continue
        if kind_set is not None and event["kind"] not in kind_set:
            continue
        if needle:
            haystack = " ".join((
                event["text"], event["kind"], *(names.get(aid, aid) for aid in event["agents"]),
            )).casefold()
            if needle not in haystack:
                continue
        total += 1
    return total


def _public(event):
    return {
        "id": event["id"],
        "step": event["step"],
        "day": event["day"],
        "time": event["time"],
        "text": event["text"],
        "kind": event["kind"],
        "agents": list(event["agents"]),
    }


def _resident(sim, person, counts):
    return {
        "id": person.id,
        "name": person.name,
        "role": person.role,
        "color": person.color,
        "activity": person.activity,
        "mood": person.mood,
        "work_count": person.work_count,
        "event_count": sum(counts.values()),
        "health": person.health,
        "happiness": shown_happiness(person, sim.step),
        "active": person.id in sim.agents,
        "by_kind": dict(counts),
    }
