"""Finite bay stocks for the living season.

Regen replaces a careful day's take. A heavier take comes back only in part,
so the shortage shows up on the next morning. Legacy villages never call this.
"""

from __future__ import annotations

FISH_RESULTS = {"silver_fish", "mackerel"}
HERB_RESULTS = {"first_basil", "mint_bundle"}
BOAT_FIX = {"sealed_hull", "sound_boat"}
FISH_PLACES = ("jetty", "shore")
GARDEN_PLACES = ("garden", "west_garden", "orchard")
SUSTAINABLE = {"jetty": 8, "shore": 6, "garden": 6, "west_garden": 4, "orchard": 4}


def initial(scarcity="calm"):
    return {
        "scarcity": scarcity,
        "fish": {
            "jetty": {"stock": 24, "capacity": 24},
            "shore": {"stock": 18, "capacity": 18},
        },
        "gardens": {
            "garden": {"fertility": 70, "seeds": 16},
            "west_garden": {"fertility": 60, "seeds": 12},
            "orchard": {"fertility": 64, "seeds": 12},
        },
        "water_capacity": {"well": 12, "rain_barrel": 8},
        "aquifer": 40,
        "aquifer_capacity": 40,
        "harvested": {},
        "projects": {
            "well": {"progress": 0, "target": 3},
            "boat": {"progress": 0, "target": 3},
        },
        "well_care_until": 0,
        "boat_care_until": 0,
        "spoiled": {"fish": 0, "herbs": 0},
    }


def apply_harsh(sim):
    """A scarce start. Regen can still bring a careful bay back."""
    eco = sim.ecology
    for ground in eco["fish"].values():
        ground["stock"] = max(2, ground["capacity"] // 6)
    eco["aquifer"] = 6
    for plot in eco["gardens"].values():
        plot["fertility"] = 20
        plot["seeds"] = 2
    for source in sim.water_sources.values():
        source["supply"] = 1
    sim.pantry["water"] = 1


def clamp_sources(sim):
    for place, source in sim.water_sources.items():
        source["supply"] = min(source["supply"], sim.ecology["water_capacity"][place])
    sim.ecology["aquifer"] = min(sim.ecology["aquifer"], sim.ecology["aquifer_capacity"])


def harvest_limit(sim, place):
    if sim.gameplay != "living":
        return None
    rule = sim.governance["rules"]["harvest"]
    if rule["rule"] != "limited":
        return None
    return rule["limit"] if type(rule.get("limit")) is int else 4


def replenish(sim):
    """Rain and the aquifer fill sources up to a cap. Care makes the next water come sooner."""
    eco = sim.ecology
    interval = 6 if eco["well_care_until"] > sim.step else 8
    if sim.weather == "storm":
        interval = 2
    if interval and sim.step % interval == 0:
        eco["aquifer"] = min(eco["aquifer_capacity"], eco["aquifer"] + 1)
    for place, source in sim.water_sources.items():
        cap = eco["water_capacity"][place]
        if place == "well":
            if source["supply"] < cap and eco["aquifer"] > 0:
                source["supply"] += 1
                eco["aquifer"] -= 1
        else:
            gain = 1 if sim.weather == "storm" or sim.step % 8 == 0 else 0
            source["supply"] = min(cap, source["supply"] + gain)


def care_bonus(sim) -> float:
    if sim.gameplay == "living" and sim.ecology["boat_care_until"] > sim.step:
        return 0.15
    return 0.0


def success_weights(options, *, skill, abundance, care=0.0):
    abundance = max(0.0, min(1.0, abundance + care))
    skill = max(0.0, min(1.0, skill))
    weights = []
    for name in options:
        if name in FISH_RESULTS or name in HERB_RESULTS or name in BOAT_FIX or name in ("mended_mesh", "knotted_rope"):
            weights.append((0.35 + 0.65 * abundance) * (1 + 0.5 * skill))
        else:
            weights.append(0.45 + 0.8 * (1 - abundance))
    return weights


def prepare(sim, place, options):
    """Drop yields the bay or a rule cannot pay. The caller still rolls the rest."""
    if not options:
        return options, 1.0, None
    if sim.gameplay != "living" or place is None:
        return options, 1.0, None
    eco = sim.ecology
    cause = None
    abundance = 1.0
    limit = harvest_limit(sim, place)
    taken = eco["harvested"].get(f"{sim.day}:{place}", 0)
    fishing = place in eco["fish"] and any(name in FISH_RESULTS for name in options)
    gardening = place in eco["gardens"] and any(name in HERB_RESULTS for name in options)
    if fishing:
        ground = eco["fish"][place]
        abundance = ground["stock"] / ground["capacity"] if ground["capacity"] else 0
        if (limit is not None and taken >= limit) or ground["stock"] <= 0:
            options = [name for name in options if name not in FISH_RESULTS] or ["empty_net"]
            cause = "rule" if limit is not None and taken >= limit else "inability"
    elif gardening:
        plot = eco["gardens"][place]
        abundance = plot["fertility"] / 100
        blocked = (limit is not None and taken >= limit) or plot["seeds"] <= 0 or plot["fertility"] < 12
        if blocked:
            options = ["wilted_seedlings"]
            cause = "rule" if limit is not None and taken >= limit else "inability"
    return options, abundance, cause


def _count(sim, place):
    key = f"{sim.day}:{place}"
    sim.ecology["harvested"][key] = sim.ecology["harvested"].get(key, 0) + 1


def transfer(sim, *, resource, quantity, source, destination, actor, purpose, event_id=None, witnesses=()):
    if quantity <= 0:
        return
    sim.ledger.append({
        "step": sim.step, "day": sim.day, "resource": resource, "quantity": quantity,
        "source": source, "destination": destination, "actor": actor or "village",
        "purpose": purpose, "event_id": event_id, "witnesses": list(witnesses),
    })


def commit(sim, place, result, actor, cause):
    """Take the yield from a real stock. If the stock cannot pay, the attempt fails."""
    if sim.gameplay != "living":
        return result, cause
    eco = sim.ecology
    if result in FISH_RESULTS:
        ground = eco["fish"].get(place) if place else None
        if ground is None or ground["stock"] <= 0:
            return "empty_net", cause or "inability"
        ground["stock"] -= 1
        _count(sim, place)
        return result, cause or "success"
    if result in HERB_RESULTS:
        plot = eco["gardens"].get(place) if place else None
        if plot is None or plot["seeds"] <= 0 or plot["fertility"] < 12:
            return "wilted_seedlings", cause or "inability"
        plot["seeds"] -= 1
        plot["fertility"] = max(0, plot["fertility"] - 8)
        _count(sim, place)
        return result, cause or "success"
    if result in BOAT_FIX and place == "shore":
        project = eco["projects"]["boat"]
        project["progress"] += 1
        if project["progress"] >= project["target"]:
            project["progress"] = 0
            eco["boat_care_until"] = sim.step + 96
    return result, cause or ("success" if result in BOAT_FIX or result in ("mended_mesh", "knotted_rope") else "chance")


def maintain_well(sim, agent):
    project = sim.ecology["projects"]["well"]
    project["progress"] += 1
    done = project["progress"] >= project["target"]
    if done:
        project["progress"] = 0
        sim.ecology["well_care_until"] = sim.step + 96
    text = (f"{agent.name} finished a round of care on the freshwater well."
            if done else f"{agent.name} maintained the freshwater well.")
    sim.event(text, "work", [agent.id], place="well", cause="success")
    return done


def end_day(sim, day):
    """Grow back a careful harvest. Extra take returns more slowly than it left."""
    eco = sim.ecology
    for place, ground in eco["fish"].items():
        taken = eco["harvested"].get(f"{day}:{place}", 0)
        sustainable = SUSTAINABLE[place]
        regen = taken + (1 if ground["stock"] < ground["capacity"] else 0) if taken <= sustainable else sustainable // 2
        ground["stock"] = min(ground["capacity"], ground["stock"] + regen)
    for place, plot in eco["gardens"].items():
        taken = eco["harvested"].get(f"{day}:{place}", 0)
        if taken <= SUSTAINABLE[place]:
            # Harvest already spent 8 fertility and one seed per bundle. Put that back.
            restored = 8 * taken + (4 if plot["fertility"] < 80 else 0)
            plot["fertility"] = min(100, plot["fertility"] + restored)
            plot["seeds"] = min(24, plot["seeds"] + (taken or (1 if plot["seeds"] < 16 else 0)))
        else:
            plot["fertility"] = max(0, plot["fertility"] - 8)
    if sim.pantry["fish"] > 2 * max(1, len(sim.agents)):
        sim.pantry["fish"] -= 1
        eco["spoiled"]["fish"] += 1
        transfer(sim, resource="fish", quantity=1, source="pantry", destination="spoil",
                 actor="village", purpose="spoilage")
        sim.event("A fish ration spoiled before it could be eaten.", "survival", sim.agents, significant=True)
    stale = [key for key in eco["harvested"] if int(key.split(":", 1)[0]) < day - 2]
    for key in stale:
        del eco["harvested"][key]


def validate(eco):
    fresh = initial()
    if not isinstance(eco, dict) or set(eco) != set(fresh):
        raise ValueError("Invalid ecology")
    if eco["scarcity"] not in ("calm", "harsh"):
        raise ValueError("Invalid scarcity")
    for place, ground in eco["fish"].items():
        if place not in fresh["fish"] or set(ground) != {"stock", "capacity"}:
            raise ValueError("Invalid fish grounds")
        if ground["capacity"] != fresh["fish"][place]["capacity"]:
            raise ValueError("Invalid fish grounds")
        if type(ground["stock"]) is not int or not 0 <= ground["stock"] <= ground["capacity"]:
            raise ValueError("Invalid fish grounds")
    for place, plot in eco["gardens"].items():
        if place not in fresh["gardens"] or set(plot) != {"fertility", "seeds"}:
            raise ValueError("Invalid gardens")
        if (type(plot["fertility"]) is not int or not 0 <= plot["fertility"] <= 100
                or type(plot["seeds"]) is not int or not 0 <= plot["seeds"] <= 24):
            raise ValueError("Invalid gardens")
    if eco["water_capacity"] != fresh["water_capacity"]:
        raise ValueError("Invalid water capacity")
    if (type(eco["aquifer"]) is not int or type(eco["aquifer_capacity"]) is not int
            or eco["aquifer_capacity"] != fresh["aquifer_capacity"]
            or not 0 <= eco["aquifer"] <= eco["aquifer_capacity"]):
        raise ValueError("Invalid aquifer")
    if not isinstance(eco["harvested"], dict):
        raise ValueError("Invalid harvest record")
    for key, count in eco["harvested"].items():
        day, _, place = str(key).partition(":")
        if not day.isdigit() or place not in (*FISH_PLACES, *GARDEN_PLACES) or type(count) is not int or count < 0:
            raise ValueError("Invalid harvest record")
    projects = eco["projects"]
    if set(projects) != {"well", "boat"}:
        raise ValueError("Invalid projects")
    for name, project in projects.items():
        target = fresh["projects"][name]["target"]
        if (set(project) != {"progress", "target"} or project["target"] != target
                or type(project["progress"]) is not int or not 0 <= project["progress"] < target):
            raise ValueError("Invalid projects")
    for key in ("well_care_until", "boat_care_until"):
        if type(eco[key]) is not int or eco[key] < 0:
            raise ValueError("Invalid maintenance")
    spoiled = eco["spoiled"]
    if (set(spoiled) != {"fish", "herbs"}
            or any(type(spoiled[key]) is not int or spoiled[key] < 0 for key in spoiled)):
        raise ValueError("Invalid spoilage")


def validate_ledger(rows, next_event_id):
    if not isinstance(rows, list):
        raise ValueError("Invalid resource ledger")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "step", "day", "resource", "quantity", "source", "destination",
                "actor", "purpose", "event_id", "witnesses"}:
            raise ValueError("Invalid resource ledger")
        if (type(row["step"]) is not int or row["step"] < 0
                or type(row["day"]) is not int or row["day"] < 1
                or row["resource"] not in ("fish", "herbs", "water")
                or type(row["quantity"]) is not int or row["quantity"] < 1
                or not isinstance(row["source"], str) or not isinstance(row["destination"], str)
                or not isinstance(row["actor"], str) or not isinstance(row["purpose"], str)
                or not isinstance(row["witnesses"], list)
                or any(not isinstance(item, str) for item in row["witnesses"])):
            raise ValueError("Invalid resource ledger")
        if row["event_id"] is not None and (type(row["event_id"]) is not int
                                            or row["event_id"] < 0 or row["event_id"] >= next_event_id):
            raise ValueError("Invalid resource ledger")
