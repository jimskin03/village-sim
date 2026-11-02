"""Operator events. Sizes and durations live in EVENT_EFFECTS.

A step is 15 minutes. Losses apply once, when the operator starts the event.
Boosts last for `duration` steps and then expire.

Passing storm: each present villager loses health and food, and the shared
stores lose the configured supplies. A store runs out when that loss meets
what is left. The net still tears and outdoor work still stops.

Lantern festival: happiness rises at once, and an extra boost lasts for the
festival. The supply gift and the guest still happen, and work still stops.

Quiet evening: rest. Standing villagers regain a little health immediately
and again each step until the duration ends, and their mood settles to
content. Trust is unchanged. It records kept and missed meetings, not a mood.

Pirate attack: a boat comes to the jetty. The nearest standing villagers, up
to `victims`, are knocked out where they stand. They use the survival
collapse and recover at the survival restart health, and they stay down for
`duration` steps. The crew takes the configured supplies. Nobody dies, and
there is no fight or preparation step. Visitors are not targets. They see
the raid and cannot talk to someone who is down.

Asking for a storm that is already blowing applies the loss again. Asking for
a festival or a pirate attack that is already running does nothing. A quiet
evening ends the storm and the festival. It does not send the pirate boat away.
"""

import math

from . import survival

EVENT_EFFECTS = {
    "storm": {
        "duration": 12,
        "health": -8,
        "food": -12,
        "supplies": {"fish": 1, "herbs": 1, "water": 2},
    },
    "festival": {
        "duration": 16,
        "happiness": 20,
        "happiness_boost": 15,
        "mood": "hopeful",
    },
    "quiet": {
        "duration": 8,
        "health": 6,
        "health_per_step": 1,
        "mood": "content",
    },
    "pirate": {
        "duration": 4,
        "victims": 2,
        "supplies": {"fish": 2, "herbs": 1, "water": 2},
    },
}

_SUPPLY_LABELS = {
    "fish": ("fish", "fish"),
    "herbs": ("herb bundle", "herb bundles"),
    "water": ("water", "water"),
}


def shown_happiness(agent, step):
    bonus = agent.happiness_bonus if step < agent.happiness_bonus_until else 0
    return survival.clamp(agent.happiness + bonus)


def _phrase(taken):
    parts = []
    for key in ("fish", "herbs", "water"):
        count = taken.get(key, 0)
        if not count:
            continue
        one, many = _SUPPLY_LABELS[key]
        parts.append(f"{count} {one if count == 1 else many}")
    if not parts:
        return "nothing was left in the stores"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _take(sim, amounts):
    taken = {}
    for key, amount in amounts.items():
        got = min(amount, sim.pantry[key])
        sim.pantry[key] -= got
        taken[key] = got
    return taken


def _knock_out_if_spent(sim):
    steps = math.ceil(survival.COLLAPSE_MINUTES / sim.world.config["minutes_per_step"])
    for agent in sim.agents.values():
        if agent.health == 0 and not survival.collapsed(agent):
            survival.knock_out(sim, agent, steps)


def inject(sim, kind):
    if kind == "storm":
        effect = EVENT_EFFECTS["storm"]
        sim.weather = "storm"
        sim.weather_until = sim.step + effect["duration"]
        sim.pantry["net_condition"] = "torn"
        taken = _take(sim, effect["supplies"])
        for agent in sim.agents.values():
            agent.food = survival.clamp(agent.food + effect["food"])
            agent.health = survival.clamp(agent.health + effect["health"])
        sim.event(
            "Rain tears the village net and closes the jetty. The villagers head for shelter. "
            f"Each villager loses {-effect['health']} health and {-effect['food']} food. "
            f"The stores lose {_phrase(taken)}.",
            "weather",
            sim.agents,
        )
        _knock_out_if_spent(sim)
    elif kind == "festival":
        if sim.step < sim.festival_until:
            return False
        effect = EVENT_EFFECTS["festival"]
        sim.festival_until = sim.step + effect["duration"]
        fish, herbs = min(2, sim.pantry["fish"]), min(1, sim.pantry["herbs"])
        sim.pantry["fish"] -= fish
        sim.pantry["herbs"] -= herbs
        x, y = sim.world.point("guest_home")
        sim.visitor = {"id": "visitor", "name": "Wren", "place": "guest_home", "x": x, "y": y}
        for agent in sim.agents.values():
            agent.happiness = survival.clamp(agent.happiness + effect["happiness"])
            agent.happiness_bonus = effect["happiness_boost"]
            agent.happiness_bonus_until = sim.festival_until
            if not survival.collapsed(agent) and agent.health >= 30:
                agent.mood = effect["mood"]
        sim.event(
            f"Wren arrived at the guest cottage for the festival; everyone shared {fish} fish "
            f"and {herbs} herb bundles. Happiness rises, with an extra lift until the lanterns go out.",
            "festival",
            sim.agents,
        )
        for first in sim.agents.values():
            for other in sim.agents.values():
                if first.id != other.id:
                    first.relationships[other.id] = (
                        f"{first.name} and {other.name} shared Wren’s lantern festival."
                    )
    elif kind == "clear":
        effect = EVENT_EFFECTS["quiet"]
        cheerful = any(agent.happiness_bonus_until > sim.step for agent in sim.agents.values())
        sim.weather = "clear"
        sim.weather_until = sim.festival_until = 0
        sim.visitor = None
        sim.calm_until = sim.step + effect["duration"]
        for agent in sim.agents.values():
            agent.happiness_bonus_until = min(agent.happiness_bonus_until, sim.step)
            if survival.collapsed(agent):
                continue
            agent.health = survival.clamp(agent.health + effect["health"])
            if agent.health >= 30:
                agent.mood = effect["mood"]
        cheer = " The festival's extra cheer fades." if cheerful else ""
        sim.event(
            "The bay is quiet again. Everyone returns to their own rhythm. "
            f"Standing villagers rest and regain {effect['health']} health, "
            f"then {effect['health_per_step']} more each step for {effect['duration']} steps."
            + cheer,
            "weather",
            sim.agents,
        )
    elif kind == "pirate":
        if sim.step < sim.pirate_until:
            return False
        effect = EVENT_EFFECTS["pirate"]
        sim.pirate_until = sim.step + effect["duration"]
        jetty = sim.world.point("jetty")
        standing = [agent for agent in sim.agents.values() if not survival.collapsed(agent)]
        standing.sort(key=lambda agent: (
            abs(agent.x - jetty[0]) + abs(agent.y - jetty[1]), agent.id))
        victims = standing[:effect["victims"]]
        taken = _take(sim, effect["supplies"])
        if len(victims) == 1:
            knocked = f"knocks out {victims[0].name}"
        elif victims:
            knocked = "knocks out " + " and ".join(agent.name for agent in victims)
        else:
            knocked = "finds nobody standing"
        sim.event(
            f"A pirate boat comes alongside the jetty. The crew {knocked} and takes {_phrase(taken)}.",
            "pirate",
            sim.agents,
            significant=True,
        )
        for agent in victims:
            survival.knock_out(
                sim, agent, effect["duration"],
                f"{agent.name} was knocked out by the pirate crew and must recover "
                f"for {effect['duration']} steps.",
            )
    else:
        raise ValueError("Unknown event; choose storm, festival, clear, or pirate")
    return True


def expire(sim):
    """Announce an effect the moment its duration elapses."""
    if sim.pirate_until and sim.step == sim.pirate_until:
        sim.event(
            "The pirate boat slips out of the bay. The shore is quiet again.",
            "pirate",
            sim.agents,
            significant=True,
        )
    if sim.calm_until and sim.step == sim.calm_until:
        sim.event(
            "The quiet rest fades. The evening returns to its usual pace.",
            "weather",
            sim.agents,
        )


def rest(sim):
    """Apply the quiet evening's per-step health while it is still in force."""
    if not sim.calm_until or sim.step >= sim.calm_until:
        return
    gain = EVENT_EFFECTS["quiet"]["health_per_step"]
    for agent in sim.agents.values():
        if survival.collapsed(agent):
            continue
        agent.health = survival.clamp(agent.health + gain)
