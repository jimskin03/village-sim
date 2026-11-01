# Cognitive engine and hybrid mode

Delivery contract for [issue #4](https://github.com/jimskin03/village-sim/issues/4).
Phases 1 and 2 are implemented. Conversation, personal reflection, local
knowledge and coordination below remain future stages.

| Stage | Deliverable | Status |
| --- | --- | --- |
| 1 | Persistent episodic/semantic memory and retrieval | Implemented |
| 2 | Autonomous plans, replanning and cognition mode flag | Implemented |
| 3 | Multi-turn conversations and negotiated commitments | Specified |
| 4 | Individual reflection and evolving trust | Specified |
| 5 | Local perception and information provenance | Specified |
| 6 | Agent-organized gatherings and coordination | Specified |

## Memory and retrieval

`cognition/memory.py` constructs and validates append-only records. Each has a
stable world event `id`, identical `content`/`text`, absolute-minute `timestamp`,
occurrence `step`/`day`/`time`, `type` (`episodic` or `semantic`), event `kind`,
`importance` in [0, 1], participant `agents`, supporting earlier `source_ids`, and
optional `embedding`/`embedding_model`. Repeated wording receives new IDs.
Reflection adds summaries without deleting episodes. Both participants retain
conversation promises independently of their latest relationship fact.

`MemoryStore.retrieve(query, now=..., limit=12, query_embedding=None,
embedding_model=None)` returns detached records without changing saved memories
or random state. Ranking remains:

```
score = 0.60 * relevance + 0.25 * importance + 0.15 * recency
recency = 0.5 ** (age_in_simulated_minutes / 1440)
lexical = shared_tokens / sqrt(query_token_count * memory_token_count)
```

Lexical matching folds case, removes stop words and normalizes trailing plural
`s`. Compatible, equal-sized, nonzero vectors blend 75% nonnegative cosine
similarity with 25% lexical overlap. Otherwise relevance uses lexical overlap.
Equal scores prefer newer timestamps, then higher IDs. Empty queries rank by
importance and recency.

The simulation retains one incremental index per resident: token postings,
importance/recency buckets and event-ID lookup. Retrieval ranks matching records
plus the newest k records from each importance bucket using a top-k heap. This
preserves exact lexical ranking while avoiding a full-history sort. Dense lexical
matches can still require linear scoring. Vector retrieval scans compatible
stored embeddings; this is not an approximate vector index. Derived indexes are
rebuilt lazily after restore and are never checkpointed.

`Simulation.observe(aid, memory_query=None)` uses activity, destination, unfinished
goals, nearby people and relationships as its default query. Harnesses can pass
`?memory_query=...` (up to 500 decoded characters) to search their resident's
complete archive. Retrieval itself makes no network calls.

### Optional automatic embeddings

A provider connection may set `embedding_model` (also available in Connection
details). A background worker batches the query and up to 24 relevant/recent
memory texts through that provider's `/embeddings` endpoint before planning.
The per-connection LRU caches 512 texts, separates endpoint/model spaces, validates
returned vectors and persists vectors on matching full memory records. At most
25 texts of at most 6,000 characters are sent in one embedding batch. The first
call may seed only part of an old archive; older episodes gain vectors as they
surface in retrieval. Existing stored vectors can participate in later queries.

An embedding failure reports a separate public `embedding_error` and continues
planning with lexical recall. Missing embedding configuration makes no embedding
calls. Credentials stay runtime-only, and model prompts omit vectors. Embedding
spaces are identified by a hash of endpoint and model, without the key.

### Storage and transmission budgets

Checkpoints retain complete memories, journals, ledgers and plans for active and
absent residents. Snapshot schema remains 1, with additive cognitive fields.
Live snapshots expose up to 64 recent memories and the total `memory_count`,
80 journal events, seven suppers/reflections and 32 work summaries. Memory text
is capped at 1,200 characters per record and 24,000 characters per resident view;
source lists expose at most 16 IDs. Snapshot vectors are omitted as null values.
These are display views, not archive deletion.

Model context has up to twelve relevant memories (6,000 text characters total)
and twelve recent memories (3,000 total). Observation memory views are bounded
as well. Live replay retains the latest 256 frames. Saved story checkpoints
still contain the entire resumable archive; headless exports retain exactly
`steps + 1` bounded frames for the explicitly requested run.

Archive storage and atomic checkpoint serialization still grow with experience.
The population remains capped at eight. Tests exercise more than 100 simulated
days/10,000 episodes, index candidate counts, archive retention, and frame/text
budgets. Larger populations need archival storage and vector indexing first.

## Hybrid planning

`rules` is the default for new villages and migrated saves. Opt into `hybrid`
through Settings → Thinking style, `--cognition hybrid` for a new village, or
`POST /api/control` with `{"action":"cognition","mode":"hybrid"}`. The setting
is persisted and reset retains it. Saved sessions retain their own mode on launch.

Rules mode preserves the legacy `voice-v1` contract: the model fills offered
result/mood/speech/promise slots while schedules choose activity/destination.
Hybrid mode uses explicit `plan-v1` proposals. It offers craft work, rest, visits
and the current routine. Shelter, sleep and festivals constrain the legal set.
Rules continue while a plan is unavailable, a call fails or a proposal rejects.
Accepted plans continue deterministically through temporary provider absence.
`generative` mode is reserved for later stages and is not accepted yet.

### Proposal contract

Observations expose `decision_contract`, `cognitive_revision`, `replan_reason`,
`allowed_actions` and the rule `suggested_action`. A plan names an ordered list
of legal steps and supporting memory IDs:

```json
{
  "contract": "plan-v1",
  "goal": "Help Tom repair his boat",
  "steps": [{
    "action": "visit", "target": "tom", "location": "shore",
    "duration_minutes": 45,
    "reason": "I remember Tom asking for help."
  }],
  "memory_ids": [42]
}
```

External harness submissions additionally include the observed `step` and
`revision` (copied from `cognitive_revision`). Legacy voice replies never gain
movement authority, even in hybrid mode. Provider replies must explicitly use
`plan-v1`; a wrong/missing contract fails safely.

Plans have 1–8 steps, each lasting 15–180 minutes in multiples of 15, at most
one simulated day total. Goal/reason text is at most 200 characters; at most 24
memory IDs may cite the resident's own archive. A model can cite only memories
in its request. Actions/locations/targets must match current legal entries.
Work must match craft/location. Visit targets must be present; their destination
must still match the proposal. Visits do not produce craft outputs or establish
commitments on another resident's behalf.

Adoption checks reachability before changing state. Every execution step checks
current legality and reachability again. Travel uses existing A*, speed and
cooldowns. Duration counts time after arrival. Plans expire after one game day
including travel. The world determines supplies and repair success. Free text
never becomes an executable command.

### Durable state and replanning

Each resident stores `cognitive_plan`, `cognitive_revision` and `replan_reason`.
A plan includes ID/revision, created/updated step, status (`active`, `completed`,
`invalidated`), step `index`, `remaining` duration in ticks, and interruption
reason. Step completion advances the plan; final completion records an outcome.
Meaningful events invalidate the plan and increment the cognitive revision.
Triggers include promises, weather, festivals, supper, daily-goal completion,
work setbacks, schedule changes, population changes and unavailable actions.
Routine movement and unimportant chores do not request new plans.

The server retains at most one request in flight per resident. Responses must
match the simulation identity, provider connection identity, cognition mode and
cognitive revision. Revalidate legal actions against the current world before
adoption. A newer accepted plan, reset, mode change or provider replacement
prevents stale work from winning. Replan triggers coalesce while a request runs.
Automatic calls remain at least ten seconds apart, with thirty-second error
backoff and thirty-second per-request timeout. Optional embedding and planning
calls run sequentially in the background. Think bypasses spacing, not validation.

Checkpoint schema 3 adds cognition mode and per-resident plans. Schema 2 migrates
to rules with empty cognitive plans; schema 1 first receives memory metadata.
Migration preserves input, identity, memory IDs/content and random state.
Validation rejects malformed plans, invalid progress/revisions, inaccessible
sources, unsupported crafts/targets and invalid vectors before session replace.
Provider keys, connections, derived indexes and embedding caches are excluded.

`cognition/planning.py` owns legality, proposal validation and plan construction;
`Simulation` executes and records outcomes; `decisions.py` remains the fallback;
`providers.py` adapts the configured completion API. The server coordinates calls
outside the world lock. Separate conversation/reflection/perception modules and
an engine coordinator remain work for subsequent stages.

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
