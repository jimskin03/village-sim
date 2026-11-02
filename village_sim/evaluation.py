"""Seeded probes for the living season.

These are comparisons, not a single realism score. A calm bay should keep
people fed. A harsh one should show a shortage that later evidence can ease.
"""

from __future__ import annotations

from .simulation import Simulation


def run(seed=7, population=3, days=10, scarcity="calm"):
    sim = Simulation(population, seed, gameplay="living", scarcity=scarcity)
    within_caps = True
    stock_min = min(ground["stock"] for ground in sim.ecology["fish"].values())
    recoveries = 0
    for _ in range(days * 96):
        sim.tick()
        eco = sim.ecology
        for place, source in sim.water_sources.items():
            if source["supply"] > eco["water_capacity"][place] or source["supply"] < 0:
                within_caps = False
        if eco["aquifer"] > eco["aquifer_capacity"] or eco["aquifer"] < 0:
            within_caps = False
        for ground in eco["fish"].values():
            if ground["stock"] < 0 or ground["stock"] > ground["capacity"]:
                within_caps = False
            stock_min = min(stock_min, ground["stock"])
        recoveries += sum(1 for event in sim.events if event["kind"] == "recovery" and event["step"] == sim.step)
    shorts = sum(1 for supper in sim.suppers if supper["missing"]["fish"] or supper["missing"]["herbs"])
    return {
        "seed": seed,
        "population": population,
        "scarcity": scarcity,
        "day": sim.day,
        "within_caps": within_caps,
        "collapses": sum(1 for event in sim.events if event["kind"] == "collapse"),
        "recoveries": sum(1 for event in sim.events if event["kind"] == "recovery"),
        "collapsed_now": any(agent.collapsed_until for agent in sim.agents.values()),
        "stock_min": stock_min,
        "stock_end": min(ground["stock"] for ground in sim.ecology["fish"].values()),
        "aquifer_end": sim.ecology["aquifer"],
        "shorts": shorts,
        "end_health": min(agent.health for agent in sim.agents.values()),
        "rules": {name: rule["rule"] for name, rule in sim.governance["rules"].items()},
        "beliefs": {aid: len(agent.beliefs) for aid, agent in sim.agents.items()},
    }
