"""Shared work legality, outcomes and pantry effects for residents and visitors."""

TASKS = {
    "fish": {"places": ("jetty", "shore"), "role": "fisher"},
    "garden": {"places": ("garden",), "role": "gardener"},
    "mend_net": {"places": ("nets",), "role": "fisher"},
    "repair_boat": {"places": ("shore",), "role": "boatwright"},
}


def options(sim, place, role, *, hungry=False, collapsed=False):
    if collapsed:
        return []
    if place == "garden" and hungry:
        return ["first_basil", "mint_bundle", "wilted_seedlings"]
    if sim.weather == "storm" or sim.step < sim.festival_until or not (6 <= sim.minute // 60 < 18):
        return []
    if place == "nets":
        return ["mended_mesh", "knotted_rope"]
    if place == "jetty" or (role == "fisher" and place == "shore"):
        if sim.pantry["net_condition"] == "torn":
            return ["torn_mesh"]
        return ["silver_fish", "mackerel", "empty_net", "torn_mesh"]
    if place == "garden":
        return ["first_basil", "mint_bundle", "wilted_seedlings"]
    if role == "boatwright" and place == "shore":
        return ["sealed_hull", "sound_boat", "leaking_boat"]
    return []


def perform(sim, options, requested=None):
    """Resolve a legal attempt. Callers own attribution and actor-specific state."""
    if not options:
        raise ValueError("Work is not available here now")
    # Useful work is common; failures still have material consequences.
    result = requested if requested in options else sim.rng.choice(options[:2] * 3 + options[2:])
    detail, success = {
        "silver_fish": ("caught a silver fish", True),
        "mackerel": ("landed a striped mackerel", True),
        "empty_net": ("hauled up an empty net", False),
        "torn_mesh": ("found a torn mesh in the village net", False),
        "first_basil": ("picked the first basil from a new stem", True),
        "mint_bundle": ("gathered a fragrant bundle of mint", True),
        "wilted_seedlings": ("found the herb seedlings wilted", False),
        "sealed_hull": ("sealed a leaking hull with fresh pitch", True),
        "sound_boat": ("tested a boat that floated soundly", True),
        "leaking_boat": ("found a boat that would not float", False),
        "mended_mesh": ("mended the torn mesh of the village net", True),
        "knotted_rope": ("secured the net with a new rope knot", True),
    }[result]
    if result in ("silver_fish", "mackerel"):
        sim.pantry["fish"] += 1
    elif result in ("first_basil", "mint_bundle"):
        sim.pantry["herbs"] += 1
    elif result == "torn_mesh":
        sim.pantry["net_condition"] = "torn"
    elif result in ("mended_mesh", "knotted_rope"):
        sim.pantry["net_condition"] = "sound"
    elif result == "leaking_boat":
        sim.pantry["boat_condition"] = "leaking"
    elif result in ("sealed_hull", "sound_boat"):
        sim.pantry["boat_condition"] = "sound"
    return {"result": result, "detail": detail, "success": success,
            "significant": result in ("torn_mesh", "leaking_boat", "wilted_seedlings")}
