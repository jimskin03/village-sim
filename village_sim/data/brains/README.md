# Lantern Bay — Villager Autobiographical Brains (Draft v1)

This is the authored source draft attached to issue #24, retained with its
proposed-canon status. The pack is now installed through the separate runtime
adapter documented in [`docs/autobiographical-memory.md`](../../../docs/autobiographical-memory.md).
Installing the files does not establish their histories as objective village
events or add them to existing saves without an operator import. The material
adapts the concepts of [Cyberdyne-T800-Agent's default-brain.json](https://github.com/jimskin03/Cyberdyne-T800-Agent/blob/main/src/cell/default-brain.json)
for the cast in [jimskin03/village-sim](https://github.com/jimskin03/village-sim).

## Contents

- `villager-brains.json` — consolidated manuscript for all eight resident identities.
- `mara.json`, `tom.json`, `lin.json`, `ada.json`, `ivo.json`, `nell.json`, `ren.json`, `bea.json` — one brain per villager.
- `INTEGRATION.md` — runtime compatibility, import contract, behavioral design, and test requirements.

The live visit had six villagers (Mara, Tom, Lin, Ada, Ivo, Nell). The repository supports two optional additional residents (Ren and Bea), so the draft covers all eight. Each brain currently contains 24 subjective memory nodes (192 total), usually one or more emotionally salient episodic scenes, some ordinary repeated routines, and learned beliefs. Each has five evidence-backed and revisable behavior facets, recall pathways, associative links, latent personal goals, and private social context.

This is intentionally **high-signal starting canon** rather than hundreds of generic daily-log entries. The source template's 693 nodes and 303 pathways describe *one* person's much longer imagined lifetime. Do not falsely inflate node count with repetitive or fabricated historical memories. Expand each person deliberately over later drafts if more detail is needed.

## A memory is not a fact about the whole village

The same event may be remembered differently by different people. For example, Mara remembers helping Ada in bad weather as a frightening responsibility. Ada remembers the rescue mixed with humiliation. Neither person's interpretation is imposed as an objective fact on the other. Disagreement is useful simulation material.

`certainty` describes how the person remembers a scene (`anchor`, `gist`, `fragment`), not whether a guess is verifiably true. `strength` controls salience, not correctness. Memories may be vivid and inaccurate.

## Source-data fields

The data intentionally retains recognizable source concepts: `version`, `id`, `person`, `age`, `compression`, `nodes`, `pathways`, `links`, `recallModel`, `biography`, and `behavior.facets`. It replaces Gregorian chronology and external historic facts with village-appropriate relative-age eras and local experiential cues. It adds `resident_id`, `starting_traits`, `latent_goals`, `initial_social_context`, and import boundaries.

Each node has a **stable string biography ID**; those IDs are *not* `Simulation.next_event_id` values and must not be copied directly into the existing `MemoryStore` checkpoint records.

## Important guardrails

1. Keep the world simulator authoritative for supplies, movement, weather, repairs and health.
2. Keep biographies private to their owners and do not reveal another resident's unshared memories.
3. Do not backdate authored scenes as if they were events witnessed on a recorded simulation day.
4. Seed once per agent/version only, and never overwrite existing memory, opinions or trust on restart.
5. Keep alternate explanations and allow later real experiences to revise initial beliefs.
6. Validate that memories reference existing people/places, and review the ages/fictional relationships before establishing canon.
7. Allow the AI to explain or propose actions using memories, but always check its actions against `allowed_actions`.
