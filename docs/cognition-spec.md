# Cognitive engine and hybrid mode

Delivery contract for [issue #4](https://github.com/jimskin03/village-sim/issues/4).
The first increment implements persistent memory and retrieval. The remaining
stages below specify future behavior. World execution, navigation, the viewer
and provider configuration retain their existing interfaces.

## Delivery stages

| Stage | Deliverable | Status |
| --- | --- | --- |
| 1 | Append-only episodic/semantic memory, retrieval, checkpoint migration | Implemented |
| 2 | Validated autonomous plans, event-driven replanning, cognition mode flag | Specified |
| 3 | Bounded multi-turn conversation with negotiated commitments | Specified |
| 4 | Individual reflection, evidence and evolving trust | Specified |
| 5 | Local perception and information provenance | Specified |
| 6 | Agent-organized gatherings and coordinated schedules | Specified |

Each stage must preserve rule fallback, checkpoint resume and immutable replay.
Providers gain action-selection authority only when stage 2's feature flag and
validation are implemented together.

## Implemented memory contract

`village_sim/cognition/memory.py` owns record construction, validation and ranking.
`Agent.memory` remains a JSON list; existing event fields continue to render in
the viewer. No database, model, vector library or network access is required.

| Field | Meaning |
| --- | --- |
| `id` | Stable integer from the world's monotonically increasing event sequence |
| `content`, `text` | Identical content; `text` preserves viewer compatibility |
| `timestamp` | Absolute simulated minutes, including the starting time |
| `step`, `day`, `time` | Existing occurrence coordinates |
| `type` | `episodic` for experiences, `semantic` for reflections/work summaries |
| `kind` | Existing category, including `promise`, `work`, `reflection` |
| `importance` | Finite score in [0, 1]; promises 0.95, reflections 0.85, chores 0.3 |
| `agents` | Relevant participant IDs; only recipients receive the episode |
| `source_ids` | Earlier supporting event IDs for derived summaries |
| `embedding`, `embedding_model` | Optional finite numeric vector and embedding-space identifier |

Every occurrence receives a new ID even when wording or timestamps repeat.
Reflection adds summaries alongside original episodes, including work by absent
residents. Conversation promises become memories for both participants,
independently of the latest relationship fact.

`MemoryStore(records).retrieve(query, now=..., limit=12, query_embedding=None,
embedding_model=None)` returns detached records without changing memory,
timestamps or random state. Retrieval never calls a provider.

```
score = 0.60 * relevance + 0.25 * importance + 0.15 * recency
recency = 0.5 ** (age_in_simulated_minutes / 1440)
lexical = shared_tokens / sqrt(query_token_count * memory_token_count)
```

Lexical matching folds case, removes common stop words and normalizes trailing
plural `s`. For compatible, equal-sized, nonzero vectors, relevance combines
75% nonnegative cosine similarity and 25% lexical overlap. Otherwise relevance
uses lexical overlap. Missing, mismatched, zero or invalid query vectors use the
lexical fallback. Storage rejects nonfinite vectors; embedding model IDs prevent
cross-space comparisons. Equal scores prefer newer timestamps, then higher IDs.
Empty queries rank by importance and recency.

`Simulation.observe(aid, memory_query=None)` builds a default query from current
activity, destination, unfinished goals, nearby people and relationship facts.
The authenticated observation endpoint accepts optional `?memory_query=...`, up
to 500 decoded characters, searching only that resident's memory. The query and
up to twelve `relevant_memories` appear in observations. Full memory remains
available to harnesses. The built-in model receives at most twelve relevant and
twelve recent memories, excluding vectors from both views. Callers with an
embedding service can supply vectors directly to `MemoryStore`; no embedding
service or automatic embedding calls are included in this increment.

### Persistence and limits

Checkpoint schema 2 stores enriched memories for active and absent residents.
Schema 1 migrates before validation: preserve IDs/content, derive timestamps
from world start and step length, assign deterministic importance/type, and
remove the obsolete permanent text index. Migration does not modify its input.
Legacy records have empty evidence links and no embeddings. Experiences already
deleted by an older version cannot be recovered.

Validation rejects invalid importance, timestamps, vectors, duplicate resident
memory IDs, unknown recipients and forward evidence references before replacing
the live village. Provider connections and secrets remain outside checkpoints;
restore retains the random sequence. Snapshot and recording schemas remain 1,
with additive fields. Older replay-only stories remain viewable.

Memory retention is unbounded. Retrieval ranks the complete list in memory;
snapshots and recording frames still include full histories, so long recordings
grow faster than elapsed steps. Future indexing/archival must retain IDs and
evidence. The viewer's existing recording import size limit still applies.

## Proposed hybrid execution contract

Stage 2 adds persisted `cognition_mode`, initially opt-in. Existing checkpoints
migrate to `rules`. Fresh villages may default to `hybrid` once stage 2's
acceptance gates pass.

| Mode | Decisions | Social cognition | Missing provider |
| --- | --- | --- | --- |
| `rules` | Existing deterministic schedules/goals | Existing behavior | No calls |
| `hybrid` | Model plans on meaningful triggers; routine steps use rules | Deterministic until stages 3/4 enabled | Rules continue |
| `generative` | Model plans at goal boundaries and meaningful triggers | Model conversation/reflection | Rules continue |

All modes share legality checks, A* movement, cooldowns, supplies and storm
priority. External harness and UI provider remain mutually exclusive per agent.

### Interfaces

`engine.py` coordinates the provider-independent pipeline. `planning.py` owns
plans and validation; `conversations.py`, `reflection.py`, `perception.py` and
`relationships.py` own durable social cognition. Proposed contracts:

```python
perceive(agent_id, world_view) -> Percepts
retrieve(query, now, limit=12) -> list[Memory]
needs_replan(agent_state, percepts, changes) -> ReplanReason | None
make_request(agent_state, percepts, memories, legal_actions) -> CognitiveRequest
complete(request) -> CognitiveProposal  # provider/harness, outside world lock
validate(proposal, current_world) -> AcceptedPlan | Rejection
execute(accepted_step, current_world) -> Outcome
```

`Simulation` executes outcomes; `decisions.py` remains the fallback.
`providers.py` translates cognitive requests to the configured provider's existing
completion API. It must not mutate the world or hard-code a model ID.

### Legal actions and plans

Expose several legal actions rather than one rule-selected slot. A proposal has
a goal, bounded ordered steps and supporting memory IDs:

```json
{
  "goal": "Help Tom repair his boat",
  "steps": [{
    "action": "visit", "target": "tom", "location": "shore",
    "duration_minutes": 45,
    "reason": "I remember Tom asking for help."
  }],
  "memory_ids": [42]
}
```

Initial limits: eight steps, 15–180 minutes per step in 15-minute increments,
one simulated day total, goal/reason text up to 200 characters. Reject unknown
or absent targets, unknown/unreachable places, unsupported actions, durations
and inaccessible evidence IDs. Work must match craft/location. Visits confer
no work result. Shared commitments require both parties' acceptance. The world
alone determines production and repair success.

Revalidate at adoption and before execution. A moving target, closed jetty or
changed schedule can invalidate a proposal. Shelter, sleep and accepted meetings
constrain legality. Rejection preserves rule execution and reports a bounded
reason. Free text never becomes executable commands.

### Replanning and asynchronous calls

Persist plan ID, current step, creation/completion times, evidence IDs, status
(`active`, `completed`, `invalidated`) and a monotonic cognitive revision.
Trigger replanning on goal completion, important newly learned information,
schedule/commitment changes, target departure, blocked actions or hazards.
Routine movement and unimportant repeated chores do not cause model calls.
Relevant promise and insight memories inform the goal-selection request.

Use background calls, at most one per resident in flight. Capture simulation
identity, provider generation, plan revision and observed step. Reject results
from replaced sessions/providers or superseded plans; revalidate all others
against current state. Coalesce triggers received during a request. Preserve
the existing ten-second automatic call spacing, thirty-second error backoff
and thirty-second timeout. Manual Think may bypass spacing, never single-flight
validation. Rules execute while requests are pending or failing.

Version the plan proposal separately from legacy voice slots. Rules-mode models
retain their existing contract; old `place`/`activity` replies must not silently
gain new authority.

## Subsequent social cognition

### Conversation and commitments (stage 3)

Start within the existing conversation range/cooldown when agents have a relevant
memory or social goal and are available. Persist ID, participants, topic, status
and transcript. Bound exchanges to six alternating turns, 240 characters per
turn and one call per turn. Each speaker receives their own memories and the
shared transcript, without the other's private memory. A failed call uses a
deterministic turn. Interrupt when agents leave range or face a hazard; expire
unfinished exchanges after eight simulation steps.

Turns may propose, accept, counter or decline commitments specifying participants,
place, task, start minute and duration. Require explicit acceptance of identical
validated terms by both parties. Check availability, overlap and travel time.
Update both schedules atomically and give both a promise memory. At meeting time,
attempt travel and record attendance, cancellation or a missed promise. Speech
alone cannot establish an agreement.

### Personal reflection and trust (stage 4)

Accumulate importance since the resident's last reflection. Trigger at 5.0 or
day's end with new evidence, at most once per resident per simulated hour.
Request up to three personal insights grounded in resident-accessible evidence.
Validate supporting IDs and participants, store semantic memories with links,
and retain original episodes. Preserve the shared journal summary separately.

Trust ranges from -1 to 1, with at most 0.1 adjustment per confirmed kept/missed
commitment, linked to its outcome. Models describe interpretations but cannot
rewrite biography/evidence. Later retrieval/planning receive insights and trust
so observed reliability can influence whom a resident asks for help.

### Perception and knowledge (stage 5)

Observe agents/events within Manhattan distance four, personal outcomes, accepted
messages and explicit village-wide announcements. Separate viewer omniscience
from agent context. Learn local resource condition by observation; remote
knowledge is a dated belief. Public weather does not reveal distant work/damage.

Record provenance (`observed`, `told`, `inferred`), observer/speaker, original
event ID and learning time. Conversation transmits only facts the speaker knows.
A recipient remembers hearing a report rather than acquiring a fabricated direct
experience. Keep conflicting reports. Reflection cannot invent knowledge of
unseen events.

### Gatherings and scale (stage 6)

Reuse commitments for organizers to invite residents to shared activities.
Track invitations, acceptance/decline, attendance, supplies and cancellation.
Reserve available supplies after acceptance, then spend once at execution.
Prevent duplicate organizer/time events; cap each resident to one outstanding
invitation per partner. Keep the user-triggered festival available. Spatial and
memory indexing precede larger population limits; stage 1 does not enlarge the
map or population.

## Acceptance gates

| Behavior | Required deterministic evidence |
| --- | --- |
| Recall (stage 1) | Retrieve a promise five days and hundreds of chores later, after rejoin, JSON resume and disk restart |
| Integrity (stage 1) | Repeated text has distinct IDs; midnight keeps episodes and linked summaries; invalid metadata rejects resume |
| Migration (stage 1) | v1 shape migrates active/absent residents, retaining IDs/content and subsequent RNG behavior |
| Providers (stage 1) | Mock HTTP provider receives old relevant facts in bounded context, without vectors; existing fallback tests pass |
| Planning (stage 2) | Mock model chooses a different legal action on an important event; invalid plans reject; routine travel makes no calls |
| Async (stage 2) | Delayed result spans ticks, revalidates, and cannot survive reset/disconnect/plan replacement |
| Negotiation (stage 3) | Separate mock contexts counter/accept a meeting, update both schedules and attempt attendance |
| Reflection (stage 4) | Evidence-backed insight changes a later helper choice; unsupported insight has no effect |
| Propagation (stage 5) | Remote resident knows nothing until told, with speaker provenance; direct witness learns independently |
| Gathering (stage 6) | Agent invites helpers, handles decline, schedules attendees and spends supplies once |
| Reliability (all stages) | Rule-only runs, validation, snapshots/replay, resume and provider fallback pass without live API access |

Run current checks with `python3 -m unittest discover -s tests -v`. Subsequent
stages add behavioral tests as implemented; module existence or plausible prose
alone does not meet these gates.
