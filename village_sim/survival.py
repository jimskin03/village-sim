"""Always-on survival rules. All rates use one simulation step (15 minutes)."""

import math

from .decisions import Action

FOOD_DRAIN = WATER_DRAIN = 0.5
FISH_FOOD = 40
HERB_FOOD = 15
DRINK_WATER = 20
RESTART_HEALTH = 10
COLLAPSE_MINUTES = 5
COLLECT_UNITS = 6
FOOD_CENTER = "food_center"
WATER_CENTER = "water_center"
SOURCES = ("well", "rain_barrel")
DRINK_PLACES = (*SOURCES, WATER_CENTER)


def clamp(value):
    return max(0, min(100, value))


def collapsed(agent):
    # A nonzero deadline remains collapsed until tick explicitly recovers them.
    return agent.collapsed_until > 0


def initial_sources():
    return {place: {"supply": COLLECT_UNITS, "fouled_until": 0} for place in SOURCES}


def centers(sim):
    count = len(sim.agents)
    food = sim.pantry["fish"] * FISH_FOOD + sim.pantry["herbs"] * HERB_FOOD
    return {
        "food": {"place": FOOD_CENTER, "fish": sim.pantry["fish"],
                 "herbs": sim.pantry["herbs"], "level": food,
                 "daily_need": math.ceil(96 * FOOD_DRAIN * count),
                 "low": food < 96 * FOOD_DRAIN * count},
        "water": {"place": WATER_CENTER, "level": sim.pantry["water"],
                  "daily_need": math.ceil(96 * WATER_DRAIN * count / DRINK_WATER),
                  "low": sim.pantry["water"] < math.ceil(96 * WATER_DRAIN * count / DRINK_WATER)},
    }


def tick(sim):
    for place, source in sim.water_sources.items():
        source["supply"] += 3 if place == "rain_barrel" and sim.weather == "storm" else 1
    for agent in sim.agents.values():
        recovered = False
        if collapsed(agent) and sim.step >= agent.collapsed_until:
            recovered = True
            agent.collapsed_until = 0
            agent.health = RESTART_HEALTH
            sim.event(f"{agent.name} recovered from collapse and can act again.",
                      "recovery", [agent.id], significant=True)
        agent.food = clamp(agent.food - FOOD_DRAIN)
        agent.water = clamp(agent.water - WATER_DRAIN)
        if collapsed(agent) or recovered:
            pass
        elif min(agent.food, agent.water) == 0:
            agent.health = clamp(agent.health - 2)
        elif min(agent.food, agent.water) < 25:
            agent.health = clamp(agent.health - 1)
        elif min(agent.food, agent.water) >= 60 and agent.health > 0:
            agent.health = clamp(agent.health + 0.5)
        if agent.health == 0 and not collapsed(agent):
            duration = math.ceil(COLLAPSE_MINUTES / sim.world.config["minutes_per_step"])
            agent.collapsed_until = sim.step + duration
            agent.activity = "collapsed"
            agent.speech = agent.thought = agent.result = None
            sim.pending.pop(agent.id, None)
            sim.event(f"{agent.name} collapsed and must recover for {duration} step.",
                      "collapse", [agent.id], significant=True)
        if agent.health < 30:
            agent.mood = "tired"


def nearest(agent, observation, places):
    positions = {p["id"]: p for p in observation["places"]}
    return min(places, key=lambda p: (abs(positions[p]["x"] - agent.x)
                                      + abs(positions[p]["y"] - agent.y), p))


def usable_sources(observation):
    return [p for p, s in observation["water_sources"].items()
            if s["supply"] > 0 and s["fouled_until"] <= observation["step"]]


def personal_action(agent, observation):
    if collapsed(agent):
        return None
    if agent.water <= 60:
        if observation["pantry"]["water"]:
            return Action(nearest(agent, observation, DRINK_PLACES), "drink")
        sources = usable_sources(observation)
        if sources:
            return Action(nearest(agent, observation, sources), "collect water")
    if agent.food <= 55 and (observation["pantry"]["fish"] or observation["pantry"]["herbs"]):
        return Action(FOOD_CENTER, "eat")
    if agent.food <= 40:
        return Action("garden", "gathering herbs for shared food")
    return None


def suggested_action(agent, observation):
    """Shared chores use the same visible roster, distance and shortage for everyone."""
    personal = personal_action(agent, observation)
    if personal:
        return personal
    hour = observation["minute"] // 60
    if (observation["weather"] == "storm" or observation["festival"]
            or not 6 <= hour < 18 or 13 <= hour < 15):
        return None
    free = [a for a in observation["present_villagers"]
            if not a["collapsed"] and not a["busy"] and a["food"] > 55 and a["water"] > 60]
    # Work toward the shared reserve after today's initial craft goal. Critical
    # needs may interrupt that goal; nobody has an exclusive survival role.
    free = [a for a in free if a["work_completed"]]
    for resource in ("water", "food"):
        center = observation["collection_centers"][resource]
        if not center["low"] or not free:
            continue
        if resource == "water":
            places = usable_sources(observation)
            if not places:
                continue
            units = COLLECT_UNITS
            activity = "collect water"
        else:
            if observation["pantry"]["fish"] < observation["villager_count"]:
                places = ["nets"] if observation["pantry"]["net_condition"] == "torn" else ["jetty"]
                activity = "mending the torn village net" if places == ["nets"] else "fishing for shared food"
                units = FISH_FOOD
            else:
                places, activity, units = ["garden"], "gathering herbs for shared food", HERB_FOOD
        points = {p["id"]: p for p in observation["places"]}
        ranked = sorted(free, key=lambda a: (
            min(abs(a["x"] - points[p]["x"]) + abs(a["y"] - points[p]["y"]) for p in places), a["id"]))
        helpers = max(1, math.ceil((center["daily_need"] - center["level"]) / units))
        selected = ranked[:helpers]
        if any(a["id"] == agent.id for a in selected):
            return Action(nearest(agent, observation, places), activity)
        free = ranked[helpers:]
    if agent.health < 30:
        return Action(agent.home, "resting to recover health")
    return None


def validate_activity(place, activity):
    if activity == "drink" and place not in DRINK_PLACES:
        raise ValueError("Seawater is not drinkable; drink at the well, rain barrel or water collection center")
    if activity == "eat" and place not in (FOOD_CENTER, "fire"):
        raise ValueError("Eat at the food collection center or village hearth")
    if activity == "collect water" and place not in SOURCES:
        raise ValueError("Collect fresh water at the well or rain barrel")


def execute(sim, agent, place, activity):
    """Run a one-step activity only after arrival; return whether it was handled."""
    validate_activity(place, activity)
    if activity not in ("drink", "eat", "collect water"):
        return False
    if collapsed(agent):
        return True
    if activity == "collect water":
        source = sim.water_sources[place]
        amount = min(COLLECT_UNITS, source["supply"]) if source["fouled_until"] <= sim.step else 0
        if amount:
            source["supply"] -= amount
            sim.pantry["water"] += amount
            text = f"{agent.name} brought {amount} water units from the {sim.world.places[place]['name']} to the water collection center."
        else:
            text = f"{agent.name} is waiting for fresh water at the {sim.world.places[place]['name']}."
    elif activity == "drink":
        if place in SOURCES and sim.water_sources[place]["fouled_until"] > sim.step:
            return True
        if not sim.pantry["water"]:
            return True
        sim.pantry["water"] -= 1
        agent.water = clamp(agent.water + DRINK_WATER)
        text = f"{agent.name} drank fresh water from the water collection center's store."
    else:
        resource = "herbs" if sim.pantry["herbs"] else "fish"
        if not sim.pantry[resource]:
            return True
        sim.pantry[resource] -= 1
        agent.food = clamp(agent.food + (HERB_FOOD if resource == "herbs" else FISH_FOOD))
        text = f"{agent.name} ate a {'herb bundle' if resource == 'herbs' else 'fish ration'} from the food collection center."
    sim.event(text, "survival", [agent.id], place=place)
    return True
