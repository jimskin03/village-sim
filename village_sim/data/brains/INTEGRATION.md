# Original integration proposal for `village_sim`

This file is retained from issue #24 as the design source. Its implementation
steps are historical: the data loader, private retrieval, checkpoint migration,
operator import control, rules bias, and coverage are now in the repository.
See [`docs/autobiographical-memory.md`](../../../docs/autobiographical-memory.md)
for the current runtime contract. The histories themselves remain proposed
fictional recollections.

## Repository facts checked

`village_sim/simulation.py` defines eight known `PERSONAS`, an `Agent` with `memory`, `traits`, `beliefs`, `skills`, goals and `trust`, and world-owned event IDs. `village_sim/cognition/memory.py` validates runtime records as: numeric event `id`, identical `text` and `content`, integer `timestamp`, `importance` in `[0,1]`, `type` as episodic/semantic, and numeric `source_ids`. `MemoryStore.retrieve()` currently scores relevance (0.60), importance (0.25), and recentness (0.15), with recentness half-life one simulated day. `village_sim/cognition/knowledge.py` defines the seven trait axes used as `starting_traits` in this draft. `village_sim/cognition/planning.py` limits proposed actions to currently legal options.

Because the authored brain nodes have IDs such as `mara-m012` and relative ages, **these JSON files are not drop-in replacements for runtime `Agent.memory`**. A compatibility layer is required. This is intentional: converting old memories into fake Day 1 journal events would contaminate replay history and violate the episode-provenance model.

## Proposed modules / data model

Suggested repository layout:

```
village_sim/
  data/brains/{mara,tom,lin,ada,ivo,nell,ren,bea}.json
  cognition/autobiography.py  # schema validation, per-person source, retrieval
  cognition/life_policy.py    # restrained preference nudges from evidence facets
```

Add optional, persisted `Agent.life_brain_version` and `Agent.autobiography` (or refer to immutable versioned seed files by content digest with a stable fallback in saved exports). Do not overload `Agent.beliefs` as the biography store: its format is for changing current beliefs from local world perception (`observed`, `told`, `inferred`). Do not overload `Agent.memory` without a designed namespace and migration. Biographical `nodes` should be an independent private archive joined with recent runtime memories for prompt construction and natural language recall.

### Proposed retrieval

For each private planning/conversation context, retrieve:

- 1 short identity summary (~250 characters) and at most 2 currently relevant, evidence-linked facets.
- Up to 3–4 autobiographical scenes ranked by semantic / lexical cue relevance and salience. Do **not** apply a one-day simulated-recency decay to childhood memories.
- Up to 8–10 runtime episodic/semantic memories using the existing `MemoryStore.retrieve()`.
- 0–2 known, cited counterpart histories only when they were actually **told** to the agent and logged with provenance.

Keep the existing total model context budget (currently around 6,000 characters for relevant memories) by allocating a share to autobiography rather than automatically adding extra text. Prefix imported scene text with a provenance marker such as `PERSONAL RECOLLECTION (proposed canon; age ~23)` when needed to prevent confusion with observations. Importance is an input to retrieval, not a direct grant of authority over the current environment.

### Prompt and action behavior

- Planning inputs should include `identity`, `latent_goals`, relevant memory and alternative belief. Examples: Mara sees a storm and remembers a near-rescue; Lin faces an early harvest and remembers a childhood drought.
- Let the brain tilt candidate preferences, not override urgent food, water, hazard, scheduled commitment, or legality constraints.
- The engine decides catch/repair results; the brain can express intent and incorporate real results afterward.
- Do **not** let model-proposed `memory_ids` include string biography IDs unless a separate contract field (e.g. `biography_ids`) is introduced and validated. Existing `memory_ids` are numeric runtime IDs.
- A recent observed event can weaken or overturn a strong old interpretation. Keep source memory, revised interpretation, and evidence of the revision.

### Sample interaction pattern (illustrative, not implemented)

1. At day 116, the food center is short. The engine tells Mara the **observed** local stock level.
2. Recall retrieves Mara's `communal_duty` facet and her childhood memory of receiving food from a neighbor.
3. She prioritizes fishing but also considers whether she is exhausted. A current legal action is selected; the simulation resolves the catch.
4. The outcome is logged as a new numeric-ID runtime memory with provenance; the autobiographical anchor stays unchanged.
5. If repeated failures show the shore is poor, the existing learned-estimate system updates policy, even if Mara's childhood associations favor the shore.

## Initial migration design

- **New village only, opt-in:** validate all files before starting a village; create a resident-specific copy of its brain/manifest; set the version or digest; do not add fabricated events to the public journal.
- **Saved village, opt-in:** snapshot and back up the checkpoint; add only the independent autobiography namespace and its import digest. Preserve `Agent.memory`, `trust`, `beliefs`, `skills`, `estimates`, `lessons`, goals, conversations, and existing event IDs exactly.
- **Restart:** verify the version/digest; load once; never reseed or duplicate nodes.
- **Missing/malformed file:** fail closed for autobiography and run existing rules unchanged, with an operator-visible diagnostic; never destroy a playable save.
- **Import of expanded biographies:** preserve old node IDs; add new IDs with separate revision and source; never replace past remembered content without an explicit editorial migration.

## Recommended tests

1. Parse and validate every brain, 192 unique node IDs, 40 behavior facets, and no broken pathway or evidence references.
2. Test cross-character age consistency for shared scenes (Mara/Ada rescue, Tom/Nell crack, Tom/Lin gate, Bea/Lin arrival).
3. A newly created village has distinct biographical recall, while the starting supplies, pathfinding, weather and plan legality are unaffected.
4. Resident A cannot see Resident B's unshared memory; hearsay creates a new attributed runtime memory rather than copying B's memory.
5. Two agents recall the same shared incident in different, character-consistent ways.
6. 100-day replay, checkpoint, restore, and old schema migrations preserve all original runtime numeric IDs and event chronology; generated prehistory does not appear as fake live events.
7. Repeated application of a brain version is idempotent; opt-out and corrupt data preserve existing saves.
8. Brain influence cannot force work during a storm, set fish yields, alter inventory, or bypass daily budget caps.
9. Models without provider access continue to act by rules and retain a low-cost deterministic bias from a few selected facets.
10. The older memory retrieval and conversation/commitment contracts remain backward compatible.

## Narrative review before declaring canon

These are **proposed** biographies with invented ages, relationships and pre-village events. Verify them against any established lore in existing saved sessions. The baseline repo defines names, roles, homes and brief personas, but does not establish all of these personal life events as canon. Keep the draft separate until reviewed. In particular, retain ambiguity around growing trust and avoid forcing a relationship into romance merely because two characters have a warm evening conversation.
